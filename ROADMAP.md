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
- [x] **litellm fallback chains** (released 0.10.0): `set_model_profile(..., fallback_models=[...])`.

## v0.4.0 — supervision, communication & resilience (released 2026-07-12)

Every item below is a direct fix for a failure mode observed in the first real-world
delegation session (Pong on MiniMax M3) — see CHANGELOG for the incident details.

- [x] **Worker → supervisor communication**: `report_progress` / `ask_supervisor` /
      `report_blocker` tools injected into the worker; blocking questions flip the task to
      `needs_input`, answered via the new `answer_worker` MCP tool (file mailbox, zero tokens
      while waiting).
- [x] **Live SSE dashboard** (`http://127.0.0.1:45673`): the user watches every shell command,
      progress note and question in a browser — zero supervisor tokens.
      *(Superseded: removed in v0.6.0; the desktop app can't integrate a separate tab and the user
      opted for poll-on-demand. Event bus + `.jsonl` logs remain, now backing `get_task_progress`.)*
- [x] **`cancel_task`**: kills the worker's whole process tree, salvages, unblocks cleanup.
- [x] **Per-command timeout with tree kill** in the worker's shell backend (the stock
      backend's timeout leaves grandchildren holding the stdout pipe → infinite hang).
- [x] **Salvage snapshot**: any non-succeeded ending stages + WIP-commits uncommitted work, so
      `fetch_task_result` returns the patch even for failed/cancelled runs.
- [x] **`test_command` preflight** in `run_dev_task`: a broken test runner is surfaced to the
      supervisor before any worker token is spent.
- [x] **Long-poll `get_task_status(wait_seconds=...)`**: returns early on change; kills the
      poll-timer dance.
      *(Superseded in v0.8.0: replaced by cheap instant `get_task_status` + verbose
      `get_task_progress`, polled on a supervisor-scheduled cadence — the supervisor's own
      scheduler makes a blocking long-poll unnecessary.)*
- [x] **Per-task observability**: append-only `.jsonl` event log per task
      (`.cc-delegate/logs/<task_id>.jsonl`) feeding the dashboard and post-mortems.

## v0.5.0 — ChatGPT subscription (OAuth)

See [docs/specs/002-oauth-subscription-providers.md](docs/specs/002-oauth-subscription-providers.md).

- [x] **ChatGPT subscription plumbing** (released 0.12.0): desk-audit of litellm's shipped
      `chatgpt` provider against the checklist (net: more mature than expected), plus
      `_ChatGPTDeviceFlowAdapter` so `setup_provider_auth`/`auth_poll` work for `chatgpt` profiles.
      Decision on record: no runtime dependency on yagr; it serves as the reference implementation.
      [ ] **Still open**: the live validation spike (toy delegation on a real `chatgpt` profile) —
      needs a user with a ChatGPT Plus/Pro subscription to complete the device-flow auth
      themselves; not something completable without them.
- [x] Factual ToS note in README (released 0.12.0).

## Visibility / traction (parallel track)

- [x] **GitHub releases caught up to current** (2026-07-15): v0.5.0 through v0.12.0 published
      with notes drawn from CHANGELOG.md (v0.9.0 has no standalone tag — it landed combined with
      v0.10.0 in one commit, so its notes are merged into the v0.10.0 release, matching the real
      git history rather than implying two separate releases happened).
- [x] **Demo GIF published** (2026-07-15, commit 73bfd34): 1.5 MB optimized GIF (shared 96-color
      palette, no dither) embedded in a centered README hero. Narrative reflects the current async
      model — `run_dev_task` returns immediately, a "time passing" beat, then a discrete
      `get_task_status`/`get_task_progress` poll rather than a live stream.
- [x] **README comparison table vs. adjacent projects** (2026-07-15): real research (houtini-lm,
      Roo Code's Orchestrator/Boomerang Tasks) rather than written from memory — per-call routing
      vs. full-task delegation, worktree isolation, rubric-gated convergence.
- [x] **Build-journey write-up** (2026-07-15): [docs/build-journey.md](docs/build-journey.md) —
      three worker engines tried (Claude Agent SDK → OpenCode/dcode CLIs → deepagents library),
      two upstream bugs documented, linked from the README. Doubles as external-post material.
- [x] **Glama listing** (2026-07-15): the auto-crawl already re-indexed the current Python
      architecture (A-grade, not stale as feared). Added `glama.json` (`maintainers:
      ["EtienneLescot"]`) to claim ownership and move it out of the anonymous-crawl pile.
- [ ] **Community plugin marketplace** — `anthropics/claude-plugins-community` auto-closes PRs;
      submission is the form at `clau.de/plugin-directory-submission`, gated by Anthropic's review
      pipeline. User-submitted (needs their identity); listing text is prepped and ready.
- [ ] Curated `awesome-claude-plugins` lists (ComposioHQ, GiladShoham, Chat2AnyLLM) — formats vary
      (link-list vs. copy-plugin-in marketplace); open PRs per-repo on the user's go-ahead.

## Later / unscheduled

- [x] **Proactive mid-run steering** (released 0.11.0): `steer_task(task_id, message)`. Delivered
      opportunistically at the worker's next tool call (`report_progress` / shell command), not
      instantaneously — genuine mid-LangGraph-step interruption would need a checkpointer, which
      is a bigger change left for if real usage shows the delay matters.
- Parallel multi-task delegation — architecture already supports concurrent worktrees, and v0.8.0
  added decompose/parallelize guidance to the skill; a helper to fan out + track a batch could come
  later.
- [x] **Mid-run budget enforcement** (released 0.10.0): `run_dev_task(..., max_budget_usd=...)`,
      checked after every step against the live cost tracker.
- [x] **Enforced git safety guard** (released 0.10.0): `git push`/`merge`/`rebase` are blocked at
      the shell-backend level, not just prompted against. Broader fine-grained tool policy
      (arbitrary allow/deny beyond this) remains open if a real need for it shows up.
