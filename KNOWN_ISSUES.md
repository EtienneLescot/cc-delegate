# Known issues

## Worker auth against a custom `ANTHROPIC_BASE_URL` fails headlessly despite a valid key

**Status:** superseded, not fixed. The worker no longer uses `@anthropic-ai/claude-agent-sdk` —
it now calls the [deepagents](https://github.com/langchain-ai/deepagents) library directly
(`worker/worker.py`), which sidesteps this entirely (no Claude Code CLI, no OAuth/login gate,
plain `litellm` API-key auth that works on the first try). Kept below for the record and in case
a future need for the Claude Agent SDK specifically resurfaces.

We also tried shelling out to CLI coding agents (OpenCode, then `dcode`/deepagents-code) before
landing on calling `deepagents` as a library — `dcode` in particular hung indefinitely in
non-interactive mode on this machine, most likely because its `rich`-based terminal UI doesn't
degrade gracefully without a real TTY (confirmed separately: `dcode doctor` crashes outright on
Windows' legacy console renderer). Calling the library directly avoids that whole class of
problem — no subprocess, no TTY, no CLI-specific auth precedence rules to fight.

### Environment

- `@anthropic-ai/claude-agent-sdk`: `0.3.207` (bundled CLI `2.1.205`)
- Node `v22.23.1`, Windows 11
- Target endpoint: `https://api.minimax.io/anthropic` (MiniMax M3, official international endpoint)

### What works (rules out a config/key problem)

Raw HTTP `POST /v1/messages` against `https://api.minimax.io/anthropic` with the worker's key:
- `Authorization: Bearer <key>` → `200 OK`
- `x-api-key: <key>` → `200 OK`

### What fails

Every combination tried via `claude -p` / the SDK's `query()` ends in either a local login gate
or a genuine 401 from MiniMax's server (10 retries with backoff, then
`"Invalid API key · Fix external API key"`):

1. **`ANTHROPIC_AUTH_TOKEN` only** (no `ANTHROPIC_API_KEY`) → fails before any network call:
   `Not logged in · Please run /login`. Per Anthropic's documented
   [authentication precedence](https://code.claude.com/docs/en/authentication), `AUTH_TOKEN`
   alone should be sufficient and outranks OAuth — this contradicts that.
2. **`ANTHROPIC_API_KEY` only** (real MiniMax key) → passes the local gate, but the request
   itself gets a genuine server-side 401 from MiniMax.
3. **Both `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN`** set to the same valid key → same
   genuine 401.
4. **`settings.apiKeyHelper`** (SDK option, no `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` in env)
   → confirmed via debug instrumentation that the helper script receives and outputs the correct
   125-char key — still the same genuine 401.

In cases 2-4 a real network call is made and MiniMax rejects it, even though the exact same key
value works instantly via raw `curl`/`Invoke-WebRequest`. The CLI is evidently sending something
different from a clean `Authorization: Bearer <key>` or `x-api-key: <key>` request — possibly a
stale/conflicting credential from the machine's existing local OAuth session
(`~/.claude/.credentials.json`, present for the primary Anthropic account), extra headers, or a
different code path for third-party endpoints in headless mode.

### Repro

```powershell
$env:ANTHROPIC_AUTH_TOKEN = "<valid minimax key>"
$env:ANTHROPIC_BASE_URL = "https://api.minimax.io/anthropic"
claude -p "reply with the single word OK and nothing else"
# => Not logged in · Please run /login

$env:ANTHROPIC_API_KEY = "<same valid minimax key>"
claude -p "reply with the single word OK and nothing else"
# => Invalid API key · Fix external API key (after 10 retries, real 401s)
```

### Not yet tried

- Capture `DEBUG_CLAUDE_AGENT_SDK=1` / `debug: true` verbose stderr to see the exact outgoing
  request — the log would contain the live token in plaintext, so this needs explicit sign-off
  and a redaction pass before it's safe to inspect.
- Try an older `@anthropic-ai/claude-agent-sdk` version to rule out a regression.
- Test whether a one-time interactive `claude` login/trust flow (as MiniMax's own docs suggest
  running before headless use) changes anything.
- Check whether the existing OAuth session for the primary Anthropic account leaks into the
  third-party request despite the `ANTHROPIC_BASE_URL` override.

### Impact

Blocks `run_dev_task` end-to-end for any target model reachable only via a custom
`ANTHROPIC_BASE_URL` — i.e. the project's core "provider-agnostic worker" goal.
