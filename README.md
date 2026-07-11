# cc-delegate

Delegate heavy dev tasks from Claude Code (Opus supervisor) to an autonomous worker on a cheaper
model, via MCP. The worker is provider-agnostic — any Anthropic-compatible endpoint works. It
currently defaults to MiniMax M3, but that's just a default, not a limitation.

The supervisor stays on Anthropic; only the worker is billed on the alternate provider. See the
full design rationale in the spec this repository was built from (three-tool async job model,
isolated git worktrees, provider isolation).

## Architecture

- **`cc-delegate` MCP server** (`src/mcp-server.ts`) — exposes `run_dev_task`,
  `get_task_status`, `fetch_task_result` to the supervisor over stdio.
- **Delegate worker** (`src/worker.ts`) — spawned via the Claude Agent SDK's `query()` with an
  isolated `env` pointed at the worker's Anthropic-compatible endpoint (`DELEGATE_BASE_URL`),
  running in a disposable git worktree on branch `delegate/<task_id>`.
- **Packaged skill** (`skills/delegate-heavy-dev/SKILL.md`) — teaches the supervisor when and
  how to delegate.

## Install

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

The worker only writes inside a disposable git worktree and never runs `git push` or merges.
The supervisor always reviews the resulting diff before deciding whether to merge branch
`delegate/<task_id>`.

## Configuration

See [`.env.example`](.env.example) for `DELEGATE_API_KEY`, `DELEGATE_BASE_URL`, `DELEGATE_MODEL`,
and the cost/turn/timeout guardrails (`DELEGATE_MAX_TURNS`, `DELEGATE_MAX_BUDGET_USD`,
`DELEGATE_TIMEOUT_MS`). Swap `DELEGATE_BASE_URL`/`DELEGATE_MODEL` to target any other
Anthropic-compatible provider.

## License

MIT for this repository's own code. See [`NOTICE`](NOTICE) for a note on the Claude Agent SDK's
license and the worker provider's terms of use.
