# Changelog

All notable changes to this project are documented here. Versions follow
[Semantic Versioning](https://semver.org/).

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