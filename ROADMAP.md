# Roadmap

Decisions and rationale live in [docs/specs/](docs/specs/); this file is the what-and-when.
Specs are written in delegation-ready form (objective, constraints, definition of done, test
command) so they can be handed to `run_dev_task` as-is when their time comes.

## v0.2.0 — reliability & honesty (in flight)

- [x] Real cost tracking (`cost_usd`, `total_tokens`) via litellm callbacks
- [x] Shell-env secret filtering (no more `inherit_env=True` leaking keys into agent shell commands)
- [x] Live progress: worker streams `PROGRESS:` lines → `get_task_status.progress`
- [x] Job persistence across MCP-server restarts (`.cc-delegate/jobs/*.json`)
- [x] `cleanup_task` tool (worktree + branch + persisted-file teardown)
- [x] `node:test` suite; English-only codebase; CHANGELOG; Mermaid architecture diagram

## v0.3.0 — single runtime & user-owned model selection

See [docs/specs/001-pure-python-migration.md](docs/specs/001-pure-python-migration.md).

- [ ] **Pure-Python migration**: rewrite the MCP server on the official `mcp` Python SDK, run
      everything through `uv run`. Removes the Node prerequisite, the committed esbuild bundle,
      and the whole npm chain — one prerequisite (`uv`) instead of two.
- [ ] **Model profiles, user-owned**: the user defines a named menu of models in config
      (`default`, plus whatever they want); `run_dev_task` accepts a profile *name* only.
      The supervisor never selects a non-default profile unless the user asked for it —
      quotas, keys, and model knowledge belong to the user, not the supervisor.
- [ ] litellm fallback chains (`DELEGATE_MODEL_FALLBACKS`)
- [ ] **Per-task observability**: step-by-step `.jsonl` transcript per task. Lesson from two
      budget-burn failures during v0.2.0 development: without a transcript, a recursion-limit
      failure is undebuggable except by forensic worktree inspection.

## v0.4.0 — subscription providers (OAuth)

See [docs/specs/002-oauth-subscription-providers.md](docs/specs/002-oauth-subscription-providers.md).

- [ ] **GitHub Copilot** via litellm's native `github_copilot` provider (device flow, cached
      credentials) + a `setup_provider_auth` MCP tool that relays the device code/URL to the
      supervisor for first-time auth.
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
