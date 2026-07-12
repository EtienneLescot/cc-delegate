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
2. **Delegate (watch mode is the default).** Call `run_dev_task` with `spec`, the absolute
   `repo_path`, `test_command`, `definition_of_done`, and (optionally) `recursion_limit`. By
   default (`watch=True`) the call BLOCKS and streams the worker's activity live into the chat —
   every shell command and progress note — at zero token cost, exactly like a Bash tool's output.
   It returns when the task finishes OR the worker asks a question. Check **`preflight`** in the
   return: a non-zero exit is often normal (tests target code that doesn't exist yet), but read
   `output_tail` — if the *runner itself* is broken (module/file not found, unknown option), the
   gate is unpassable, so fix `test_command` and re-delegate rather than letting the worker fight
   it. (Run several delegations at once instead with `watch=False` — it returns a `task_id` and
   you supervise via `get_task_status(wait_seconds=…)` or `watch_task`.)
3. **When watch mode returns on a question** (`status == "needs_input"`): the worker is blocked on
   the included `question`. **You decide, at your discretion** — answer from your own context with
   `answer_worker(task_id, answer)` when it's within your knowledge, or relay it to the user first
   when it's genuinely a product/user decision (naming, API shape, a destructive change, a
   trade-off only they can settle). After answering, call **`watch_task(task_id)`** to resume the
   live stream. Repeat until the task reaches a terminal state.
4. **Review.** When the watch returns terminal `succeeded`, the payload already carries
   `patch_path`, `files_changed`, `cost_usd`, and `summary` (or call `fetch_task_result`). Open
   the diff and read it. On `failed` / `timeout` / `cancelled`, check `salvaged`: if true, the
   patch contains the worker's uncommitted work — review it BEFORE re-delegating; often it's
   nearly complete (e.g. the run only overran its step budget after finishing). To stop a stalled
   or runaway worker, `cancel_task(task_id)` kills the whole process tree and salvages its work.
5. **Decide.** Present the diff to the user. You (with the user) decide whether to merge branch
   `delegate/<task_id>`. The worker never pushes or merges.

## Recovery playbook (failed or stalled runs)

1. `cancel_task` if still running; then `fetch_task_result` and check `salvaged`.
2. Preserve anything useful: the salvage WIP commit already sits on `delegate/<task_id>`;
   branch off it (e.g. `worker-base`) before `cleanup_task`.
3. Diagnose before re-delegating — especially test failures: decide per failure whether the
   CODE or the TEST is wrong. A naive "make the tests pass" instruction makes the worker
   corrupt correct code to satisfy bad assertions.
4. Re-delegate BOUNDED: `base_branch` = your preserved branch, and a spec listing each issue
   with your verdict (code bug vs test bug) and the exact expected resolution. Bounded
   re-delegation converges far more reliably than a fresh greenfield attempt.

## Model profiles

`run_dev_task` accepts an optional `profile` name resolved against the user's configured menu
(see `provider_status`). **The user owns model selection**: only pass a non-default `profile`
when the user explicitly asked for it in this conversation ("delegate this on the cheap
profile"). Quotas, keys, and knowledge of which models work belong to the user.

## Configuration tools — explicit user request only

The `provider_status` / `set_model_profile` / `remove_model_profile` / `set_default_profile` /
`store_api_key` / `auth_status` / `setup_provider_auth` / `auth_poll` tools change what the
worker spends money on. Call them ONLY when the user explicitly asked for a configuration
change. Never reconfigure as a side effect of a failing delegation — report the failure and
let the user decide. For API keys, prefer calling `store_api_key` WITHOUT the `key` argument:
the server asks the user directly through an elicitation dialog and the secret never enters
this conversation; if you pass `key` yourself, tell the user their key transited the chat and
suggest rotating it. For subscription providers (GitHub Copilot), `setup_provider_auth`
returns a verification URL and user code — relay both to the user verbatim, then poll
`auth_poll(flow_id)` until it reads `authorized`.

## Rules

- Never set the worker's environment variables in this (supervisor) session — the worker is isolated.
- Always review the diff before proposing a merge.
- Prefer one well-scoped delegation over many tiny ones.
- Delegate bounded modifications of existing code; greenfield synthesis needs file-level
  skeletons in the spec or should stay with you.
- Verify the `preflight` report before trusting a run: a broken test RUNNER (vs failing
  assertions) makes the rubric unpassable and must be fixed before delegating.
- Prefer watch mode (the default): it streams the run into the chat token-free and returns you
  control exactly at questions and completion. Use `watch=False` + `get_task_status(wait_seconds=…)`
  only when running several delegations in parallel.
- Answer `needs_input` at your discretion — answer yourself when it's within your context, relay
  genuine user decisions to the user. The worker is blocked (token-free, but wall-clock stalls),
  so don't leave it waiting.
