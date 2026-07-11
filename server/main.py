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

import config_store
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
    profile: str | None = None,
) -> str:
    # Resolve model/key per task from the config store (facade profiles),
    # falling back to legacy env config. Read fresh each call so facade
    # changes apply without a server restart.
    try:
        resolved = config_store.resolve_profile(
            profile,
            {"model": cfg.model, "api_key_env_var": cfg.api_key_env_var},
        )
    except KeyError as e:
        return json.dumps({"error": str(e)})

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
        "model": resolved["model"],
        "api_key_env_var": resolved["api_key_env_var"],
        "api_key": resolved["api_key"],
    }
    # Fire-and-forget: the worker runs in the background; job state is
    # updated live by worker_launcher via persist_job on every change.
    task = asyncio.create_task(run_worker(cfg, job, args, timeout_ms or cfg.default_timeout_ms))
    runtime.setdefault(job["taskId"], {})["task"] = task

    return json.dumps(
        {
            "task_id": wt["taskId"], "status": "running",
            "branch": wt["branch"], "worktree": wt["worktree"],
            "model": resolved["model"], "model_source": resolved["source"],
        }
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


# ── Configuration facade (spec 003) ─────────────────────────────────────────
# Sovereignty rule: the supervisor must only call these tools when the user
# explicitly asked for a configuration change (enforced by the packaged skill).

from pydantic import BaseModel  # noqa: E402 - transitive dependency of mcp


class _ApiKeyInput(BaseModel):
    api_key: str


@mcp.tool(
    description=(
        "Shows the delegate's configuration state: model profiles, the default profile, "
        "per-profile auth availability (API key reachable / OAuth token cache present), and "
        "the config file location. Read-only."
    )
)
async def provider_status() -> str:
    store = config_store.load_store()
    profiles = {
        name: {**prof, "auth": config_store.auth_state(prof)}
        for name, prof in store["profiles"].items()
    }
    return json.dumps(
        {
            "config_path": str(config_store.config_path()),
            "default_profile": store["default_profile"],
            "profiles": profiles,
            "legacy_env": {
                "DELEGATE_MODEL": cfg.model,
                "DELEGATE_API_KEY_ENV_VAR": cfg.api_key_env_var,
                "DELEGATE_API_KEY_set": bool(cfg.worker_api_key),
                "active_when": "no profile is defined or requested",
            },
        }
    )


@mcp.tool(
    description=(
        "Creates or updates a named model profile in the persistent config store. Applies "
        "immediately (no Claude Code restart). Only call when the user explicitly asked to "
        "add or change a profile."
    )
)
async def set_model_profile(
    name: str,
    model: str,
    api_key_env_var: str | None = None,
    api_base: str | None = None,
) -> str:
    try:
        prof = config_store.set_profile(name, model, api_key_env_var, api_base)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    return json.dumps({"profile": name, "saved": True, **prof})


@mcp.tool(description="Removes a named model profile. Only on explicit user request.")
async def remove_model_profile(name: str) -> str:
    if not config_store.remove_profile(name):
        return json.dumps({"error": f"unknown profile {name!r}"})
    return json.dumps({"profile": name, "removed": True})


@mcp.tool(description="Sets the default model profile. Only on explicit user request.")
async def set_default_profile(name: str) -> str:
    try:
        config_store.set_default_profile(name)
    except KeyError:
        return json.dumps({"error": f"unknown profile {name!r}"})
    return json.dumps({"default_profile": name})


@mcp.tool(
    description=(
        "Stores an API key for a profile's api_key_env_var. Preferred path: call WITHOUT the "
        "'key' argument — the server then asks the user directly through an MCP elicitation "
        "dialog, so the secret never enters the model's conversation context. Passing 'key' "
        "as an argument works but the value transits the conversation; warn the user."
    )
)
async def store_api_key(profile: str, key: str | None = None) -> str:
    store = config_store.load_store()
    prof = store["profiles"].get(profile)
    if not prof:
        return json.dumps({"error": f"unknown profile {profile!r}"})
    env_var = prof.get("api_key_env_var")
    if not env_var:
        return json.dumps(
            {"error": f"profile {profile!r} has no api_key_env_var; it may be an OAuth profile"}
        )

    via = "parameter"
    if key is None:
        # Elicitation: the response returns straight to this server via the
        # client UI — the model never sees the secret.
        try:
            ctx = mcp.get_context()
            result = await ctx.elicit(
                message=(
                    f"Enter the API key to store for profile '{profile}' "
                    f"(saved to {config_store.credentials_path()} as {env_var})."
                ),
                schema=_ApiKeyInput,
            )
            if getattr(result, "action", None) != "accept":
                return json.dumps({"cancelled": True, "profile": profile})
            key = result.data.api_key
            via = "elicitation"
        except Exception as e:  # noqa: BLE001 - client may not support elicitation
            return json.dumps(
                {
                    "error": "elicitation unavailable on this client",
                    "detail": str(e)[:200],
                    "fallback": (
                        f"Set the {env_var} environment variable before launching Claude Code, "
                        f"or add {{\"{env_var}\": \"<key>\"}} to {config_store.credentials_path()}."
                    ),
                }
            )
    if not key or not isinstance(key, str):
        return json.dumps({"error": "no key provided"})

    config_store.store_credential(env_var, key)
    note = None
    if via == "parameter":
        note = "key transited the model conversation; consider rotating it and re-entering via elicitation"
    return json.dumps({"profile": profile, "stored_as": env_var, "via": via, "note": note})


@mcp.tool(
    description=(
        "Reports OAuth auth state for a profile (token cache present or not). Starting a new "
        "device flow from here is not implemented yet (roadmap v0.4); for litellm OAuth "
        "providers, the first interactive run performs the device flow and caches tokens."
    )
)
async def auth_status(profile: str) -> str:
    store = config_store.load_store()
    prof = store["profiles"].get(profile)
    if not prof:
        return json.dumps({"error": f"unknown profile {profile!r}"})
    return json.dumps({"profile": profile, **config_store.auth_state(prof)})


if __name__ == "__main__":
    mcp.run()
