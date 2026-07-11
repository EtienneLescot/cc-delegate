# Spec 003 — Configuration facade (v0.3.x)

Status: accepted. Subsumes the "model profiles" item of spec 001 and provides the UX layer
spec 002's OAuth work plugs into. Delegation brief — SPEC.md is the authority.

## Why

Three standing problems share one root cause — configuration is env-vars-read-at-launch:

1. **The restart trap**: changing provider/model/key means editing OS env vars and restarting
   Claude Code (documented as the most error-prone install step).
2. **OAuth has no UX**: a device flow needs to surface a URL + code to the user and confirm
   completion; env vars can't do that.
3. **Profiles need a home**: the user-owned model menu (SPEC O7/FR-13) has nowhere to live.

The facade: the plugin configures itself through its own MCP tools, driven conversationally
from Claude Code ("switch the worker to DeepSeek", "set up Copilot auth", "show provider
status"). No restarts, no manual env editing.

## Design

### Persistent config store

- `~/.cc-delegate/config.json` (user-level, cross-repo): named profiles
  (`{name: {model, api_key_env_var?, api_base?, extra_headers?}}`), `default_profile`,
  and non-secret settings. Created on first use.
- The server reads the store **per task** (at `run_dev_task` time), not at launch. Env vars
  (`DELEGATE_MODEL`, etc.) remain as overrides/fallback for backwards compatibility; when both
  exist, the store wins and `provider_status` says so.
- API keys preferably stay OUT of the store: each profile names its `api_key_env_var`, and
  keys live in the OS env or in a separate `~/.cc-delegate/credentials.json` (0600-style
  permissions, gitignored by location) — decided per the secret-entry paths below.

### New MCP tools

| Tool | Behavior |
|---|---|
| `provider_status()` | Current default profile, all profiles with auth state (key env var set? OAuth token cache present?), config path, whether env overrides are active |
| `set_model_profile(name, model, api_key_env_var?, api_base?)` | Create/update a profile; validates the litellm prefix format |
| `remove_model_profile(name)` / `set_default_profile(name)` | Menu management |
| `store_api_key(profile, key?)` | Without `key`: returns the exact place to put it (env var name / credentials file) so the secret never transits the conversation. With `key`: stores it after an explicit warning that the value passed through the chat context |
| `setup_provider_auth(profile)` | Starts the provider's OAuth device flow in the background; returns `{verification_url, user_code}` for the supervisor to show the user |
| `auth_status(profile)` | Poll: device-flow completion / token-cache presence |

`run_dev_task` gains `profile: string` (name only, never a raw model string; unknown name →
error listing available profiles; absent → default).

### Secret-entry paths, in order of preference

1. **OAuth device flow** — no secret in the conversation at all (the whole point).
2. **MCP elicitation** — investigate: FastMCP's `ctx.elicit` lets the server request input
   through the client UI without transiting the model. If Claude Code supports elicitation
   for text input, use it for API keys and drop path 3.
3. **Key as tool parameter** — allowed but explicit: the tool warns that the key entered the
   conversation context and recommends rotating via path 1/2 later.

### Skill rule (user sovereignty, extended)

The supervisor only calls configuration tools when the user explicitly asked for a
configuration change in this conversation. Never as a side effect of a failing delegation
("MiniMax seems down, let me switch you to X" is FORBIDDEN — report and let the user decide).

## Definition of done

- Config store + per-task read; env fallback intact (a 0.3.0-style env-only setup keeps
  working unchanged).
- The six tools above registered and covered by unit tests (store round-trip, profile
  validation, status assembly; OAuth flow mocked).
- `run_dev_task(profile=...)` resolves the model per profile; `delegate-heavy-dev` skill updated
  with the sovereignty rule and the new tools.
- README: "Configuration" section rewritten around the facade; the restart-trap warning
  becomes "only needed for the legacy env-var path".
- E2E: change default profile via tools, run a toy delegation on it — no Claude Code restart.
