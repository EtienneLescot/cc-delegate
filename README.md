# cc-delegate

Delegate heavy dev tasks from Claude Code (Opus supervisor) to an autonomous worker on a cheaper
model, via MCP. The worker is provider-agnostic — any model litellm can route to works. It
currently defaults to MiniMax M3, but that's just a default, not a limitation.

The supervisor stays on Anthropic; only the worker is billed on the alternate provider.

## Architecture

```mermaid
flowchart LR
  Supervisor["Claude Code supervisor<br/>(Anthropic API)"]
  MCP["cc-delegate MCP server<br/>Python (uv run server/main.py), stdio<br/>run_dev_task (watch), watch_task,<br/>get_task_status, answer_worker,<br/>cancel_task, fetch_task_result, cleanup_task"]
  Worktree["Disposable git worktree<br/>branch delegate/&lt;task_id&gt;"]
  Worker["uv run worker/worker.py<br/>deepagents loop<br/>+ implementer / tester / reviewer subagents<br/>+ rubric grader"]
  Provider["Provider<br/>(litellm-routed,<br/>e.g. MiniMax M3)"]
  Review["Supervisor review<br/>of patch / diff"]

  Supervisor -->|"run_dev_task /<br/>get_task_status /<br/>fetch_task_result /<br/>cleanup_task"| MCP
  MCP -->|"createWorktree()"| Worktree
  MCP -->|"uv run worker.py"| Worker
  Worker -->|"litellm completion"| Provider
  Provider -->|"model response"| Worker
  Worker -->|"PROGRESS: {step, node, ...}<br/>QUESTION: {id, message}<br/>(live, flushed per step)<br/>RESULT_JSON: {status, summary,<br/>turns, cost_usd, total_tokens,<br/>rubric_status, error}"| MCP
  MCP -->|"live progress notifications<br/>(watch mode, in chat)"| Supervisor
  Worktree -->|"git diff --cached"| MCP
  MCP -->|"patch + diff"| Review
```

- **`cc-delegate` MCP server** (`server/main.py`, official `mcp` Python SDK, run via
  `uv run`) — exposes the delegation tools to the supervisor over stdio: `run_dev_task` (start
  a delegated task — preflights your `test_command` first — and, by default, stream it live in
  the chat until it finishes or asks a question), `watch_task` (attach/resume the live stream),
  `get_task_status` (long-poll with `wait_seconds`; returns early on progress, completion, or
  a worker question), `answer_worker` (reply to a worker blocked on a question),
  `cancel_task` (kill a stalled/runaway worker's whole process tree, salvaging its work),
  `fetch_task_result` (final summary, patch, files changed, cost — including salvaged work
  from failed runs), and `cleanup_task` (tear down a finished task's worktree, branch, and
  persisted job file).
- **Job persistence** — every job is mirrored to `<repo>/.cc-delegate/jobs/<task_id>.json` on
  each state change, so `get_task_status` / `fetch_task_result` / `cleanup_task` still work
  across MCP-server restarts: the in-memory registry is rebuilt from disk on demand.
- **Delegate worker** (`worker/worker.py`) — a [deepagents](https://github.com/langchain-ai/deepagents)
  agent, run as a subprocess via `uv run` (see `server/worker_launcher.py`). Uses `LocalShellBackend` in
  `virtual_mode=True` to keep filesystem/shell access scoped to the disposable git worktree
  (branch `delegate/<task_id>`), `SubAgent`s for implementer/tester/reviewer roles, and
  `RubricMiddleware` to grade completion against `definition_of_done`/`test_command` instead of
  trusting the model's own "I'm done" judgment. Each run reports real `cost_usd` and
  `total_tokens` via a litellm success callback so the supervisor knows what the delegation
  actually cost, and prints a flushed `PROGRESS:` line per graph step so `get_task_status`
  shows what the worker is doing without waiting for completion.
- **Packaged skill** (`skills/delegate-heavy-dev/SKILL.md`) — teaches the supervisor when and
  how to delegate.

## Watch mode — the delegation streams live in the chat

By default `run_dev_task` runs in **watch mode**: the call blocks and relays the worker's
activity to the supervisor as MCP **progress notifications**, so the delegation shows live *in
the chat* — right where a Bash tool streams its output — on both the TUI and the desktop app. It
costs **zero supervisor tokens** (progress notifications bypass the model). You see every shell
command, progress note, and question stream by, then the call returns with the final result.

```
run_dev_task ⏳  worker started · MiniMax-M3
             $ npm test
             12 passing
             ✓ done · 4 files · $0.24
```

**Questions stay the supervisor's call.** When the worker asks something (`ask_supervisor` /
`report_blocker`), watch mode returns control to the supervisor with the question — it never pops
a dialog at the user. The supervisor decides at its discretion: answer from its own context with
`answer_worker(task_id, answer)`, or relay to the user when it is genuinely a user decision. Then
`watch_task(task_id)` resumes the live stream. A question is the single point that costs a
supervisor turn — exactly where judgment belongs.

For running several delegations at once, pass `watch=False`: `run_dev_task` returns a `task_id`
immediately and you supervise with `get_task_status(wait_seconds=…)` or attach later with
`watch_task`. Progress notifications degrade gracefully — a client that sends no `progressToken`
still gets a blocking call that returns the result, just without the intermediate lines.

## Status line — always-visible ambient indicator

Watch mode is the blow-by-blow view on the active tool call; the **status line** keeps a
one-line glance *in Claude Code's status bar* even after the tool call scrolls away, token-free
(TUI only — the desktop app does not render custom status lines). While a delegation runs:

```
⏳ delegate t_…yqsldx · MiniMax-M3 · step 24 · writing src/auth/tokens.js
⚠ delegate t_…yqsldx · asks: which token TTL? · → answer_worker
✓ delegate t_…yqsldx · done · 4 files · $0.24
```

How it stays token-free on both ends: the MCP server (already resident for the session) renders
the line in Python and writes it to `~/.cc-delegate/statusline`; the status-line script Claude
Code runs is a dependency-free reader (no `jq`, no `python`, no JSON parsing) that just prints
the pre-baked line while it is fresh. The harness runs it locally — it never consumes API tokens.

Wire it once in `~/.claude/settings.json` (point `command` at the shipped reader; **`refreshInterval`
is required** — status-line event triggers go quiet while the session waits on the background
worker, so the timer is what keeps the line live):

```json
{
  "statusLine": {
    "type": "command",
    "command": "~/.claude/cc-delegate-statusline.sh",
    "refreshInterval": 2
  }
}
```

Copy `statusline/cc-delegate-statusline.sh` (or, on Windows without Git Bash, the `.ps1`
variant) to `~/.claude/` and `chmod +x` it. A running task refreshes the line on every event; a
blocked task keeps its question visible until you answer; a finished task shows a short-lived
summary that then fades — no stale state left on screen.

## Worker → supervisor communication

The worker is not fire-and-forget anymore. Three tools are injected into its agent loop:

- **`report_progress(update)`** — fire-and-forget one-liners at phase transitions; they stream
  into watch mode, `get_task_status`, and the status line.
- **`ask_supervisor(question, context)`** — blocks the worker (zero tokens spent while
  waiting) and flips the task to status `needs_input`. In watch mode the `run_dev_task` /
  `watch_task` call returns with the question so the supervisor can answer or relay it; it then
  replies with `answer_worker(task_id, answer)` and the worker resumes.
- **`report_blocker(problem, attempts)`** — same mechanism, for "I've failed 3 times at the
  same error" situations: the supervisor gets a chance to correct course instead of the worker
  thrashing until timeout.

Answers travel out-of-band through a file mailbox in `<repo>/.cc-delegate/comm/<task_id>/` —
never through the model conversation. If no answer arrives within `DELEGATE_ASK_TIMEOUT_S`
(default 600s), the worker resumes with its best conservative judgment.

We started with the worker calling `@anthropic-ai/claude-agent-sdk`'s `query()` pointed at a
third-party endpoint, then tried shelling out to CLI coding agents (OpenCode, `dcode`) — both hit
either an unresolved Claude Code CLI headless-auth bug or a Windows/no-TTY hang in `dcode`'s rich
terminal UI. Calling `deepagents` directly as a library sidesteps both: no CLI, no TTY dependency,
and it gives us real control over the loop (subagents, rubric-based convergence) instead of a
black-box CLI. See [KNOWN_ISSUES.md](KNOWN_ISSUES.md) for the Claude Agent SDK auth bug writeup.

## Install

Installing the plugin itself is one command (below), but two things live outside Claude Code's
control and won't be set up for you: a model API key and `uv`. Neither is guaranteed just
because you have Claude Code. Go through these in order:

**1. Get a worker API key.** Default target is MiniMax — sign up at
[platform.minimax.io](https://platform.minimax.io) and generate a key. (Using a different
provider instead? Skip ahead to [Configuration](#configuration).)

**2. Install [uv](https://docs.astral.sh/uv/getting-started/installation/)**, if `uv --version`
doesn't already show it. That's the only runtime prerequisite: `uv run` resolves the MCP
server's and the worker's inline Python dependencies (and Python itself, if needed) on first
use — no `pip install`, no Node.js, no build step.

**3. Set `DELEGATE_API_KEY` as a persistent environment variable, then restart Claude Code.**
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

**4. Install the plugin.**

```
/plugin marketplace add EtienneLescot/cc-delegate
/plugin install cc-delegate@cc-delegate-marketplace
```

Or locally during development: `claude --plugin-dir .`

**5. Verify.** Run `/mcp` — this is the checkpoint that surfaces a missing `uv`/key before
you're mid-task. A `SessionStart` hook (`hooks.json`) additionally probes `uv --version` at the
start of every session as an earlier best-effort check; it's written in exec-form (no shell) so
it behaves the same on Windows/macOS/Linux, but a hook failure isn't guaranteed to surface as a
friendly message in the transcript — treat it as a bonus signal, not the primary one.

### For maintainers

No build step. The server is plain Python (`server/`, stdlib + the `mcp` SDK declared inline in
`main.py`); the worker is `worker/worker.py`. Run the test suite with:

```bash
uv run python -m unittest discover -s server -p "test_*.py"
```

## Verify

- `/mcp` should list the `cc-delegate` server and its tools.
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

**The facade (preferred):** the plugin configures itself through its own MCP tools, driven
conversationally from Claude Code — no restart needed, changes apply to the next task:

- *"Show me the provider status"* → `provider_status` lists your model profiles, the default,
  and per-profile auth state (key reachable? OAuth token cache present?).
- *"Add a deepseek profile"* → `set_model_profile("deepseek", "litellm:deepseek/deepseek-chat",
  "DEEPSEEK_API_KEY")`; `set_default_profile` / `remove_model_profile` manage the menu.
- *"Store my key for the deepseek profile"* → `store_api_key("deepseek")` asks you for the key
  through a native Claude Code dialog (MCP elicitation, Claude Code >= 2.1.199): **the secret
  goes straight back to the server without ever entering the model's conversation.**
- Per task: *"delegate this on the deepseek profile"* → `run_dev_task(..., profile="deepseek")`.
  The supervisor's skill forbids it from picking a non-default profile on its own.

Profiles live in `~/.cc-delegate/config.json`, facade-stored keys in
`~/.cc-delegate/credentials.json`. Any litellm-routable model works — see
[litellm's provider list](https://docs.litellm.ai/docs/providers).

**Subscription providers (OAuth):** for a profile on an OAuth provider — GitHub Copilot today
(`set_model_profile("copilot", "litellm:github_copilot/gpt-5")`, no API key) — run
`setup_provider_auth("copilot")`. It returns a verification URL and a user code; visit the URL,
enter the code, authorize, and `auth_poll(flow_id)` flips to `authorized`. litellm caches the
tokens, so later runs need no interaction and the key never touches the config. ChatGPT
subscription OAuth is planned but not wired yet.

**Legacy env path (still supported):** see [`.env.example`](.env.example) for
`DELEGATE_API_KEY`, `DELEGATE_MODEL`, `DELEGATE_API_KEY_ENV_VAR`, and the guardrails
(`DELEGATE_RECURSION_LIMIT`, `DELEGATE_RUBRIC_MAX_ITERATIONS`, `DELEGATE_TIMEOUT_MS`). It
applies when no profile is defined; env changes require restarting Claude Code (the
restart-trap warning in Install step 3 only concerns this path).

`DELEGATE_MAX_BUDGET_USD` is accepted and surfaced in `cost_usd`, but it is not yet enforced
mid-run: deepagents/LangGraph has no built-in budget cut-off hook, so the value is reported for
visibility rather than as a hard stop.

## License

MIT for this repository's own code. See [`NOTICE`](NOTICE) for a note on third-party terms of use.
