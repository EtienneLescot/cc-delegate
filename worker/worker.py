# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "deepagents>=0.7.0a6",
#   "langchain-litellm",
# ]
# ///
"""Runs one delegated coding task with deepagents, then prints a single
JSON result line to stdout. Invoked as a subprocess by the Node MCP server
(src/worker.ts) — this script owns the agent loop only; git worktree setup,
diff collection, and job bookkeeping stay in Node (src/jobs.ts).
"""

import argparse
import json
import os
import sys

from deepagents import RubricMiddleware, SubAgent, create_deep_agent
from deepagents.backends.local_shell import LocalShellBackend

RESULT_MARKER = "RESULT_JSON:"

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

    api_key = os.environ.get("DELEGATE_API_KEY")
    if not api_key:
        print(json.dumps({"status": "failed", "error": "DELEGATE_API_KEY missing"}))
        return 1
    os.environ[args.api_key_env_var] = api_key

    # inherit_env=True: the worker needs the host PATH to find node/npm/git/etc.
    # for the target repo's own tooling. Safe here because access is already
    # scoped to a disposable git worktree (virtual_mode=True) on its own branch.
    backend = LocalShellBackend(root_dir=args.worktree, virtual_mode=True, timeout=120, inherit_env=True)

    # _rubric_status is a PrivateStateAttr, omitted from invoke()'s return value
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

    result = {"status": "failed", "summary": None, "turns": 0, "error": None, "rubric_status": None}
    try:
        state = agent.invoke(invoke_state, config={"recursion_limit": args.recursion_limit})
        messages = state.get("messages", [])
        result["turns"] = len(messages)
        result["summary"] = messages[-1].content if messages else None
        result["rubric_status"] = rubric_evaluations[-1]["result"] if rubric_evaluations else None
        if rubric:
            result["status"] = "succeeded" if result["rubric_status"] == "satisfied" else "failed"
            if result["status"] == "failed":
                result["error"] = f"rubric not satisfied: {result['rubric_status']}"
        else:
            result["status"] = "succeeded"
    except Exception as e:  # noqa: BLE001 - surface any failure to the supervisor as a structured result
        result["error"] = f"{type(e).__name__}: {e}"

    print(RESULT_MARKER + json.dumps(result))
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
