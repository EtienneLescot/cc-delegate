# Spec 001 — Pure-Python migration & user-owned model profiles (v0.3.0)

Status: accepted, not started. Blocked on: v0.2.0 merged to master.

## Why

Today the plugin needs two runtimes: Node >= 20 (MCP server, `dist/mcp-server.js`) and Python
via `uv` (deepagents worker). Node exists **only** because the MCP server was written in
TypeScript — the protocol itself has an official Python SDK (`mcp`) that is just as capable
over stdio. Dropping Node:

- cuts user prerequisites from two runtimes to one (`uv`, which also auto-installs Python);
- removes the committed 1.3 MB esbuild bundle and the entire npm/esbuild/tsc chain;
- eliminates the class of "did you rebuild dist before committing?" maintenance mistakes;
- unifies the codebase into one language around one dependency ecosystem (litellm/deepagents).

Rejected alternative — moving the *worker* to Node (`deepagentsjs`) instead: we would lose
litellm (Python-only), which supplies the 100+ providers, built-in cost math, and the native
`github_copilot`/`chatgpt` OAuth providers spec 002 depends on. Feature parity of the JS port
(RubricMiddleware, LocalShellBackend) was also unverified at decision time (July 2026).

## What

### 1. MCP server in Python

- Rewrite `src/*.ts` as a Python package (suggested: `server/` — keep `worker/worker.py` where
  it is) on the official `mcp` Python SDK, stdio transport, PEP-723 or a small
  `pyproject.toml` resolved by `uv run`.
- `.mcp.json` becomes `"command": "uv", "args": ["run", "${CLAUDE_PLUGIN_ROOT}/server/main.py"]`.
- Port 1:1: the four tools (`run_dev_task`, `get_task_status`, `fetch_task_result`,
  `cleanup_task`), git worktree/diff handling (`subprocess` replaces execa), JSON job
  persistence (same `.cc-delegate/jobs/` format — restarted servers must still read files
  written by the Node version), PROGRESS/RESULT_JSON stdout contract with the worker
  (now an in-language import boundary or subprocess — keep the subprocess boundary so the
  worker's crash/timeout isolation is preserved).
- Tool response shapes must not change (supervisor-visible contract).
- Delete: `src/`, `dist/`, `package.json`, `tsconfig.json`, npm test chain. Port the
  `tests/*.test.mjs` cases to `pytest` or stdlib `unittest` (keep them dependency-light).
- `hooks.json` prerequisite probe drops the `node --version` check.

### 2. Model profiles — user-owned selection

Principle on record (2026-07-11): **model choice belongs to the user, not the supervisor.**
The user knows their subscription quotas, API budgets, and which models they trust. The
supervisor must never unilaterally spend on a non-default profile.

- Config defines named profiles, e.g.
  `DELEGATE_MODEL_PROFILES='{"default":"litellm:minimax/MiniMax-M3","cheap":"...","strong":"..."}'`
  (JSON env var, or a small config file — pick whichever survives `.mcp.json` env plumbing
  best). Each profile may carry its own `api_key_env_var`.
- `run_dev_task` gains optional `profile: string` (name only — never a raw model string).
  Unknown profile → clear error listing available names. Absent → `default`.
- The packaged skill gains the rule: *only pass a non-default profile when the user explicitly
  asked for it* ("delegate this on the cheap profile").
- litellm fallbacks: optional `DELEGATE_MODEL_FALLBACKS` per profile.

### 3. Per-task observability

Lesson from v0.2.0 development: two runs burned their entire recursion budget on
environment traps (a test-glob collecting a build artifact; a missing PATH) and the failures
were undebuggable without forensic worktree inspection, because nothing records what the
agent did step by step.

- The worker appends one JSON line per stream update to
  `<repo>/.cc-delegate/logs/<task_id>.jsonl` (step, node, message/tool-call summary,
  timestamps; cost snapshot if cheap to compute).
- `fetch_task_result` returns the log path. `cleanup_task` removes it with the rest.

## Definition of done

From a machine with only `uv` installed (no Node): `/plugin` install → `/mcp` shows the four
tools → a toy delegation completes end-to-end with progress, cost, rubric verdict, patch, and
a populated `.jsonl` log; `uv run pytest` (or chosen runner) green; no `node`/`npm` references
left outside CHANGELOG/history; README install section reflects the single prerequisite.
