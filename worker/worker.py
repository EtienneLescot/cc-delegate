# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "deepagents>=0.7.0a6",
#   "langchain-litellm",
#   "fastapi",
# ]
# ///
"""Runs one delegated coding task with deepagents, then prints a single
JSON result line to stdout. Invoked as a subprocess by the MCP server
(server/worker_launcher.py) — this script owns the agent loop only; git
worktree setup, diff collection, and job bookkeeping stay in the server.

During the run the worker also emits one ``PROGRESS:`` line per graph step
to stdout so the MCP server can refresh its ``get_task_status`` view live.
"""

import argparse
import json
import os
import sys

import litellm

from deepagents import RubricMiddleware, SubAgent, create_deep_agent
from deepagents.backends.local_shell import LocalShellBackend

RESULT_MARKER = "RESULT_JSON:"
PROGRESS_MARKER = "PROGRESS:"

# Substrings (case-insensitive) that mark an env var as a secret. Matched
# against the uppercased name with ``in`` — so ``MY_API_KEY``,
# ``GITHUB_TOKEN``, ``DB_PASSWORD`` are all filtered. ``is_sensitive_env_name``
# is a pure function so it stays unit-testable without touching ``os.environ``.
_SENSITIVE_ENV_SUBSTRINGS: tuple[str, ...] = (
    "API_KEY",
    "APIKEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
)


def is_sensitive_env_name(name: str) -> bool:
    """Return True if ``name`` looks like a secret-bearing environment variable.

    The check is intentionally broad: any substring hit (case-insensitive) on
    API_KEY / APIKEY / TOKEN / SECRET / PASSWORD / CREDENTIAL qualifies. We
    err on the side of dropping more variables rather than risking a leak
    through a shell command the agent runs. PATH / HOME / SystemRoot / TEMP
    are unaffected.
    """
    upper = name.upper()
    return any(token in upper for token in _SENSITIVE_ENV_SUBSTRINGS)


def build_shell_env(api_key_env_var: str) -> dict[str, str]:
    """Return a copy of ``os.environ`` safe to hand to ``LocalShellBackend``.

    Drops anything that matches ``is_sensitive_env_name``, plus the well-known
    secret names ``DELEGATE_API_KEY`` and the provider-specific key env var
    named by ``api_key_env_var``. PATH, HOME, SystemRoot, TEMP, and similar
    survive so that ``git``, ``node``, ``npm``, etc. keep working inside the
    worktree.

    IMPORTANT: this only filters the dict we hand to the shell backend — the
    Python process's own ``os.environ`` keeps the provider key, because
    litellm reads it from there.
    """
    drop_names = {"DELEGATE_API_KEY", api_key_env_var}
    return {
        k: v
        for k, v in os.environ.items()
        if k not in drop_names and not is_sensitive_env_name(k)
    }


class CostTracker:
    """litellm success callback that accumulates cost + tokens across every
    model call in the run (main agent, subagents, rubric grader).

    Per-call failures degrade silently: a model without a known price simply
    contributes nothing to ``cost_usd`` but does not crash the agent loop.
    """

    def __init__(self) -> None:
        self.cost_usd: float = 0.0
        self.total_tokens: int = 0
        self._priced_calls: int = 0

    def __call__(self, kwargs, completion_response, start_time, end_time) -> None:  # noqa: ARG002
        # Per-call cost lookup — wrapped because unknown model pricing raises
        # in litellm; a metering failure must never crash the agent.
        try:
            cost = litellm.completion_cost(completion_response=completion_response)
            if cost is not None and isinstance(cost, (int, float)) and float(cost) >= 0:
                self.cost_usd += float(cost)
                self._priced_calls += 1
        except Exception:
            # Unknown model / missing pricing entry — skip silently.
            pass

        # Token accumulation — try the attribute, then the dict, then give up.
        try:
            usage = getattr(completion_response, "usage", None)
            if usage is None and isinstance(completion_response, dict):
                usage = completion_response.get("usage")
            total: object | None = None
            if usage is not None:
                if hasattr(usage, "total_tokens"):
                    total = usage.total_tokens
                elif isinstance(usage, dict):
                    total = usage.get("total_tokens")
            if isinstance(total, (int, float)):
                self.total_tokens += int(total)
        except Exception:
            pass

    def final_cost_usd(self) -> float | None:
        """Return the accumulated cost, or ``None`` if no call could be priced."""
        if self._priced_calls == 0:
            return None
        return self.cost_usd


SUBAGENTS: list[SubAgent] = [
    {
        "name": "implementer",
        "description": "Writes and edits source code to satisfy the spec. Use for implementation work.",
        "system_prompt": (
            "You implement the requested change with minimal, focused edits. "
            "Follow the repository's conventions (see CLAUDE.md if present). "
            "Never touch files outside the working directory. Do not run git push, merge, or destructive commands."
        ),
    },
    {
        "name": "tester",
        "description": "Writes and runs tests, and reports failures. Use to validate the implementation.",
        "system_prompt": (
            "You write and run tests only. Do not modify non-test source files. "
            "Run the provided test command, summarize pass/fail, and return the failing output tail."
        ),
    },
    {
        "name": "reviewer",
        "description": "Read-only reviewer that returns a prioritized list of issues.",
        "system_prompt": (
            "You review changes for correctness, security, and adherence to the spec. "
            "Return a prioritized, actionable list. You never edit files."
        ),
        "tools": [],
    },
]

SYSTEM_PROMPT = (
    "You are an autonomous coding worker delegated by a supervisor. "
    "Work only inside the current working directory. "
    "Never run git push, merge, rebase onto other branches, or destructive commands. "
    "Use the implementer/tester/reviewer subagents when helpful. "
    "Deliver a complete, tested change, then stop — the supervisor reviews and merges."
)


def build_prompt(spec: str, definition_of_done: str | None, test_command: str | None) -> str:
    parts = ["# Task", spec]
    if definition_of_done:
        parts.append(f"\n# Definition of done\n{definition_of_done}")
    if test_command:
        parts.append(f"\n# Test command\nRun `{test_command}` and iterate until it passes.")
    return "\n".join(parts)


def _message_content(message) -> object | None:
    """Best-effort extraction of a message's textual content."""
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return content


def _short_note_from_message(message) -> str | None:
    """Return a short human-readable snippet (≤200 chars) from ``message``.

    Returns ``None`` if the message carries no usable text — caller should
    then omit the ``note`` field from the progress payload.
    """
    content = _message_content(message)
    if isinstance(content, str) and content:
        return content[:200]
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        text = " ".join(parts).strip()
        return text[:200] if text else None
    return None


def _last_message_content(messages: list) -> object | None:
    if not messages:
        return None
    return _message_content(messages[-1])


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--worktree", required=True)
    p.add_argument("--spec", required=True)
    p.add_argument("--definition-of-done", default=None)
    p.add_argument("--test-command", default=None)
    p.add_argument("--model", default="litellm:minimax/MiniMax-M3")
    p.add_argument("--api-key-env-var", default="MINIMAX_API_KEY",
                    help="Provider-specific env var litellm expects for --model's provider prefix.")
    p.add_argument("--recursion-limit", type=int, default=400)
    p.add_argument("--rubric-max-iterations", type=int, default=6)
    args = p.parse_args()

    # DELEGATE_API_KEY is optional: OAuth-based providers (github_copilot,
    # chatgpt) authenticate from litellm's local token caches instead of a key.
    api_key = os.environ.get("DELEGATE_API_KEY")
    if api_key:
        os.environ[args.api_key_env_var] = api_key

    # Filter the env before handing it to LocalShellBackend so the agent can't
    # echo host secrets (DELEGATE_API_KEY, the provider key, GITHUB_TOKEN, ...)
    # back through a shell command. PATH / HOME / SystemRoot / TEMP survive so
    # node/npm/git still work. The Python process's own os.environ keeps the
    # provider key — litellm reads it from there.
    backend = LocalShellBackend(
        root_dir=args.worktree,
        virtual_mode=True,
        timeout=120,
        inherit_env=False,
        env=build_shell_env(args.api_key_env_var),
    )

    # Register a litellm success callback to meter cost + tokens across every
    # model call in the run (main agent, subagents, rubric grader). Registered
    # BEFORE create_deep_agent so the first model call is metered too.
    tracker = CostTracker()
    litellm.success_callback = [tracker]

    # _rubric_status is a PrivateStateAttr, omitted from stream()'s final state
    # by design; on_evaluation is the documented way to observe the grader's
    # verdict without a checkpointer.
    rubric_evaluations: list[dict] = []
    agent = create_deep_agent(
        model=args.model,
        backend=backend,
        system_prompt=SYSTEM_PROMPT,
        subagents=SUBAGENTS,
        middleware=[RubricMiddleware(
            model=args.model,
            max_iterations=args.rubric_max_iterations,
            on_evaluation=rubric_evaluations.append,
        )],
    )

    prompt = build_prompt(args.spec, args.definition_of_done, args.test_command)
    rubric = args.definition_of_done or (f"Running `{args.test_command}` succeeds." if args.test_command else None)

    invoke_state = {"messages": [{"role": "user", "content": prompt}]}
    if rubric:
        invoke_state["rubric"] = rubric

    result = {
        "status": "failed",
        "summary": None,
        "turns": 0,
        "error": None,
        "rubric_status": None,
        "cost_usd": None,
        "total_tokens": None,
    }
    try:
        # Live progress: stream updates one node at a time, print a flushed
        # PROGRESS line per step, and accumulate the longest messages list we
        # see so we can reconstruct the final state for RESULT_JSON. The
        # "last relevant update" for the message history is whichever agent
        # node emitted the full message list — we keep the longest one seen.
        step_counter = 0
        accumulated_messages: list = []
        for update in agent.stream(
            invoke_state,
            config={"recursion_limit": args.recursion_limit},
            stream_mode="updates",
        ):
            for node_name, node_state in update.items():
                step_counter += 1
                messages_delta: list | None = None
                if isinstance(node_state, dict):
                    delta = node_state.get("messages")
                    if isinstance(delta, list):
                        messages_delta = delta
                if messages_delta is not None and len(messages_delta) > len(accumulated_messages):
                    accumulated_messages = messages_delta

                note = None
                source_for_note = messages_delta if messages_delta else accumulated_messages
                if source_for_note:
                    note = _short_note_from_message(source_for_note[-1])
                payload: dict[str, object] = {"step": step_counter, "node": node_name}
                if note:
                    payload["note"] = note
                print(PROGRESS_MARKER + json.dumps(payload), flush=True)

        messages = accumulated_messages
        result["turns"] = len(messages)
        result["summary"] = _last_message_content(messages)
        result["rubric_status"] = rubric_evaluations[-1]["result"] if rubric_evaluations else None
        if rubric:
            result["status"] = "succeeded" if result["rubric_status"] == "satisfied" else "failed"
            if result["status"] == "failed":
                result["error"] = f"rubric not satisfied: {result['rubric_status']}"
        else:
            result["status"] = "succeeded"
    except Exception as e:  # noqa: BLE001 - surface any failure to the supervisor as a structured result
        result["error"] = f"{type(e).__name__}: {e}"

    # Metering is recorded regardless of success/failure so the supervisor
    # can still report partial spend on crashed runs.
    result["cost_usd"] = tracker.final_cost_usd()
    result["total_tokens"] = tracker.total_tokens if tracker.total_tokens > 0 else None

    print(RESULT_MARKER + json.dumps(result))
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())