"""Spawns the deepagents worker and folds its stdout into the job.

`uv run worker/worker.py ...` as a subprocess, consuming stdout line-by-line
as it arrives:

- ``PROGRESS:`` lines refresh job["progress"] + lastActivityTs (persisted so
  get_task_status sees them live) and feed the event bus (SSE dashboard);
- ``QUESTION:`` lines flip the job to ``needs_input`` — the worker is then
  blocked waiting for answer_worker to drop a file in the comm dir;
- the final ``RESULT_JSON:`` line decides success/failure;
- a hard timeout (and cancel_task) kills the whole process TREE — killing
  only the direct child leaves grandchildren holding the stdout pipe, which
  is how a stuck command once froze a delegation for 20+ minutes;
- any non-succeeded ending triggers a salvage pass so completed-but-
  uncommitted work still reaches fetch_task_result.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from config import Config
from events import publish
from jobs import collect_diff, persist_job, runtime, salvage_worktree
from statusline_render import write_statusline
from persistence import (
    find_last_result_line,
    parse_progress_line,
    parse_question_line,
    progress_note,
    strip_result_marker,
)
from proc_utils import kill_tree

WORKER_SCRIPT = str(Path(__file__).resolve().parent.parent / "worker" / "worker.py")


def comm_dir_for(job: dict[str, Any], work_dir: str) -> Path:
    return Path(job["repo"]) / work_dir / "comm" / job["taskId"]


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
        "--command-timeout", str(cfg.command_timeout_s),
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

    # Mailbox for supervisor answers to worker questions (ask_supervisor /
    # report_blocker). The worker polls files here while blocked.
    comm_dir = comm_dir_for(job, cfg.work_dir)
    comm_dir.mkdir(parents=True, exist_ok=True)
    env["DELEGATE_COMM_DIR"] = str(comm_dir)

    def _publish(event: dict[str, Any]) -> None:
        publish(job["repo"], job["taskId"], event, cfg.work_dir)

    def _touch(note: str | None = None) -> None:
        job["lastActivityTs"] = time.time()
        if note:
            job["progress"] = note
        persist_job(job, cfg.work_dir)
        write_statusline(job)  # refresh the token-free status line

    try:
        proc = await asyncio.create_subprocess_exec(
            "uv", *_build_args(cfg, args),
            # stdin MUST be detached: this server's own stdin is the MCP
            # protocol channel, and an inheriting child steals protocol bytes.
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            start_new_session=(os.name != "nt"),
        )
    except FileNotFoundError:
        job["status"] = "failed"
        job["error"] = (
            "'uv' was not found on PATH. The worker needs uv to run worker/worker.py "
            "(https://docs.astral.sh/uv/getting-started/installation/)."
        )
        persist_job(job, cfg.work_dir)
        _publish({"kind": "failed", "error": job["error"]})
        return

    rt = runtime.setdefault(job["taskId"], {})
    rt["proc"] = proc
    job["workerPid"] = proc.pid
    job["startedAt"] = time.time()
    _touch("worker starting")
    _publish({"kind": "started", "model": args.get("model") or cfg.model, "pid": proc.pid})

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
            question = parse_question_line(line)
            if question:
                job["status"] = "needs_input"
                job["question"] = {**question, "askedAt": time.time()}
                _touch(f"worker asks: {question['message'][:120]}")
                _publish({"kind": question.get("kind", "question"), **question})
                continue
            progress = parse_progress_line(line)
            if progress:
                if progress.get("step"):
                    job["lastStep"] = progress["step"]
                _touch(progress_note(progress))
                _publish({"kind": progress.get("kind", "progress"), **progress})
        await proc.wait()

    def _finalize_failure(error: str, kind: str = "failed") -> None:
        job["status"] = "cancelled" if rt.get("cancelled") else "failed"
        job["error"] = "cancelled by supervisor" if rt.get("cancelled") else error
        job.pop("question", None)
        if salvage_worktree(cfg.work_dir, job):
            job["salvaged"] = True
        persist_job(job, cfg.work_dir)
        write_statusline(job)
        _publish({
            "kind": "cancelled" if rt.get("cancelled") else kind,
            "error": job["error"], "salvaged": job.get("salvaged", False),
        })

    try:
        await asyncio.wait_for(consume(), timeout=timeout_ms / 1000)
    except asyncio.TimeoutError:
        kill_tree(proc.pid)
        _finalize_failure(f"worker timed out after {timeout_ms} ms", kind="timeout")
        return
    except Exception as e:  # noqa: BLE001 - any launcher failure becomes a job failure
        kill_tree(proc.pid)
        _finalize_failure(f"{type(e).__name__}: {e}")
        return

    final_line = result_line or find_last_result_line("\n".join(tail_lines))
    if not final_line:
        _finalize_failure(
            "worker produced no result line; last stdout: " + "\n".join(tail_lines[-10:])
        )
        return

    try:
        result = json.loads(strip_result_marker(final_line))
    except (json.JSONDecodeError, ValueError) as e:
        _finalize_failure(f"unparseable RESULT_JSON line: {e}")
        return

    job["turns"] = result.get("turns", 0)
    job["summary"] = result.get("summary")
    job["costUsd"] = result.get("cost_usd")
    job["totalTokens"] = result.get("total_tokens")
    job.pop("question", None)

    if result.get("status") == "succeeded" and not rt.get("cancelled"):
        diff = collect_diff(cfg.work_dir, job["repo"], job["worktree"], job["taskId"])
        job.update(diff)
        job["status"] = "succeeded"
        persist_job(job, cfg.work_dir)
        write_statusline(job)
        _publish({
            "kind": "succeeded",
            "files_changed": len(job.get("filesChanged", [])),
            "cost_usd": job.get("costUsd"),
        })
    else:
        _finalize_failure(result.get("error") or "worker reported failure")
