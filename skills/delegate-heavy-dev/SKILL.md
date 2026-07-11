---
name: delegate-heavy-dev
description: >-
  Use when the user asks to implement a large feature, do a big refactor, or run a heavy
  batch of code changes and wants to save cost by offloading execution to a cheaper worker model.
  Triggers on phrases like "delegate this", "offload the heavy work", "run this on the worker",
  or any large multi-file implementation where the supervisor should stay on its current model for
  planning and review. Do NOT use for one-line fixes or read-only questions.
---

# Delegate heavy development to the worker model

You are the supervisor. Keep planning and review on your current (Anthropic) model. Delegate the
heavy execution to the worker exposed by the `cc-delegate` MCP server — a deepagents-powered
worker on whatever model `DELEGATE_MODEL` points to (MiniMax by default, but any litellm-routed
provider works).

## Workflow

1. **Write a crisp spec.** Turn the user's request into: objective, constraints, a clear
   `definition_of_done`, and the `test_command` to validate. These two fields double as the
   worker's rubric — the more precise they are, the more reliably the worker recognizes when
   it's actually done instead of stopping early or over-iterating.
2. **Delegate.** Call `run_dev_task` with `spec`, the absolute `repo_path`, `test_command`,
   `definition_of_done`, and (optionally) `recursion_limit` for unusually large tasks. It returns
   a `task_id`.
3. **Supervise.** Poll `get_task_status(task_id)` periodically. Report progress to the user
   (cost isn't tracked yet — `cost_usd` reads `null`). If the run fails to converge, refine the
   spec and delegate again.
4. **Review.** When status is `succeeded`, call `fetch_task_result(task_id)`. Read the `summary`,
   open the `patch_path` diff, and check `files_changed` and `tests`.
5. **Decide.** Present the diff to the user. You (with the user) decide whether to merge branch
   `delegate/<task_id>`. The worker never pushes or merges.

## Model profiles

`run_dev_task` accepts an optional `profile` name resolved against the user's configured menu
(see `provider_status`). **The user owns model selection**: only pass a non-default `profile`
when the user explicitly asked for it in this conversation ("delegate this on the cheap
profile"). Quotas, keys, and knowledge of which models work belong to the user.

## Configuration tools — explicit user request only

The `provider_status` / `set_model_profile` / `remove_model_profile` / `set_default_profile` /
`store_api_key` / `auth_status` tools change what the worker spends money on. Call them ONLY
when the user explicitly asked for a configuration change. Never reconfigure as a side effect
of a failing delegation — report the failure and let the user decide. For API keys, prefer
calling `store_api_key` WITHOUT the `key` argument: the server asks the user directly through
an elicitation dialog and the secret never enters this conversation; if you pass `key`
yourself, tell the user their key transited the chat and suggest rotating it.

## Rules

- Never set the worker's environment variables in this (supervisor) session — the worker is isolated.
- Always review the diff before proposing a merge.
- Prefer one well-scoped delegation over many tiny ones.
- Delegate bounded modifications of existing code; greenfield synthesis needs file-level
  skeletons in the spec or should stay with you.
