"""Spawns the deepagents worker and folds its stdout into the job.

Mirror of src/worker.ts: `uv run worker/worker.py ...` as a subprocess,
consuming stdout line-by-line as it arrives — each PROGRESS: line refreshes
job["progress"] (persisted so get_task_status sees it live), the final
RESULT_JSON: line decides success/failure, and a hard timeout kills the
subprocess and marks the job failed.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from config import Config
from jobs import collect_diff, persist_job, runtime
from persistence import (
    find_last_result_line,
    parse_progress_line,
    progress_note,
    strip_result_marker,
)
import json

WORKER_SCRIPT = str(Path(__file__).resolve().parent.parent / "worker" / "worker.py")


def _build_args(cfg: Config, args: dict[str, Any]) -> list[str]:
    # Model and key env var are resolved per task (config_store profile or
    # legacy env defaults) and passed in via `args` by main.py.
    cli = [
        "run", WORKER_SCRIPT,
        "--worktree", args["worktree"],
        "--spec", args["spec"],
        "--model", args.get("model") or cfg.model,
        "--api-key-env-var", args.get("api_key_env_var") or cfg.api_key_env_var,
        "--recursion-limit", str(args["recursion_limit"]),
        "--rubric-max-iterations", str(args["rubric_max_iterations"]),
    ]
    if args.get("definition_of_done"):
        cli += ["--definition-of-done", args["definition_of_done"]]
    if args.get("test_command"):
        cli += ["--test-command", args["test_command"]]
    return cli


async def run_worker(cfg: Config, job: dict[str, Any], args: dict[str, Any], timeout_ms: int) -> None:
    """Run one delegated task to completion, mutating + persisting `job`."""
    # OAuth-based profiles (github_copilot, chatgpt) have no API key — the
    # worker must then run WITHOUT DELEGATE_API_KEY so litellm falls back to
    # its own token caches.
    env = {**os.environ}
    resolved_key = args.get("api_key") or cfg.worker_api_key
    if resolved_key:
        env["DELEGATE_API_KEY"] = resolved_key
    else:
        env.pop("DELEGATE_API_KEY", None)

    try:
        proc = await asyncio.create_subprocess_exec(
            "uv", *_build_args(cfg, args),
            # stdin MUST be detached: this server's own stdin is the MCP
            # protocol channel, and an inheriting child steals protocol bytes.
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
    except FileNotFoundError:
        job["status"] = "failed"
        job["error"] = (
            "'uv' was not found on PATH. The worker needs uv to run worker/worker.py "
            "(https://docs.astral.sh/uv/getting-started/installation/)."
        )
        persist_job(job, cfg.work_dir)
        return

    rt = runtime.setdefault(job["taskId"], {})
    rt["proc"] = proc

    result_line: str | None = None
    tail_lines: list[str] = []

    async def consume() -> None:
        nonlocal result_line
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            tail_lines.append(line)
            if len(tail_lines) > 50:
                tail_lines.pop(0)
            if line.startswith("RESULT_JSON:"):
                result_line = line
                continue
            progress = parse_progress_line(line)
            if progress:
                job["progress"] = progress_note(progress)
                persist_job(job, cfg.work_dir)
        await proc.wait()

    try:
        await asyncio.wait_for(consume(), timeout=timeout_ms / 1000)
    except asyncio.TimeoutError:
        proc.kill()
        job["status"] = "failed"
        job["error"] = f"worker timed out after {timeout_ms} ms"
        persist_job(job, cfg.work_dir)
        return
    except Exception as e:  # noqa: BLE001 - any launcher failure becomes a job failure
        proc.kill()
        job["status"] = "failed"
        job["error"] = f"{type(e).__name__}: {e}"
        persist_job(job, cfg.work_dir)
        return

    final_line = result_line or find_last_result_line("\n".join(tail_lines))
    if not final_line:
        job["status"] = "failed"
        job["error"] = "worker produced no result line; last stdout: " + "\n".join(tail_lines[-10:])
        persist_job(job, cfg.work_dir)
        return

    try:
        result = json.loads(strip_result_marker(final_line))
    except (json.JSONDecodeError, ValueError) as e:
        job["status"] = "failed"
        job["error"] = f"unparseable RESULT_JSON line: {e}"
        persist_job(job, cfg.work_dir)
        return

    job["turns"] = result.get("turns", 0)
    job["summary"] = result.get("summary")
    job["costUsd"] = result.get("cost_usd")
    job["totalTokens"] = result.get("total_tokens")

    if result.get("status") == "succeeded":
        diff = collect_diff(cfg.work_dir, job["repo"], job["worktree"], job["taskId"])
        job.update(diff)
        job["status"] = "succeeded"
    else:
        job["status"] = "failed"
        job["error"] = result.get("error") or "worker reported failure"
    persist_job(job, cfg.work_dir)
