# cc-minimax-delegate

Delegate heavy dev tasks from Claude Code (Opus supervisor) to an autonomous MiniMax worker via MCP.

The supervisor stays on Anthropic; only the worker is billed on MiniMax. See the full design
rationale in the spec this repository was built from (three-tool async job model, isolated
git worktrees, provider isolation).

## Architecture

- **`minimax-delegate` MCP server** (`src/mcp-server.ts`) — exposes `run_dev_task`,
  `get_task_status`, `fetch_task_result` to the supervisor over stdio.
- **MiniMax worker** (`src/worker.ts`) — spawned via the Claude Agent SDK's `query()` with an
  isolated `env` pointed at MiniMax's Anthropic-compatible endpoint, running in a disposable
  git worktree on branch `mm/<task_id>`.
- **Packaged skill** (`skills/delegate-heavy-dev/SKILL.md`) — teaches the supervisor when and
  how to delegate.

## Install

```bash
npm install && npm run build
export MINIMAX_API_KEY=...
```

Load the plugin locally:

```bash
claude --plugin-dir .
```

Or install from a marketplace:

```bash
/plugin marketplace add EtienneLescot/cc-minimax-delegate
/plugin install minimax-delegate@cc-minimax-marketplace
```

## Verify

- `/mcp` should list the `minimax-delegate` server and its three tools.
- `/status` in the supervisor session should still show `api.anthropic.com` — no MiniMax
  config ever leaks into the supervisor process.
- Ask the supervisor to delegate a heavy task; it should call `run_dev_task`, poll
  `get_task_status`, then present the diff via `fetch_task_result`.

## Safety

The worker only writes inside a disposable git worktree and never runs `git push` or merges.
The supervisor always reviews the resulting diff before deciding whether to merge branch
`mm/<task_id>`.

## Configuration

See [`.env.example`](.env.example) for `MINIMAX_API_KEY`, `MINIMAX_BASE_URL`, `MINIMAX_MODEL`,
and the cost/turn/timeout guardrails (`MM_MAX_TURNS`, `MM_MAX_BUDGET_USD`, `MM_TIMEOUT_MS`).

## License

MIT for this repository's own code. See [`NOTICE`](NOTICE) for a note on the Claude Agent SDK's
license and MiniMax's terms of use.
