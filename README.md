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

Requires Node >= 20 and [uv](https://docs.astral.sh/uv/) (manages the worker's Python
environment automatically — no separate `pip install` needed, `uv run` resolves
`worker/worker.py`'s inline dependencies on first use).

```bash
npm install && npm run build
export DELEGATE_API_KEY=...
```

Load the plugin locally:

```bash
claude --plugin-dir .
```

Or install from a marketplace:

```bash
/plugin marketplace add EtienneLescot/cc-delegate
/plugin install cc-delegate@cc-delegate-marketplace
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
