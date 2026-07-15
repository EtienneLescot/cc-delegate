# Build journey — three worker engines, two upstream bugs

cc-delegate has one job that turned out to be surprisingly hard: **run an autonomous coding
worker on a model that isn't Claude, from inside Claude Code, on Windows.** The supervisor stays
on Anthropic; the worker was supposed to be "just point an agent at a cheaper endpoint." Three
engines and two upstream bugs later, it works — but not the way we first assumed. This is the
honest version of how we got here.

The throughline: **owning the loop beats driving someone else's.** Every failure below came from
delegating control to a layer we couldn't see into — a CLI's auth precedence, a TUI's TTY
assumptions. The fix, every time, was to move one layer down.

---

## Engine 1 — the Claude Agent SDK, pointed at a third-party endpoint

The obvious first move. MiniMax ships an Anthropic-compatible endpoint
(`https://api.minimax.io/anthropic`), so in theory you set `ANTHROPIC_BASE_URL`, hand
`@anthropic-ai/claude-agent-sdk`'s `query()` a key, and you have a full coding agent on a cheaper
model for free.

It never authenticated headlessly, and the failure was a maze rather than a wall:

- **The key is fine.** Raw `POST /v1/messages` to the endpoint with the exact same key returns
  `200 OK` — both `Authorization: Bearer <key>` and `x-api-key: <key>`. So it's not a config or
  credential problem.
- **`ANTHROPIC_AUTH_TOKEN` alone** → `Not logged in · Please run /login`, *before any network
  call*. Anthropic's own documented precedence says `AUTH_TOKEN` should outrank OAuth and be
  sufficient. It wasn't.
- **`ANTHROPIC_API_KEY` (real MiniMax key)** → passes the local gate, then a genuine server-side
  `401` from MiniMax, after 10 retries with backoff.
- **`apiKeyHelper`** → debug instrumentation confirmed the helper handed the CLI the correct
  125-character key. Still a real `401`.

So the same key value succeeds instantly via `curl` and fails through the CLI. The CLI is sending
*something* other than a clean bearer/x-api-key request — most likely a stale OAuth credential
from the machine's existing Anthropic login (`~/.claude/.credentials.json`) leaking into a
third-party request, or a separate headless code path for non-Anthropic base URLs. We never got a
clean look, because the one diagnostic left (`DEBUG_CLAUDE_AGENT_SDK=1` verbose stderr) would
print the live token in plaintext — it needs a sign-off and a redaction pass first.

**Documented in full:** [KNOWN_ISSUES.md → "Worker auth against a custom `ANTHROPIC_BASE_URL`
fails headlessly"](../KNOWN_ISSUES.md), with the exact repro, environment
(`claude-agent-sdk 0.3.207`, bundled CLI `2.1.205`, Node `v22.23.1`, Windows 11), and the
narrowing that rules out a key problem.

**Lesson:** a compatible endpoint isn't a compatible *client*. The SDK's auth precedence is tuned
for first-party Anthropic use; the moment you're a third-party endpoint in headless mode, you're
on an unadvertised path.

---

## Engine 2 — shelling out to a CLI coding agent

If the SDK won't cooperate, drive a standalone coding-agent CLI as a subprocess: OpenCode first,
then `dcode` (deepagents-code). Let it own the agent loop; we just start it and read its output.

`dcode` **hung indefinitely in non-interactive mode** on this machine. The likely cause is its
`rich`-based terminal UI, which doesn't degrade gracefully without a real TTY — confirmed from a
different angle when `dcode doctor` crashed outright on Windows' legacy console renderer. A rich
TUI expects a terminal; a subprocess with piped stdout is not a terminal; on Windows the gap is a
hang, not an error.

**Lesson:** a CLI built for a human at a terminal carries TTY assumptions all the way down. Piping
its stdout doesn't make it headless — it makes it stuck. And you inherit every one of its
decisions (auth, rendering, buffering) with no way to reach in.

---

## Engine 3 — deepagents as a library (where we landed)

Both dead ends shared a root cause: we were driving a black box — a CLI or an SDK whose internal
auth and I/O we couldn't reach. So we stopped driving one and **called
[deepagents](https://github.com/langchain-ai/deepagents) directly as a library** from
`worker/worker.py`.

Everything that hurt before evaporates:

- **No CLI, no subprocess coding-agent, no TTY.** No `rich`, no console renderer, nothing to hang.
- **No OAuth/login gate.** Plain `litellm` API-key auth — works on the first try, against any of
  litellm's 100+ providers, not just Anthropic-compatible endpoints.
- **Real control over the loop.** We compose it ourselves: `LocalShellBackend` in
  `virtual_mode=True` to scope the worker to a disposable git worktree, `SubAgent`s for
  implementer / tester / reviewer roles, and `RubricMiddleware` to grade completion against the
  caller's `definition_of_done` / `test_command` instead of trusting the model's own "I'm done".

Owning the loop is also what made the rest of the project *possible*: the two-tier
status/progress polling, the injected worker→supervisor comms (`ask_supervisor`,
`report_blocker`), mid-run `steer_task`, the stall watchdog, and per-step budget enforcement all
require hooks into the agent loop that a black-box CLI simply doesn't expose.

---

## The tax you pay for going one layer down

Owning the loop means you also own the sharp edges of the library underneath. Two more deepagents
issues (both on `0.7.0a6`) surfaced only because we were now close enough to hit them — and close
enough to work around:

- **`LocalShellBackend`'s timeout can hang forever on Windows.** The stock backend runs commands
  with `subprocess.run(shell=True, timeout=...)`. On Windows, CPython's timeout path kills only
  the direct shell, then calls `communicate()` again *without a timeout*; a surviving grandchild
  (`bash → find`) holds the stdout pipe open and blocks forever. A model-issued
  `find / -name logic.js` froze a real delegation until the 30-minute task timeout. Our
  `SupervisedShellBackend` runs the process itself and kills the whole tree on expiry.
- **`virtual_mode` silently remaps absolute paths.** A write to `/c/Users/.../file.txt` lands at
  `<worktree>/c/Users/.../file.txt` — no error, just a misplaced file. Models emit absolute paths
  spontaneously (often echoing `pwd`), so greenfield files drift into a junk subtree. Mitigated by
  mandating relative paths in the worker's system prompt.

Both are written up with repros in [KNOWN_ISSUES.md](../KNOWN_ISSUES.md).

---

## What the journey taught us

1. **Compatible ≠ interchangeable.** An Anthropic-compatible *endpoint* is not an
   Anthropic-compatible *client path*. Verify the whole request, not just the response.
2. **Headless is a first-class mode, or it's a hang.** Anything with a TTY-oriented UI must be
   treated as guilty until proven headless — especially on Windows.
3. **Own the loop.** Every capability that makes cc-delegate more than a wrapper — supervision,
   steering, budget caps, salvage, rubric-gated "done" — exists because the worker is a library we
   compose, not a CLI we poke. The failures pushed us exactly where we needed to be.
4. **Windows is the honest test.** Every bug here reproduced on Windows first. Building for it from
   the start surfaced problems a POSIX-only path would have shipped.

The two upstream bugs are filed as issues on our side for the record; the endpoint-auth one in
particular is a clean, reproducible report if the Claude Agent SDK ever needs it. Neither is a
complaint — they're the map of a real path through unfamiliar terrain.
