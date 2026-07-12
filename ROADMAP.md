# Roadmap

Decisions and rationale live in [SPEC.md](SPEC.md) — the single reference specification.
This file is the what-and-when; [docs/specs/](docs/specs/) hold disposable delegation briefs
(self-contained work orders for `run_dev_task`); they never override SPEC.md.

## v0.2.0 — reliability & honesty (released 2026-07-11)

- [x] Real cost tracking (`cost_usd`, `total_tokens`) via litellm callbacks
- [x] Shell-env secret filtering (no more `inherit_env=True` leaking keys into agent shell commands)
- [x] Live progress: worker streams `PROGRESS:` lines → `get_task_status.progress`
- [x] Job persistence across MCP-server restarts (`.cc-delegate/jobs/*.json`)
- [x] `cleanup_task` tool (worktree + branch + persisted-file teardown)
- [x] `node:test` suite; English-only codebase; CHANGELOG; Mermaid architecture diagram

## v0.3.0 — single runtime & user-owned model selection

See [docs/specs/001-pure-python-migration.md](docs/specs/001-pure-python-migration.md).

- [x] **Pure-Python migration** (released 0.3.0): MCP server on the official `mcp` Python SDK,
      everything through `uv run`. Node prerequisite, committed esbuild bundle, and npm chain
      removed — one prerequisite (`uv`) instead of two.
- [x] **Configuration facade** (released 0.3.1, brief: [docs/specs/003](docs/specs/003-config-facade.md)):
      the plugin configures itself through its own MCP tools — user-owned model profiles,
      provider switching, secret-safe key entry via elicitation — against a persistent
      `~/.cc-delegate/config.json` read per-task.
- [x] **GitHub Copilot OAuth** (released 0.3.2): device flow via litellm's native
      `github_copilot` provider, relayed by `setup_provider_auth` / `auth_poll`.
- [ ] litellm fallback chains (per profile)

## v0.4.0 — supervision, communication & resilience (released 2026-07-12)

Every item below is a direct fix for a failure mode observed in the first real-world
delegation session (Pong on MiniMax M3) — see CHANGELOG for the incident details.

- [x] **Worker → supervisor communication**: `report_progress` / `ask_supervisor` /
      `report_blocker` tools injected into the worker; blocking questions flip the task to
      `needs_input`, answered via the new `answer_worker` MCP tool (file mailbox, zero tokens
      while waiting).
- [x] **Live SSE dashboard** (`http://127.0.0.1:45673`): the user watches every shell command,
      progress note and question in a browser — zero supervisor tokens.
- [x] **`cancel_task`**: kills the worker's whole process tree, salvages, unblocks cleanup.
- [x] **Per-command timeout with tree kill** in the worker's shell backend (the stock
      backend's timeout leaves grandchildren holding the stdout pipe → infinite hang).
- [x] **Salvage snapshot**: any non-succeeded ending stages + WIP-commits uncommitted work, so
      `fetch_task_result` returns the patch even for failed/cancelled runs.
- [x] **`test_command` preflight** in `run_dev_task`: a broken test runner is surfaced to the
      supervisor before any worker token is spent.
- [x] **Long-poll `get_task_status(wait_seconds=...)`**: returns early on change; kills the
      poll-timer dance.
- [x] **Per-task observability**: append-only `.jsonl` event log per task
      (`.cc-delegate/logs/<task_id>.jsonl`) feeding the dashboard and post-mortems.

## v0.5.0 — ChatGPT subscription (OAuth)

See [docs/specs/002-oauth-subscription-providers.md](docs/specs/002-oauth-subscription-providers.md).

- [ ] **ChatGPT subscription** via litellm's native `chatgpt` provider — gated on a validation
      spike for tool calling under deepagents (Codex backend has documented quirks; see spec).
      Fallback paths defined in the spec. Decision on record: no runtime dependency on yagr;
      it serves as the reference implementation.
- [ ] Factual ToS note in README (OpenAI tolerates + runs a support program; Anthropic
      prohibits subscription use outside Claude Code; Google likewise for Gemini CLI).

## Visibility / traction (parallel track)

- [ ] Tag + GitHub release for v0.2.0 (current v0.1.0 release describes the abandoned
      Claude-Agent-SDK architecture)
- [ ] Demo GIF/asciinema of a full delegation round-trip
- [ ] README comparison table vs. adjacent projects (per-call routing vs. full-task delegation,
      worktree isolation, rubric-gated convergence)
- [ ] Submit to plugin/MCP directories; refresh the stale Glama listing
- [ ] Write-up of the build journey (three worker engines tried, two upstream bugs documented)

## Later / unscheduled

- Parallel multi-task delegation (architecture already supports concurrent worktrees)
- Mid-run budget enforcement (cut the run when accumulated `cost_usd` crosses the cap)
- Fine-grained tool policy for the worker (allow/deny beyond the current system-prompt rules)
