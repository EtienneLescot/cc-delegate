"""Environment-driven configuration. Mirror of src/config.ts."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    worker_api_key: str
    api_key_env_var: str
    model: str
    default_recursion_limit: int
    default_rubric_max_iterations: int
    default_max_budget_usd: float
    default_timeout_ms: int
    work_dir: str


def load_config() -> Config:
    worker_api_key = os.environ.get("DELEGATE_API_KEY")
    if not worker_api_key:
        raise RuntimeError("DELEGATE_API_KEY is required")
    return Config(
        worker_api_key=worker_api_key,
        # Provider-specific env var litellm reads for DELEGATE_MODEL's provider
        # prefix (e.g. MINIMAX_API_KEY for "minimax/..."). Change alongside
        # DELEGATE_MODEL when switching providers.
        api_key_env_var=os.environ.get("DELEGATE_API_KEY_ENV_VAR", "MINIMAX_API_KEY"),
        model=os.environ.get("DELEGATE_MODEL", "litellm:minimax/MiniMax-M3"),
        # LangGraph step count — every model call and tool call individually.
        default_recursion_limit=int(os.environ.get("DELEGATE_RECURSION_LIMIT", "400")),
        default_rubric_max_iterations=int(os.environ.get("DELEGATE_RUBRIC_MAX_ITERATIONS", "6")),
        # Accepted for forward compatibility; not yet enforced mid-run.
        default_max_budget_usd=float(os.environ.get("DELEGATE_MAX_BUDGET_USD", "5")),
        default_timeout_ms=int(os.environ.get("DELEGATE_TIMEOUT_MS", "1800000")),
        work_dir=os.environ.get("DELEGATE_WORK_DIR", ".cc-delegate"),
    )
