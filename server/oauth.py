"""Headless OAuth device-flow manager (spec 002/003 — Copilot for now).

litellm's own device flow (`Authenticator._login`) prints the verification
URL + user code to stdout, which is useless for a headless MCP server. We
split it: call `_get_device_code()` to capture {verification_uri, user_code}
and relay them to the supervisor, then run the blocking
`_poll_for_access_token()` on a daemon thread so the event loop stays free.

litellm is imported lazily INSIDE the authenticator factory, so this module
(and its tests) import cleanly without litellm installed — the test suite
monkeypatches the factory.

Security: device_code and access tokens never leave this module. Tool
responses only ever carry verification_uri, user_code, and an opaque flow_id.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Any, Callable

# Provider key -> factory returning an object with the three methods we use:
#   _get_device_code() -> dict, _poll_for_access_token(device_code) -> str, get_api_key() -> str
# Tests replace entries here with stubs; litellm is imported only when a real
# factory runs.
def _github_copilot_authenticator() -> Any:
    from litellm.llms.github_copilot.authenticator import Authenticator

    return Authenticator()


PROVIDER_AUTHENTICATORS: dict[str, Callable[[], Any]] = {
    "github_copilot": _github_copilot_authenticator,
}

# Model-string substrings that map to an OAuth provider key. Kept in sync with
# config_store.OAUTH_CACHE_DIRS; only providers wired above are actionable.
_MODEL_PREFIX_TO_PROVIDER = {
    "github_copilot": "github_copilot",
    "chatgpt": "chatgpt",
}

_flows: dict[str, dict[str, Any]] = {}
_flows_lock = threading.Lock()


def provider_key_for_model(model: str) -> str | None:
    """Return the OAuth provider key implied by a model string, or None."""
    for prefix, provider in _MODEL_PREFIX_TO_PROVIDER.items():
        if prefix in model:
            return provider
    return None


def _run_poll(flow_id: str, authenticator: Any, device_code: str) -> None:
    """Background: block on litellm's poller, then warm the api-key cache."""
    try:
        authenticator._poll_for_access_token(device_code)
        # Mint/refresh the downstream key so the first real completion doesn't
        # have to; some providers key api access separately from the token.
        get_api_key = getattr(authenticator, "get_api_key", None)
        if callable(get_api_key):
            get_api_key()
        _set_status(flow_id, "authorized")
    except Exception as e:  # noqa: BLE001 - report, never crash the daemon
        _set_status(flow_id, "failed", error=f"{type(e).__name__}: {e}")


def _set_status(flow_id: str, status: str, error: str | None = None) -> None:
    with _flows_lock:
        flow = _flows.get(flow_id)
        if flow is None:
            return
        flow["status"] = status
        if error is not None:
            flow["error"] = error


def start_device_flow(provider: str) -> dict[str, Any]:
    """Begin a device flow. Returns relay-safe fields + an opaque flow_id.

    Raises ValueError for an unsupported provider. Any litellm/network error
    while requesting the device code propagates to the caller (main.py turns
    it into a clean error response).
    """
    factory = PROVIDER_AUTHENTICATORS.get(provider)
    if factory is None:
        supported = ", ".join(sorted(PROVIDER_AUTHENTICATORS)) or "(none)"
        raise ValueError(f"OAuth not supported for provider {provider!r}; supported: {supported}")

    authenticator = factory()
    device_info = authenticator._get_device_code()

    flow_id = secrets.token_hex(8)
    with _flows_lock:
        _flows[flow_id] = {
            "provider": provider,
            "status": "pending",
            "started_at": time.time(),
            "error": None,
        }

    thread = threading.Thread(
        target=_run_poll,
        args=(flow_id, authenticator, device_info["device_code"]),
        daemon=True,
        name=f"cc-delegate-oauth-{flow_id}",
    )
    thread.start()

    return {
        "flow_id": flow_id,
        "verification_uri": device_info["verification_uri"],
        "user_code": device_info["user_code"],
        "expires_in": device_info.get("expires_in"),
        "interval": device_info.get("interval"),
    }


def poll_status(flow_id: str) -> dict[str, Any]:
    """Non-secret status for a flow: pending | authorized | failed."""
    with _flows_lock:
        flow = _flows.get(flow_id)
        if flow is None:
            return {"error": "unknown flow_id"}
        out = {"flow_id": flow_id, "status": flow["status"], "provider": flow["provider"]}
        if flow.get("error"):
            out["error"] = flow["error"]
        return out
