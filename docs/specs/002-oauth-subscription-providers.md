# Spec 002 — OAuth subscription providers: Copilot & ChatGPT (v0.4.0)

Status: accepted with a validation gate. Depends on: spec 001 (profiles) landing first is
preferred but not required.

## Why

Many users have subscription quota (GitHub Copilot, ChatGPT Plus/Pro) that is cheaper for
them than metered API keys. The ecosystem norm in 2026 is that third-party coding harnesses
tap these via OAuth device flows: OpenCode, Kilocode, Cline, OpenClaw all ship "sign in with
ChatGPT"/Copilot flows. OpenAI tolerates and even supports this (its *Codex for Open Source*
program explicitly lists such tools); Anthropic cut off subscription use outside Claude Code
in April 2026, and Google did the equivalent for Gemini CLI.

**ToS positioning for the README** (factual, one paragraph): OpenAI — tolerated, active
support program, not a contractual guarantee; Anthropic — prohibited, do not implement;
GitHub Copilot — device flow widely used by third-party editors/tools.

## Decision on record: no yagr dependency

[yagr](https://github.com/EtienneLescot/yagr) (`packages/provider-runtime`) has working
TypeScript implementations of all three OAuth flows plus local per-provider proxies. We will
**not** depend on it at runtime:

- v0.3.0 removes Node; a Node sidecar would reintroduce it;
- coupling a public plugin's install story to a fast-moving personal project transfers its
  breakage to our users with less visibility;
- a sidecar proxy holding OAuth tokens enlarges the trust surface for third-party users.

yagr's role is **reference implementation**: it encodes the hard-won Codex knowledge below,
and is the model for any port or upstream contribution.

## The Codex quirks checklist (extracted from yagr `openai-account.ts`, July 2026)

Any ChatGPT-subscription integration must handle ALL of these — this is the validation
checklist for the spike and the test plan for any port:

1. **Endpoint**: `https://chatgpt.com/backend-api/codex/responses` (Responses API shape;
   *not* `api.openai.com`). Device flow verification at `…/codex/device`; models at
   `…/codex/models`.
2. **Codex identity instructions**: the `instructions` field must start with the Codex
   identity preamble ("You are Codex, based on GPT-5."); prepend it when the caller's system
   prompt lacks it (`ensureCodexInstructions` pattern).
3. **Tool calling shape**: prompt must be converted to Responses items — assistant tool calls
   become `{type:"function_call", call_id, name, arguments}`, tool results become
   `{type:"function_call_output", call_id, output}`, and assistant raw output items must be
   replayed **sanitized** (strip `id` and `status` fields) on subsequent turns.
4. **Proprietary headers**: `originator`, `session_id`, `x-codex-installation-id`,
   `x-codex-window-id`; `account_id` extracted from the JWT claim
   (`https://api.openai.com/auth` → `chatgpt_account_id`).
5. **Stateful incremental prompting** via `previous_response_id` (send only the delta).
6. **Rejected fields** must be stripped: `max_tokens`/`max_output_tokens`/
   `max_completion_tokens`, `metadata`.

## Plan

### Phase 1 — Copilot (low risk)

litellm ships a native `github_copilot` provider: OAuth device flow on first use, credentials
cached under `~/.config/litellm/github_copilot/`. Work:

- expose it as a model profile (`"copilot": "litellm:github_copilot/<model>"`);
- first-run UX: a `setup_provider_auth` MCP tool that triggers the flow out-of-band and
  relays the verification URL + device code back through the supervisor to the user (the
  worker is headless; auth must not stall a delegated task). Persisted-credential detection
  so subsequent runs skip it.

### Phase 2 — ChatGPT: validation spike, then decide

**Desk-audit update (2026-07-15, litellm 1.92.0), against `site-packages/litellm/llms/chatgpt/`.**
The checklist item-by-item, from reading the actual shipped source (not docs, which are still
thin on this provider):

1. **Endpoint** — confirmed: `CHATGPT_API_BASE = "https://chatgpt.com/backend-api/codex"`,
   `get_complete_url` appends `/responses`; device flow at `auth.openai.com/codex/device`.
   Live-checked: a real (unauthenticated, no-consequence) device-code request against
   `auth.openai.com` returned a valid `{device_auth_id, user_code, interval}` response — the
   endpoint is live and responding as documented. ✅
2. **Codex identity instructions** — confirmed: `CHATGPT_DEFAULT_INSTRUCTIONS` ("You are Codex,
   based on GPT-5...") is unconditionally prepended in `transform_responses_api_request`,
   matching the `ensureCodexInstructions` pattern exactly. ✅
3. **Tool calling shape** — our worker calls through `langchain_litellm.ChatLiteLLM` →
   `litellm.completion()`, i.e. the **`chat/` module** (`ChatGPTConfig(OpenAIConfig)`), not the
   `responses/` module directly. Strong evidence it works: `chat/streaming_utils.py` ships a
   dedicated `ChatGPTToolCallNormalizer` that fixes two *specific, named* real-world bugs in the
   backend's tool-call streaming (wrong `index` for parallel calls, duplicate "closing" chunks) —
   that only exists because someone already exercised real tool-calling against this backend and
   hit those bugs. High confidence, not a certainty from static reading alone. 🟡
4. **Proprietary headers + account_id** — confirmed: `_extract_account_id` decodes the JWT and
   reads the exact `https://api.openai.com/auth` → `chatgpt_account_id` claim from the spec;
   `originator`, `session_id`, `ChatGPT-Account-Id` headers all present (no `x-codex-window-id`
   equivalent, but nothing suggests it's required — Copilot-style single-session use, not a
   multi-window desktop app). ✅
5. **`previous_response_id` / stateful prompting** — present in the Responses-API allowlist, but
   whether litellm auto-threads it is unverified and, for our integration, **likely moot**: the
   `chat/` path we actually use is a stateless chat-completions call (full message history sent
   every turn), so incremental-prompting statefulness isn't something our worker needs anyway.
6. **Rejected fields** (`max_tokens`, `metadata`, ...) — confirmed via a different but equally
   effective mechanism: `transform_responses_api_request` returns only an explicit `allowed_keys`
   allowlist that simply excludes them, rather than a blocklist. ✅

**Net assessment: markedly more mature than this spec anticipated in July 2026.** Every checklist
item resolves positively or is moot for our specific call path, except tool-calling (item 3),
which has strong-but-indirect evidence (a real, named bug-fix) rather than a live confirmation.

**OAuth plumbing implemented** (`server/oauth.py`): litellm's real ChatGPT `Authenticator` is a
three-step flow (request device code → poll for an authorization code → exchange the code for
tokens) — different in shape from github_copilot's two-step one that `start_device_flow` was
built against, and its own `_login_device_code()` convenience method prints to stdout (the same
headless-unfriendly problem github_copilot's `_login()` had). `_ChatGPTDeviceFlowAdapter` drives
the three non-printing private methods directly and persists the result to the same auth file
`get_access_token()`/`get_account_id()` read from — so the existing generic
`start_device_flow`/`setup_provider_auth`/`auth_poll` tools work for `chatgpt` profiles with zero
changes to that generic code. 3 unit tests (stubbed inner authenticator) + confirmed against the
real litellm package that the adapter's assumed method names/shapes match.

**What's left — requires the user, not something I can complete alone:** the actual authorization
step (visiting the URL, entering the code) ties to a real ChatGPT Plus/Pro account, and the
`Spike protocol` below (a live toy delegation) needs that completed auth. Both are the user's to
do; the plumbing is ready.

**Spike protocol** (unchanged, still the bar for calling this "shipped"): run the standard toy
delegation (add function + test, requires write_file/execute tool round-trips) with a `chatgpt`
profile through the normal deepagents worker. Pass = multi-turn tool calling works and the rubric
grader completes.

Outcomes:
- **Spike passes** → ship as a profile like Copilot; done.
- **Spike fails on checklist items** → two fallbacks, in order of preference:
  1. upstream contribution to litellm's `chatgpt` provider (yagr as reference);
  2. targeted Python port (~300 lines: prompt→Codex-input conversion, headers, token
     refresh) as a custom LangChain chat model inside the worker — self-contained, we own
     the breakage.
- A local proxy sidecar remains the last resort, and would be a standalone minimal tool,
  not a yagr dependency.

## Definition of done

- Copilot: fresh machine, `setup_provider_auth` walks the user through device flow via the
  supervisor; a toy delegation completes on a Copilot model with cost/tokens reported
  (or cost null with a documented reason if Copilot pricing is unavailable).
- ChatGPT: spike executed and written up in this file (pass/fail per checklist item);
  chosen path implemented; toy delegation completes on `chatgpt/gpt-5.x-codex`.
- README ToS paragraph merged.
