# cc-delegate — Reference Specification

**Status: living document — single source of truth.** Merged from the original design document
(v0.1, "cc-minimax-delegate") and everything learned since. When implementation and this file
disagree, one of them is a bug: fix whichever is wrong.

Related documents: [ROADMAP.md](ROADMAP.md) (what & when) · [docs/specs/](docs/specs/)
(*delegation briefs* — disposable, self-contained work orders handed to `run_dev_task`;
they never override this file) · [CHANGELOG.md](CHANGELOG.md) · [KNOWN_ISSUES.md](KNOWN_ISSUES.md).

## 1. Problem & rationale

Claude Code cannot mix providers inside one process: the endpoint is read once at startup and
native subagents inherit it. A supervisor on Anthropic therefore cannot natively delegate
execution to a cheaper third-party model. cc-delegate solves this with a separate worker
process reached over MCP.

The original design ran the worker on the Claude Agent SDK pointed at an Anthropic-compatible
third-party endpoint. That architecture was abandoned: headless auth against a custom
`ANTHROPIC_BASE_URL` never worked (documented in KNOWN_ISSUES.md), and CLI-based alternatives
(OpenCode, dcode) failed on no-TTY/Windows constraints or offered no control over the agent
loop. The worker now runs on **LangChain deepagents as a library** — no CLI, no TTY, full
loop ownership — with **litellm** as the provider layer (100+ providers, built-in cost math,
native OAuth device-flow providers).

## 2. Objectives / non-objectives

Objectives:
- **O1.** One-call delegation of a heavy dev task from the supervisor to a cheap worker.
- **O2.** Worker autonomy: it owns its edit→run→test→fix loop and its own subagents
  (implementer / tester / reviewer). The supervisor supervises outcomes, not steps.
- **O3.** Structured results: summary, git patch, files changed, real cost (USD + tokens),
  turn count, rubric verdict, live progress while running.
- **O4.** Strict isolation: disposable git worktree per task; the main tree is never touched;
  the worker never pushes, merges, or commits; provider secrets never reach the shell
  commands the agent runs.
- **O5.** Convergence is *graded, not self-declared*: a rubric grader checks the definition
  of done rather than trusting the model's own "I'm done".
- **O6.** Distribution as a Claude Code plugin (skill + MCP server) that works from a clean
  install without a build step.
- **O7.** Model selection is **user-owned**: the user defines which models/providers are
  usable (their keys, their quotas); the supervisor never spends on a non-default choice
  without an explicit user request. (Profiles: planned, v0.3.0.)

Non-objectives:
- Replacing the supervisor: planning and final review stay on the supervisor's model.
- Interactive UI beyond Claude Code itself.
- Anthropic-subscription piggybacking (explicitly prohibited by Anthropic; not implemented).

## 3. Architecture (current: v0.3.x — single runtime)

| Component | Role | Tech |
|---|---|---|
| MCP server (`server/main.py`) | 4 stdio tools; worktree + diff + job registry/persistence | Python via `uv run`, official `mcp` SDK (FastMCP); stdlib elsewhere |
| Worker (`worker/worker.py`) | The agent loop: deepagents + subagents + rubric; PROGRESS/RESULT_JSON stdout contract | Python via `uv run` (PEP-723 inline deps), litellm routing |
| Skill (`skills/delegate-heavy-dev/`) | Teaches the supervisor when/how to delegate, poll, review | Claude Code packaged skill |

The only user prerequisite is `uv` (which also provisions Python). Subprocess hygiene rule:
every child the server spawns gets `stdin=DEVNULL` — the server's stdin is the MCP protocol
channel, and an inheriting child steals protocol bytes (learned from the first e2e run).

Data flow: supervisor → `run_dev_task` → git worktree `delegate/<task_id>` → `uv run
worker.py` (spawned with the provider key in process env only) → `PROGRESS:` lines stream
into `get_task_status.progress` → final `RESULT_JSON:` line → diff collected to
`.cc-delegate/patches/<task_id>.diff` → supervisor reviews → user decides merge →
`cleanup_task`. (Mermaid diagram: README, Architecture section.)

Remaining v0.3.x scope (user-owned model profiles, litellm fallbacks, per-task `.jsonl`
observability): brief docs/specs/001.

## 4. Functional requirements

| ID | Requirement | Status |
|---|---|---|
| FR-1 | `run_dev_task(spec, repo_path, …)` returns a `task_id` immediately; job runs in background | ✅ |
| FR-2 | Spec fields: objective, constraints, `definition_of_done`, `test_command`; the last two double as the rubric | ✅ |
| FR-3 | Worker iterates autonomously (edit→run→test→fix) inside its own loop | ✅ |
| FR-4 | Worker can spawn its own subagents; all inherit the worker's provider | ✅ |
| FR-5 | `fetch_task_result` → summary, patch path, files changed, `cost_usd`, `total_tokens`, turns, rubric status | ✅ |
| FR-6 | `get_task_status` shows live progress (latest step note) while running | ✅ v0.2.0 |
| FR-7 | Jobs survive MCP-server restarts (JSON under `.cc-delegate/jobs/`) | ✅ v0.2.0 |
| FR-8 | `cleanup_task` tears down worktree + branch + persisted state; refuses running jobs | ✅ v0.2.0 |
| FR-9 | Worker never pushes/merges/commits; supervisor reviews the diff; user decides the merge | ✅ (prompt-enforced + no credentials for push) |
| FR-10 | Provider isolation both ways: supervisor session has no worker config; worker shell env has no secrets | ✅ v0.2.0 (name-based filter: API_KEY/APIKEY/TOKEN/SECRET/PASSWORD/CREDENTIAL) |
| FR-11 | Guardrails: `recursion_limit` (LangGraph steps — every model+tool call), `timeout_ms` (hard abort), rubric `max_iterations` | ✅ |
| FR-12 | Mid-run budget cut-off on accumulated `cost_usd` | ⏳ planned |
| FR-13 | User-owned model profiles; per-task profile *name*; supervisor defaults to `default` | ⏳ v0.3.0 |
| FR-14 | Per-task step-by-step `.jsonl` log for post-mortems | ⏳ v0.3.0 |
| FR-15 | Subscription providers via OAuth device flow (Copilot; ChatGPT gated on validation spike) | ⏳ v0.4.0, brief: docs/specs/002 |

## 5. Security model

- **Worktree isolation**: all writes confined to `.cc-delegate/worktrees/<task_id>` on branch
  `delegate/<task_id>`; `LocalShellBackend(virtual_mode=True)` scopes file tools; the diff is
  collected from the worktree only.
- **Secret hygiene**: the provider key travels `MCP env → worker process env` and stops
  there; `build_shell_env()` strips secret-named variables before any shell command runs.
  Plugin `.mcp.json` references `${DELEGATE_API_KEY}` — never a literal.
- **No irreversible acts**: push/merge/rebase/commit forbidden to the worker (system prompt);
  the supervisor's skill mandates diff review before proposing a merge; the user owns the
  merge decision.
- **No third-party MCP loading in the worker**; the target repo's own config cannot inject
  servers into the agent.

## 6. Operational lessons (encoded as practice)

1. **Spec-size discipline**: one delegation = one bounded lot. A 9-item omnibus spec burned
   its entire step budget twice; two focused lots then succeeded. The skill says it; specs
   passed to `run_dev_task` must honor it.
2. **Validation-command hygiene**: the worker loops on its `test_command`; if that command
   can fail for reasons outside the worker's code (glob traps, missing PATH, flaky deps),
   the worker burns its budget on an invisible wall. Keep validation commands minimal and
   trap-free; prefer `py_compile`-class checks for non-executable lots.
3. **Observability is not optional**: budget-exhaustion failures are undebuggable without a
   step log (hence FR-14). Until it lands, forensic worktree inspection is the fallback.
4. **Environment beats intelligence**: both major failures were environment traps, not model
   weakness. When a delegation fails, inspect the worktree before blaming the model or
   raising budgets.
5. **Delegate modification, not synthesis**: bounded changes to existing code converge
   (lots A/B); open-ended "write N new files from cross-language references" burned a full
   budget producing two lines (migration lot M1). Greenfield ports either get file-level
   skeletons in the spec or are implemented by the supervisor directly.

## 7. Evolution

Planned work lives in [ROADMAP.md](ROADMAP.md); implementation plans for the next majors are
the delegation briefs [001 (pure-Python + profiles + observability)](docs/specs/001-pure-python-migration.md)
and [002 (OAuth subscription providers)](docs/specs/002-oauth-subscription-providers.md).
Decisions of record so far: deepagents-as-library over any CLI harness; litellm as the only
provider layer; no runtime dependency on yagr (reference implementation only); user
sovereignty over model spending.
