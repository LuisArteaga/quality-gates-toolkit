"""Judge model configuration resolution for quality-gates-toolkit.

Vendored, trimmed port of the orchestrator's ``resolve_model_config``:
resolves the model configuration for a judge node from a consumer-owned
``factory.json``. Orchestrator-runtime concerns (recursion budget, loop
detection thresholds) are intentionally NOT carried here — the judges never
consume them.

Precedence (highest to lowest):
  1. Node-specific env var: ``f"{node_name.upper()}_MODEL"`` (e.g. ``SECURITY_MODEL``)
  2. General env var: ``AGENT_MODEL``
  3. ``factory.json`` entry for the node
  4. Hardcoded ``DEFAULT_MODEL`` constant

The config file location is resolved at call time from the
``REVIEW_CONFIG_PATH`` env var (set by the toolkit's reusable workflows to the
consumer's ``config-path`` input), defaulting to ``config/factory.json``
relative to the current working directory — inside a called workflow that is
the caller checkout's root.

Routing modes (consumer policy, see README):
  - ``routing: null`` / omitted  → auto-route: OpenRouter chooses the provider
    per request (price-weighted, automatic failover).
  - ``routing: [..]``            → pinned provider order; failover disabled.
"""

import json
import os
import sys
from typing import Any

DEFAULT_MODEL = "z-ai/glm-5.3-flash"
CONFIG_PATH_ENV = "REVIEW_CONFIG_PATH"
DEFAULT_CONFIG_PATH = "config/factory.json"


def _warn(message: str) -> None:
    print(f"[WARN] {message}", file=sys.stderr)


def load_factory_config(filepath: str | None = None) -> dict[str, Any]:
    """Load and parse the consumer's factory.json.

    Returns an empty dict on missing or malformed files (never raises) so the
    resolver can degrade gracefully to DEFAULT_MODEL. No caching: review.py
    resolves judge configs a handful of times per run.
    """
    path = (
        filepath
        if filepath is not None
        else os.getenv(CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH)
    )
    if not os.path.exists(path):
        _warn(
            f"Judge configuration not found at '{path}'; falling back to "
            f"default model {DEFAULT_MODEL} for all nodes."
        )
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        _warn(f"Malformed judge configuration at '{path}': {exc}")
        return {}

    return data if isinstance(data, dict) else {}


def resolve_model_config(node_name: str) -> dict[str, Any]:
    """Resolve the judge Model Config for ``node_name``.

    Returns: ``{"model": str, "routing": list[str] | None,
    "temperature": float, "options": dict | None, "max_tokens": int | None,
    "fallback_model": str | None}``
    """
    factory = load_factory_config()
    factory_cfg = factory.get(node_name) if isinstance(factory, dict) else None
    if factory_cfg is not None and not isinstance(factory_cfg, dict):
        _warn(f"Factory entry for node '{node_name}' is not an object; ignoring.")
        factory_cfg = None

    # 1 & 2. Environment overrides
    node_env_var = f"{node_name.upper()}_MODEL"
    node_model = os.getenv(node_env_var)
    overridden_model = node_model or os.getenv("AGENT_MODEL") or None

    if overridden_model:
        # Env override active => disable provider routing: the override model
        # may not be registered in the factory's routing list. Inherit
        # temperature/options/max_tokens from the factory entry only if it
        # names the same model.
        if factory_cfg and factory_cfg.get("model") == overridden_model:
            temperature = factory_cfg.get("temperature", 0.0)
            options = factory_cfg.get("options")
            max_tokens = factory_cfg.get("max_tokens")
        else:
            temperature = 0.0
            options = None
            max_tokens = None
        source = node_env_var if node_model else "AGENT_MODEL"
        cfg: dict[str, Any] = {
            "model": overridden_model,
            "routing": None,
            "temperature": temperature,
            "options": options,
            "max_tokens": max_tokens,
            "fallback_model": factory_cfg.get("fallback_model")
            if factory_cfg
            else None,
        }
        _warn(f"Model override active for '{node_name}' via {source}.")
    elif factory_cfg:
        # 3. Factory configuration
        cfg = {
            "model": factory_cfg["model"],
            "routing": factory_cfg.get("routing"),
            "temperature": factory_cfg.get("temperature", 0.0),
            "options": factory_cfg.get("options"),
            "max_tokens": factory_cfg.get("max_tokens"),
            "fallback_model": factory_cfg.get("fallback_model"),
        }
    else:
        # 4. Hardcoded fallback (config missing/malformed or node absent)
        _warn(
            f"Node '{node_name}' not found in judge configuration; "
            f"falling back to {DEFAULT_MODEL} with auto routing."
        )
        cfg = {
            "model": DEFAULT_MODEL,
            "routing": None,
            "temperature": 0.0,
            "options": None,
            "max_tokens": None,
            "fallback_model": None,
        }

    print(
        f"[INFO] Resolved judge config (node={node_name}): model={cfg['model']}, "
        f"routing={'auto' if cfg['routing'] is None else 'pinned'}",
        file=sys.stderr,
    )
    return cfg
