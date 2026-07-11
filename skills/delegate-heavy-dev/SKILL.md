---
name: delegate-heavy-dev
description: >-
  Use when the user asks to implement a large feature, do a big refactor, or run a heavy
  batch of code changes and wants to save cost by offloading execution to the MiniMax worker.
  Triggers on phrases like "delegate this", "run this on MiniMax", "offload the heavy work",
  or any large multi-file implementation where the supervisor should stay on Opus for planning
  and review. Do NOT use for one-line fixes or read-only questions.
---

# Delegate heavy development to the MiniMax worker

You are the supervisor. Keep planning and review on your current (Anthropic) model. Delegate the
heavy execution to the MiniMax worker exposed by the `minimax-delegate` MCP server.

## Workflow

1. **Write a crisp spec.** Turn the user's request into: objective, constraints, a clear
   `definition_of_done`, and the `test_command` to validate.
2. **Delegate.** Call `run_dev_task` with `spec`, the absolute `repo_path`, `test_command`,
   `definition_of_done`, and (optionally) `max_budget_usd`. It returns a `task_id`.
3. **Supervise.** Poll `get_task_status(task_id)` periodically. Report progress and cost to the user.
   If the run fails on budget/turns, refine the spec and delegate again.
4. **Review.** When status is `succeeded`, call `fetch_task_result(task_id)`. Read the `summary`,
   open the `patch_path` diff, and check `files_changed` and `tests`.
5. **Decide.** Present the diff to the user. You (with the user) decide whether to merge branch
   `mm/<task_id>`. The worker never pushes or merges.

## Rules

- Never set MiniMax environment variables in this (supervisor) session — the worker is isolated.
- Always review the diff before proposing a merge.
- Prefer one well-scoped delegation over many tiny ones.
