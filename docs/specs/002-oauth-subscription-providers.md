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

litellm also ships a native `chatgpt` provider (device flow, token cache via
`CHATGPT_TOKEN_DIR`, models like `chatgpt/gpt-5.3-codex`, `completion()` bridged to
Responses, strips rejected fields, `CHATGPT_ORIGINATOR` header). Known unknowns: the docs
do not mention tool calling, identity-instruction injection, or session ids — items 2–5 of
the checklist — and at least one open litellm issue reports subscription auth failures.

**Spike protocol** (timeboxed, before any commitment): run the standard toy delegation
(add function + test, requires write_file/execute tool round-trips) with
`DELEGATE_MODEL=chatgpt/gpt-5.3-codex` through the normal deepagents worker. Pass = multi-turn
tool calling works and the rubric grader completes. Evaluate each checklist item explicitly.

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
