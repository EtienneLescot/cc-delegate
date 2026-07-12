# Changelog

All notable changes to this project are documented here. Versions follow
[Semantic Versioning](https://semver.org/).

## 0.4.0

Supervision, communication & resilience. Every change is a direct fix for a failure mode
observed in the first full real-world delegation session (a Pong game built on MiniMax M3):
a stalled worker nobody could stop, a whole-drive `find /` that froze the run for 20+ minutes,
a completed-but-uncommitted result lost to a recursion overrun, a broken `test_command` that
sent the worker chasing phantom failures, and a supervisor reduced to blind timer polling.

### Added

- **Worker → supervisor communication.** Three tools are injected into the worker's agent
  loop: `report_progress(update)` (fire-and-forget status lines), `ask_supervisor(question,
  context)` and `report_blocker(problem, attempts)` (both BLOCK the worker — zero tokens
  spent — and flip the task to status `needs_input`). The supervisor answers with the new
  **`answer_worker(task_id, answer)`** MCP tool; delivery is an atomic file drop in
  `<repo>/.cc-delegate/comm/<task_id>/`, never through the model conversation. Unanswered
  questions time out (`DELEGATE_ASK_TIMEOUT_S`, default 600s) into "proceed conservatively".
- **Live SSE dashboard.** The MCP server serves `http://127.0.0.1:45673` (fallback: ephemeral
  port; URL echoed in `run_dev_task`/`get_task_status` responses and
  `~/.cc-delegate/dashboard.json`): a one-page live feed of every task — each shell command,
  progress report, question, answer, and verdict, streamed over Server-Sent Events from the
  new per-task event log. Monitoring moves to the user's browser at **zero supervisor token
  cost**. `DELEGATE_DASHBOARD=0` disables; `DELEGATE_DASHBOARD_PORT` pins the port.
- **`cancel_task(task_id)`.** Kills the worker's WHOLE process tree (`taskkill /T` / process
  groups — killing just the child leaves grandchildren holding the stdout pipe), salvages
  uncommitted work, marks the task `cancelled`, and unblocks `cleanup_task`. Works on jobs
  from a previous server session via the persisted worker PID.
- **Salvage snapshots.** Any non-succeeded ending (failed, timeout, cancelled) now stages the
  worktree, writes the patch file, and best-effort WIP-commits on the delegate branch.
  `fetch_task_result` reports `salvaged: true` and returns the patch — a run that finished the
  work but died before committing (the exact Pong incident) is no longer a total loss.
- **`test_command` preflight.** `run_dev_task` executes the test command once in the fresh
  worktree and returns `preflight` (+ advisory `preflight_note`). A broken test RUNNER — the
  gate that can never pass — is caught before a single worker token is spent.
- **Long-poll `get_task_status(task_id, wait_seconds=...)`.** Returns EARLY on any change
  (progress, completion, question). Status now also carries `elapsed_s`,
  `last_activity_age_s`, `question` + `action_required`, `salvaged`, and `dashboard_url`.
- **Per-task event log** (`.cc-delegate/logs/<task_id>.jsonl`): append-only trace of every
  lifecycle event, shell command, progress note, question and answer — feeds the dashboard
  live and post-mortems after the fact.

### Fixed

- **Per-command timeout now actually terminates stuck commands.** The stock deepagents
  backend uses `subprocess.run(shell=True, timeout=...)`; on Windows, CPython's timeout path
  kills only the direct shell and then re-reads the pipes without a timeout — a surviving
  grandchild (e.g. `bash → find`) holds stdout open and hangs the run indefinitely. The
  worker's shell backend now runs commands itself and kills the entire process tree on
  expiry (`DELEGATE_CMD_TIMEOUT_S`, default 120s), returning partial output to the model.
  The same tree-kill applies to the task-level timeout in the launcher.
- **Whole-drive scans refused.** `find /` (or `find C:/`) is rejected with guidance before it
  runs, and the worker's system prompt forbids leaving the working directory — the exact
  pathology that froze the first Pong delegation.
- **Shell activity is now visible.** Each command start is announced as a `PROGRESS:` line
  (`$ <command>`), so `get_task_status` and the dashboard show live activity during long
  tool calls instead of a frozen step marker.
- **Virtual-path misplacement mitigated.** deepagents' `virtual_mode` remaps absolute paths
  under the worktree root (`/c/Users/...` → `<worktree>/c/Users/...`), silently misplacing
  files; the worker's system prompt now mandates relative paths.

### Notes

- The supervisor skill now teaches: long-polls over timers, `needs_input` handling,
  preflight verification, salvage review before re-delegation, and a recovery playbook
  (preserve → diagnose code-bug-vs-test-bug → re-delegate bounded).

## 0.3.4

### Fixed

- **Windows shell friction.** deepagents' `LocalShellBackend` runs commands via
  `subprocess.run(shell=True)`, i.e. cmd.exe on Windows — so the model's Unix-style commands
  (`ls`, `find`, `cat`, `&&`, forward-slash paths) failed, and the worker burned turns fighting
  the shell. Added `BashShellBackend`, which on Windows routes each command through bash (found
  via `PATH`/Git/hermes) by writing it to a temp script — sidestepping cmd.exe and all quoting
  conflicts. Measured on a trivial task: 71 → 44 steps, ~10 shell errors → 1, ~19% lower cost.
  No change on non-Windows (default shell is already sh-compatible).

## 0.3.3

### Fixed

- **Worker crashed with `ModuleNotFoundError: No module named 'fastapi'`** at
  `RubricMiddleware.before_agent`, blocking every delegation. deepagents 0.7.0a6's rubric
  grader imports `fastapi` lazily but doesn't declare it; added `fastapi` to
  `worker/worker.py`'s PEP-723 dependencies. Verified: a real MiniMax delegation runs to
  `succeeded` again.

## 0.3.2

OAuth device flow (GitHub Copilot) — subscription auth without an API key.

### Added

- **`setup_provider_auth(profile)`** and **`auth_poll(flow_id)`** MCP tools. For a profile on
  an OAuth provider (GitHub Copilot today), `setup_provider_auth` starts litellm's device flow
  and returns the verification URL + user code for the supervisor to show you, plus an opaque
  `flow_id`; you authorize in a browser and `auth_poll` reports `pending`/`authorized`/`failed`.
- **`server/oauth.py`**: a headless device-flow manager. It drives litellm's
  `github_copilot` `Authenticator` directly (`_get_device_code` then a background-threaded
  `_poll_for_access_token`) instead of its stdout-printing `_login`, so the URL/code are
  relayed rather than printed. The device code and access tokens never leave the module —
  tool responses carry only the verification URL, user code, and flow id. litellm is imported
  lazily, so the module and its tests load without litellm present.

### Notes

- ChatGPT subscription OAuth is a separate, less-stable path and is not wired yet; `chatgpt`
  profiles map to a provider but `setup_provider_auth` reports it as unsupported for now.
- The device flow itself can only be completed by a human with a browser; the test suite
  exercises the full pending→authorized/failed lifecycle against a mocked authenticator.

## 0.3.1

Configuration facade: the plugin configures itself through its own MCP tools.

### Added

- **Six facade tools**: `provider_status`, `set_model_profile`, `remove_model_profile`,
  `set_default_profile`, `store_api_key`, `auth_status`. Profiles persist in
  `~/.cc-delegate/config.json` and are read **per task**, so configuration changes apply
  without restarting Claude Code.
- **`run_dev_task(profile=...)`**: per-task model selection by profile *name* (never a raw
  model string). Unknown names error with the list of available profiles.
- **Secret-safe key entry**: `store_api_key` without a `key` argument asks the user through an
  MCP elicitation dialog — the secret returns straight to the server and never enters the
  model's conversation context (verified end-to-end with an elicitation-capable client).
  Facade-stored keys land in `~/.cc-delegate/credentials.json`; resolution order is
  credentials file > provider env var > legacy `DELEGATE_API_KEY`.
- **OAuth-ready plumbing**: `DELEGATE_API_KEY` is now optional end-to-end (server, launcher,
  worker) so OAuth providers (litellm `github_copilot`/`chatgpt` token caches) can run
  keyless; `auth_status` reports token-cache presence. Starting a device flow from the facade
  ships with the v0.4 OAuth work.
- Skill: sovereignty rules — configuration tools and non-default profiles only on explicit
  user request; never reconfigure as a side effect of a failing delegation.

## 0.3.0

Single-runtime migration: the MCP server is now Python.

### Changed

- **MCP server rewritten in Python** (`server/`, official `mcp` SDK, FastMCP over stdio),
  replacing the TypeScript/Node implementation. Same four tools, identical response field
  names, and the same persisted-job JSON format — jobs written by the 0.2.x Node server load
  unchanged. `.mcp.json` now launches `uv run server/main.py`.
- **Single prerequisite**: Node.js is no longer required. `uv` resolves both the server's and
  the worker's inline dependencies (and Python itself) on first use. The committed esbuild
  bundle, `package.json`, `tsconfig.json`, and the whole npm chain are gone.
- The `SessionStart` prerequisite hook now probes only `uv --version`.

### Fixed

- **Child-process stdin hygiene**: the worker subprocess and git calls no longer inherit the
  server's stdin — which is the MCP protocol channel; an inheriting child stole protocol
  bytes and hung the session. Caught by the first end-to-end run of the Python server.

### Removed

- `src/`, `dist/mcp-server.js`, `tests/` (Node test suite — its cases live on as
  `server/test_*.py`), `package.json`, `package-lock.json`, `tsconfig.json`.

## 0.2.0

Python worker maturity + docs overhaul.

### Added

- **Real litellm cost tracking.** `worker/worker.py` now registers a litellm
  `success_callback` that meters `cost_usd` (via `litellm.completion_cost`)
  and `total_tokens` across every model call in the run — main agent,
  subagents, and the rubric grader. Per-call lookups are wrapped in
  `try/except`: models without a known price contribute nothing to
  `cost_usd` but never crash the run. Both fields land in the `RESULT_JSON:`
  payload so the supervisor can surface actual spend.
- **Shell-environment secret filtering.** The `LocalShellBackend` no longer
  uses `inherit_env=True`. It is now handed a filtered copy of `os.environ`
  built by a pure, unit-testable `is_sensitive_env_name()` helper that drops
  any variable whose name contains `API_KEY`, `APIKEY`, `TOKEN`, `SECRET`,
  `PASSWORD`, or `CREDENTIAL` (case-insensitive, substring match) — plus
  `DELEGATE_API_KEY` and the provider-specific key env var explicitly.
  `PATH`, `HOME`, `SystemRoot`, `TEMP`, etc. are preserved so that `git`,
  `node`, and `npm` keep working inside the worktree. The Python process's
  own `os.environ` still carries the provider key, since litellm reads it
  from there.
- **Live `PROGRESS:` streaming.** The agent now runs via `agent.stream(...)`
  with `stream_mode="updates"`. After every graph step the worker prints a
  flushed `PROGRESS:{"step","node","note"}` line, so `get_task_status`
  reflects what the worker is doing instead of going silent until the final
  `RESULT_JSON:` line. The final `RESULT_JSON:` contract is preserved
  exactly — same marker, same fields, plus the two new cost fields.
- **Job persistence across MCP-server restarts.** Jobs are mirrored to
  `<repo>/.cc-delegate/jobs/<task_id>.json` on every state change, so
  `get_task_status`, `fetch_task_result`, and `cleanup_task` all serve
  jobs created in a previous process lifetime via a disk fallback.
- **`cleanup_task` MCP tool.** New tool that tears down a finished task's
  git worktree, branch, and persisted job file in one call.
- **`node:test` suite.** Fast unit tests for the line-parsing helpers in
  `src/persistence.ts` (`findLastResultLine`, `parseProgressLine`,
  `progressNote`, `stripResultMarker`) runnable via `npm test`.
- **Architecture diagram.** The README's Architecture section now ships a
  ```mermaid``` flowchart of the supervisor → MCP server → worktree →
  worker → provider path, with edge labels for each hop.

### Changed

- **English-only codebase.** All remaining French comments and docstrings
  in `worker/worker.py` have been translated to English.
- **`README.md` Configuration section** no longer claims `cost_usd` stays
  `null`; the section now describes the value as reported-for-visibility
  rather than enforced-mid-run.
- **`plugin.json` keywords** dropped the stale `agent-sdk` entry (we no
  longer use the Claude Agent SDK — see `KNOWN_ISSUES.md`).

## 0.1.0

Initial release.

- **MCP delegation server** (`src/mcp-server.ts`) exposing `run_dev_task`,
  `get_task_status`, and `fetch_task_result` to the Claude Code supervisor
  over stdio.
- **Git-worktree isolation.** Every task gets its own disposable worktree
  on a fresh `delegate/<task_id>` branch — no writes ever land on the
  supervisor's working branch.
- **Deepagents worker** (`worker/worker.py`) with `implementer`, `tester`,
  and `reviewer` `SubAgent`s, driven by `RubricMiddleware` to grade
  completion against `definition_of_done` / `test_command` instead of
  trusting the model's own "I'm done" judgment.
- **`LocalShellBackend` in `virtual_mode=True`** to scope filesystem and
  shell access to the worktree.
- **Packaged supervisor skill** (`skills/delegate-heavy-dev/SKILL.md`)
  teaching the supervisor when and how to delegate.
- **`SessionStart` hook** (`hooks.json`) probing `node --version` and
  `uv --version` as an early best-effort environment check.
- **Single-file esbuild bundle** (`dist/mcp-server.js`) so the plugin has
  no runtime `node_modules` dependency.