# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp"]
# ///
"""cc-delegate MCP server, Python edition (parallel implementation of
src/mcp-server.ts — same four tools, same response shapes, same persisted-job
format). Runs over stdio via `uv run server/main.py`.

Only this module imports `mcp`; config/jobs/persistence/worker_launcher are
stdlib-only so unit tests run without any dependency install.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.fastmcp import FastMCP

from config import load_config
from jobs import (
    cleanup_job,
    create_worktree,
    get_job_with_fallback,
    persist_job,
    put_job,
    runtime,
)
from worker_launcher import run_worker

cfg = load_config()
mcp = FastMCP("cc-delegate")


@mcp.tool(
    description=(
        "Starts an autonomous coding worker on an isolated git worktree. "
        "Returns a task_id immediately; poll with get_task_status, then fetch_task_result."
    )
)
async def run_dev_task(
    spec: str,
    repo_path: str,
    test_command: str | None = None,
    definition_of_done: str | None = None,
    base_branch: str | None = None,
    recursion_limit: int | None = None,
    timeout_ms: int | None = None,
) -> str:
    wt = create_worktree(cfg.work_dir, repo_path, base_branch)
    job: dict[str, Any] = {
        **wt,
        "status": "running",
        "turns": 0,
        "costUsd": None,
        "totalTokens": None,
    }
    put_job(job)
    persist_job(job, cfg.work_dir)

    args = {
        "spec": spec,
        "worktree": wt["worktree"],
        "test_command": test_command,
        "definition_of_done": definition_of_done,
        "recursion_limit": recursion_limit or cfg.default_recursion_limit,
        "rubric_max_iterations": cfg.default_rubric_max_iterations,
    }
    # Fire-and-forget: the worker runs in the background; job state is
    # updated live by worker_launcher via persist_job on every change.
    task = asyncio.create_task(run_worker(cfg, job, args, timeout_ms or cfg.default_timeout_ms))
    runtime.setdefault(job["taskId"], {})["task"] = task

    return json.dumps(
        {"task_id": wt["taskId"], "status": "running", "branch": wt["branch"], "worktree": wt["worktree"]}
    )


@mcp.tool(description="Returns current status, progress, cost and turns.")
async def get_task_status(task_id: str) -> str:
    j = get_job_with_fallback(task_id, cfg.work_dir)
    if not j:
        return json.dumps({"error": "unknown task_id"})
    return json.dumps(
        {
            "task_id": task_id,
            "status": j.get("status"),
            "progress": j.get("progress"),
            "turns": j.get("turns", 0),
            "cost_usd": j.get("costUsd"),
            "total_tokens": j.get("totalTokens"),
            "error": j.get("error"),
        }
    )


@mcp.tool(description="Returns summary, patch, files changed, tests and cost of a completed task.")
async def fetch_task_result(task_id: str) -> str:
    j = get_job_with_fallback(task_id, cfg.work_dir)
    if not j:
        return json.dumps({"error": "unknown task_id"})
    return json.dumps(
        {
            "task_id": task_id,
            "status": j.get("status"),
            "summary": j.get("summary"),
            "patch_path": j.get("patchPath"),
            "files_changed": j.get("filesChanged", []),
            "tests": j.get("tests", {}),
            "cost_usd": j.get("costUsd"),
            "total_tokens": j.get("totalTokens"),
            "num_turns": j.get("turns", 0),
            "branch": j.get("branch"),
            "worktree": j.get("worktree"),
            "error": j.get("error"),
        }
    )


@mcp.tool(description="Removes the worktree, branch, and persisted file for a finished task.")
async def cleanup_task(task_id: str, delete_branch: bool | None = None) -> str:
    j = get_job_with_fallback(task_id, cfg.work_dir)
    if not j:
        return json.dumps({"error": "unknown task_id"})
    if j.get("status") == "running":
        return json.dumps(
            {"task_id": task_id, "error": "task is still running; abort or wait before calling cleanup_task"}
        )
    result = cleanup_job(cfg.work_dir, j, delete_branch if delete_branch is not None else True)
    return json.dumps({"task_id": task_id, "cleaned": True, **result})


if __name__ == "__main__":
    mcp.run()
