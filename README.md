# cc-delegate

Delegate heavy dev tasks from Claude Code (Opus supervisor) to an autonomous worker on a cheaper
model, via MCP. The worker is provider-agnostic — any model litellm can route to works. It
currently defaults to MiniMax M3, but that's just a default, not a limitation.

The supervisor stays on Anthropic; only the worker is billed on the alternate provider.

## Architecture

- **`cc-delegate` MCP server** (`src/mcp-server.ts`) — exposes `run_dev_task`,
  `get_task_status`, `fetch_task_result` to the supervisor over stdio.
- **Delegate worker** (`worker/worker.py`) — a [deepagents](https://github.com/langchain-ai/deepagents)
  agent, run as a subprocess via `uv run` (see `src/worker.ts`). Uses `LocalShellBackend` in
  `virtual_mode=True` to keep filesystem/shell access scoped to the disposable git worktree
  (branch `delegate/<task_id>`), `SubAgent`s for implementer/tester/reviewer roles, and
  `RubricMiddleware` to grade completion against `definition_of_done`/`test_command` instead of
  trusting the model's own "I'm done" judgment.
- **Packaged skill** (`skills/delegate-heavy-dev/SKILL.md`) — teaches the supervisor when and
  how to delegate.

We started with the worker calling `@anthropic-ai/claude-agent-sdk`'s `query()` pointed at a
third-party endpoint, then tried shelling out to CLI coding agents (OpenCode, `dcode`) — both hit
either an unresolved Claude Code CLI headless-auth bug or a Windows/no-TTY hang in `dcode`'s rich
terminal UI. Calling `deepagents` directly as a library sidesteps both: no CLI, no TTY dependency,
and it gives us real control over the loop (subagents, rubric-based convergence) instead of a
black-box CLI. See [KNOWN_ISSUES.md](KNOWN_ISSUES.md) for the Claude Agent SDK auth bug writeup.

## Install

Installing the plugin itself is one command (below), but three things live outside Claude Code's
control and won't be set up for you: a model API key, Node.js, and `uv`. None of these are
guaranteed just because you have Claude Code — the `claude` binary is a self-contained native
executable that doesn't expose its own Node.js to other processes, and Claude Code doesn't run
`npm install` or any build step when it installs a plugin. Go through these in order:

**1. Get a worker API key.** Default target is MiniMax — sign up at
[platform.minimax.io](https://platform.minimax.io) and generate a key. (Using a different
provider instead? Skip ahead to [Configuration](#configuration).)

**2. Install Node.js >= 20**, if `node --version` doesn't already show it —
[nodejs.org](https://nodejs.org). This runs the pre-built `dist/mcp-server.js`; nothing compiles
on your machine.

**3. Install [uv](https://docs.astral.sh/uv/getting-started/installation/)**, if `uv --version`
doesn't already show it. It manages the worker's Python environment automatically — no
`pip install`, `uv run` resolves `worker/worker.py`'s inline dependencies (and Python itself, if
needed) on first use.

**4. Set `DELEGATE_API_KEY` as a persistent environment variable, then restart Claude Code.**
This is the step most likely to trip you up: `.mcp.json`'s `${DELEGATE_API_KEY}` only reads the
OS-level environment of the process that launched Claude Code — there's no `.env` file
auto-loading and no interactive prompt. Setting it in a terminal *after* Claude Code is already
running does nothing until you restart it from a shell that has the variable.

```powershell
# Windows (PowerShell) — persists across terminals, requires restarting Claude Code after
[Environment]::SetEnvironmentVariable("DELEGATE_API_KEY", "your-key-here", "User")
```

```bash
# macOS/Linux — add to ~/.zshrc or ~/.bashrc, then open a new shell
export DELEGATE_API_KEY="your-key-here"
```

**5. Install the plugin.**

```
/plugin marketplace add EtienneLescot/cc-delegate
/plugin install cc-delegate@cc-delegate-marketplace
```

Or locally during development: `claude --plugin-dir .`

**6. Verify.** Run `/mcp` — this is the checkpoint that surfaces a missing Node/`uv`/key before
you're mid-task. If `uv` isn't on `PATH`, `run_dev_task` also fails fast with a clear, actionable
error in `get_task_status`/`fetch_task_result` — this is the reliable signal, since it flows
through the same structured result the supervisor already reads. A `SessionStart` hook
(`hooks.json`) additionally probes `node --version` and `uv --version` at the start of every
session as an earlier best-effort check; it's written in exec-form (no shell) so it behaves the
same on Windows/macOS/Linux, but a hook failure isn't guaranteed to surface as a friendly message
in the transcript — treat it as a bonus signal, not the primary one.

### For maintainers

`dist/mcp-server.js` is a single-file esbuild bundle (no runtime `node_modules` needed — verified
by running it with `node_modules` removed). After changing anything under `src/`, rebuild and
commit the result before pushing:

```bash
npm install   # dev-time only: typescript, esbuild, @types/node
npm run build # tsc --noEmit for type-checking, then esbuild bundles dist/mcp-server.js
git add dist/mcp-server.js
```

## Verify

- `/mcp` should list the `cc-delegate` server and its three tools.
- `/status` in the supervisor session should still show `api.anthropic.com` — no worker
  config ever leaks into the supervisor process.
- Ask the supervisor to delegate a heavy task; it should call `run_dev_task`, poll
  `get_task_status`, then present the diff via `fetch_task_result`.

## Safety

The worker's `LocalShellBackend` runs in `virtual_mode=True`, scoping filesystem and shell access
to the disposable git worktree — it never runs `git push` or merges (also enforced via its system
prompt). The supervisor always reviews the resulting diff before deciding whether to merge branch
`delegate/<task_id>`.

## Configuration

See [`.env.example`](.env.example) for `DELEGATE_API_KEY`, `DELEGATE_MODEL`,
`DELEGATE_API_KEY_ENV_VAR`, and the guardrails (`DELEGATE_RECURSION_LIMIT`,
`DELEGATE_RUBRIC_MAX_ITERATIONS`, `DELEGATE_TIMEOUT_MS`). Swap `DELEGATE_MODEL`'s litellm
provider prefix (and the matching `DELEGATE_API_KEY_ENV_VAR`) to target any other provider —
see [litellm's provider list](https://docs.litellm.ai/docs/providers).

`DELEGATE_MAX_BUDGET_USD` is accepted but not yet enforced mid-run: deepagents/LangGraph has no
built-in cost meter, so `cost_usd` in `fetch_task_result`/`get_task_status` stays `null` for now.

## License

MIT for this repository's own code. See [`NOTICE`](NOTICE) for a note on third-party terms of use.
