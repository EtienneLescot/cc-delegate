"""In-memory job registry, git worktree lifecycle, diff collection, cleanup.

Mirror of src/jobs.ts. Jobs are plain dicts (persisted shape — see
persistence.py); runtime handles live in the separate `runtime` map keyed by
task id, so a restored-from-disk job simply has no runtime entry and cannot
be aborted — same semantics as the TypeScript server.
"""

from __future__ import annotations

import secrets
import string
import subprocess
import time
from pathlib import Path
from typing import Any

from persistence import (
    delete_persisted_job,
    find_persisted_job,
    remember_repo,
    save_job,
)

_jobs: dict[str, dict[str, Any]] = {}
runtime: dict[str, dict[str, Any]] = {}

_BASE36 = string.digits + string.ascii_lowercase


def _to_base36(n: int) -> str:
    if n == 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, 36)
        out.append(_BASE36[r])
    return "".join(reversed(out))


def new_task_id() -> str:
    ts = _to_base36(int(time.time() * 1000))
    rand = "".join(secrets.choice(_BASE36) for _ in range(6))
    return f"t_{ts}_{rand}"


def get_job(task_id: str) -> dict[str, Any] | None:
    return _jobs.get(task_id)


def put_job(job: dict[str, Any]) -> None:
    _jobs[job["taskId"]] = job


def delete_job(task_id: str) -> None:
    _jobs.pop(task_id, None)
    runtime.pop(task_id, None)


def get_job_with_fallback(task_id: str, work_dir: str) -> dict[str, Any] | None:
    """Registry first, then the persisted JSON (jobs from a previous process)."""
    return _jobs.get(task_id) or find_persisted_job(task_id, work_dir)


def persist_job(job: dict[str, Any], work_dir: str) -> None:
    """Best-effort persistence; in-memory state stays authoritative."""
    try:
        save_job(job, work_dir)
    except OSError:
        pass


def _git(repo: str, *args: str) -> subprocess.CompletedProcess[str]:
    # stdin detached: in the MCP server, inherited stdin is the protocol
    # channel and any child reading it corrupts the session.
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True,
        stdin=subprocess.DEVNULL,
    )


def create_worktree(work_dir: str, repo_path: str, base_branch: str | None = None) -> dict[str, str]:
    """Create the disposable branch + worktree that isolates worker writes."""
    repo = str(Path(repo_path).resolve())
    task_id = new_task_id()
    branch = f"delegate/{task_id}"
    wt_root = Path(repo) / work_dir / "worktrees"
    wt_root.mkdir(parents=True, exist_ok=True)
    worktree = str(wt_root / task_id)
    base = base_branch or _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    _git(repo, "worktree", "add", "-b", branch, worktree, base)
    remember_repo(repo)
    return {"taskId": task_id, "branch": branch, "worktree": worktree, "repo": repo}


def collect_diff(work_dir: str, repo: str, worktree: str, task_id: str) -> dict[str, Any]:
    """Produce the git patch + list of files the worker changed."""
    _git(worktree, "add", "-A")
    diff = _git(worktree, "diff", "--cached").stdout
    names = _git(worktree, "diff", "--cached", "--name-only").stdout
    patch_dir = Path(repo) / work_dir / "patches"
    patch_dir.mkdir(parents=True, exist_ok=True)
    patch_path = patch_dir / f"{task_id}.diff"
    patch_path.write_text(diff, encoding="utf-8")
    files_changed = [line.strip() for line in names.splitlines() if line.strip()]
    return {"patchPath": str(patch_path), "filesChanged": files_changed}


def cleanup_job(work_dir: str, job: dict[str, Any], delete_branch: bool = True) -> dict[str, Any]:
    """Remove worktree + branch + persisted file. Caller checks job is not running."""
    result = {
        "taskId": job["taskId"],
        "worktreeRemoved": False,
        "branchDeleted": False,
        "persistedRemoved": False,
    }
    try:
        _git(job["repo"], "worktree", "remove", "--force", job["worktree"])
        result["worktreeRemoved"] = True
    except subprocess.CalledProcessError:
        # Already gone or locked; branch + file cleanup still proceed.
        pass

    if delete_branch:
        try:
            _git(job["repo"], "branch", "-D", job["branch"])
            result["branchDeleted"] = True
        except subprocess.CalledProcessError:
            pass

    try:
        delete_persisted_job(job, work_dir)
        result["persistedRemoved"] = True
    except OSError:
        pass

    delete_job(job["taskId"])
    return result
