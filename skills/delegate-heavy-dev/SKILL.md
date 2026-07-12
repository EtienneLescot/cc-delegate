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

1. **Write a crisp spec, and decompose large work.** Turn the user's request into: objective,
   constraints, a clear `definition_of_done`, and the `test_command` to validate — these double as
   the worker's rubric, so precision makes it recognize completion instead of stopping early or
   over-iterating. For large work, **split it into bounded sub-tasks** (bounded modification
   converges far better than one greenfield mega-task). Sub-tasks that touch **different** files
   can run in **parallel** — fire several `run_dev_task` calls, each gets its own worktree/branch.
   Sub-tasks that touch the **same** files must be **serialized** (or they'll conflict on merge).
   Don't over-fragment: each task should be coherent, not a one-liner.
2. **Delegate (async — you stay free).** Call `run_dev_task` with `spec`, the absolute
   `repo_path`, `test_command`, `definition_of_done`, and (optionally) `recursion_limit`. It
   returns a `task_id` IMMEDIATELY; the worker runs in the background and you keep working with the
   user. Check **`preflight`** in the return: a non-zero exit is often normal (tests target code
   that doesn't exist yet), but read `output_tail` — if the *runner itself* is broken (module/file
   not found, unknown option), the gate is unpassable, so fix `test_command` and re-delegate rather
   than letting the worker fight it. Tell the user it's running.
3. **Supervise on a cadence YOU schedule — don't sit blocked.** MCP can't push into your context,
   but you don't need to block waiting: end your turn and re-check on your own schedule (schedule a
   wake-up / background wait that re-invokes you, e.g. "I'll check in ~2 min", or simply when the
   user next speaks). Between checks you're free. At each check-in:
   - Call the **cheap `get_task_status`** (tiny: `running` / `needs_input` / `done`) — poll it as
     often as you like, it costs almost nothing.
   - **Occasionally**, or when the user asks "how's it going?", call **`get_task_progress`** for the
     real audit (files written so far, recent shell commands, step, cost) and give the user a
     one-line update. A few substantive updates per task, not a running commentary.
   - On `needs_input`, **decide at your discretion**: answer with `answer_worker(task_id, answer)`
     from your own context, or relay to the user first when it's genuinely their call (naming, API
     shape, a destructive change, a trade-off only they can settle). Answer promptly — the worker is
     blocked (and times out to a conservative default after ~10 min).
   - If a run stalls or goes rogue, `cancel_task(task_id)` kills the whole process tree and salvages
     its work.
4. **Review.** When `done` (status `succeeded`), call `fetch_task_result(task_id)`. Read the
   `summary`, open the `patch_path` diff, check `files_changed` and `tests`. On `failed` /
   `timeout` / `cancelled`, check `salvaged`: if true, the patch holds the worker's uncommitted
   work — review it BEFORE re-delegating; often it's nearly complete (e.g. the run only overran its
   step budget after finishing).
5. **Decide.** Present the diff to the user. You (with the user) decide whether to merge each
   `delegate/<task_id>` branch. The worker never pushes or merges. With parallel tasks, review and
   merge each branch separately (and watch for conflicts between them).

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
- Prefer well-scoped, coherent tasks: decompose large work into a few bounded sub-tasks rather
  than one greenfield mega-task or a swarm of one-liners.
- Delegate bounded modifications of existing code; greenfield synthesis needs file-level
  skeletons in the spec or should stay with you.
- Verify the `preflight` report before trusting a run: a broken test RUNNER (vs failing
  assertions) makes the rubric unpassable and must be fixed before delegating.
- Delegation is async and you stay free. Don't block waiting — end your turn and re-check on a
  cadence you schedule. MCP cannot push into your context, so you supervise by polling: cheap
  `get_task_status` for liveness (often), `get_task_progress` for the occasional deeper audit.
- Decompose large work into bounded sub-tasks; parallelize ones that touch different files,
  serialize ones that touch the same files.
- Answer `needs_input` at your discretion — answer yourself when it's within your context, relay
  genuine user decisions to the user. The worker is blocked (token-free, but wall-clock stalls),
  so don't leave it waiting.
