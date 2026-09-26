"""Judge model configuration resolution for quality-gates-toolkit.

Vendored, trimmed port of the orchestrator's ``resolve_model_config``:
resolves the model configuration for a judge node from a consumer-owned
``factory.json``. Orchestrator-runtime concerns (recursion budget, loop
detection thresholds) are intentionally NOT carried here — the judges never
consume them.

Precedence (highest to lowest):
  1. Node-specific env var: ``f"{node_name.upper()}_MODEL"`` (e.g. ``SECURITY_MODEL``)
  2. General env var: ``AGENT_MODEL``
  3. ``factory.json`` entry for the node: top level first, then known/declared
     nested sections (see ``DEFAULT_JUDGES_SECTIONS`` / ``JUDGES_SECTION_KEY``)
  4. Hardcoded ``DEFAULT_MODEL`` constant

Consumers may nest judge configs under a section (e.g.
``ci_cd_pr_judges``) to coexist with other factory sections; resolution is
additive — flat top-level configs behave exactly as before.

The config file location is resolved at call time from the
``REVIEW_CONFIG_PATH`` env var (set by the toolkit's reusable workflows to the
consumer's ``config-path`` input), defaulting to ``config/factory.json``
relative to the current working directory — inside a called workflow that is
the caller checkout's root.

Routing modes (consumer policy, see README):
  - ``routing: null`` / omitted  → auto-route: OpenRouter chooses the provider
    per request (price-weighted, automatic failover).
  - ``routing: [..]``            → pinned provider order; failover disabled.

Completion bound: the resolved config always carries a positive
``max_tokens`` — the consumer's value when set, else ``DEFAULT_MAX_TOKENS``
(D-0021). It is a request-level latency/cost bound, not a model setting, so
it persists across the fallback path in review.py.

Fallback model: ``fallback_model`` is validated at this same boundary — a
malformed value warns and resolves to ``None``, so the retry ladder degrades
to "no fallback configured" instead of failing on its last attempt, and a
value equal to the resolved ``model`` warns (it re-asks the same model) but
is kept (D-0028). A configured id is never checked against OpenRouter's
model list: resolution stays offline, and the id must simply name a model
the provider serves.
"""

import json
import os
import sys
from typing import Any

DEFAULT_MODEL = "z-ai/glm-5.3-flash"
# Bounded completion-token default (D-0021). Applied at this resolution
# boundary when a consumer config omits `max_tokens`, so every judge request
# carries a completion bound: a degenerate generation (e.g. a reasoning loop)
# otherwise burns the model's whole output ceiling on one call — observed as
# 131,072 tokens / 23 min with empty content — while a judge verdict needs a
# few thousand. Observed judge completions (reasoning included) on a ~2.2k-line
# diff: 0.8k-12.2k tokens, so 32k keeps ~2.5x headroom.
DEFAULT_MAX_TOKENS = 32768
CONFIG_PATH_ENV = "REVIEW_CONFIG_PATH"
DEFAULT_CONFIG_PATH = "config/factory.json"
# Nested factory.json sections scanned when a node is not at the top level.
# Scanning is bounded to these known names plus sections declared via
# JUDGES_SECTION_KEY — a generic dict-of-dicts scan could silently resolve a
# judge node from an unrelated section whose node names happen to collide.
DEFAULT_JUDGES_SECTIONS = ("ci_cd_pr_judges",)
# Reserved top-level factory.json key: a list of additional nested section
# names to scan. The name is reserved and cannot be used as a node name.
JUDGES_SECTION_KEY = "judges-section"


def _warn(message: str) -> None:
    print(f"[WARN] {message}", file=sys.stderr)


def _resolve_max_tokens(value: Any, node_name: str) -> int:
    """The effective completion-token cap for ``node_name``.

    A configured ``max_tokens`` is used as-is when it is a positive integer;
    anything else (absent, ``null``, ``0``, negative, float, string, or a
    bool — ``True`` is an ``int`` in Python but never a token bound) warns and
    falls back to ``DEFAULT_MAX_TOKENS`` so a malformed config value can
    never remove the bound.
    """
    if value is None:
        return DEFAULT_MAX_TOKENS
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _warn(
            f"Config key 'max_tokens' for node '{node_name}' must be a "
            f"positive integer; using default {DEFAULT_MAX_TOKENS}."
        )
        return DEFAULT_MAX_TOKENS
    return value


def _resolve_fallback_model(value: Any, node_name: str, model: str) -> str | None:
    """The effective ``fallback_model`` for ``node_name``.

    A configured fallback is used as-is when it is a non-empty string; a
    malformed one (``null``, a non-string, or an empty/blank string) warns
    and resolves to ``None`` so the ladder degrades to "no fallback
    configured" — a malformed rescue path must not be the failure the last
    attempt reports (D-0028).

    A value equal to the resolved ``model`` warns but is kept: the fallback
    call resets ``routing``, ``options`` and ``temperature`` (ADR-0021), so it
    is still a different request for a routing-pinned node, and the attempt
    count is what bounds the ladder (D-0027). The id is otherwise not
    validated — resolution stays offline (D-0028).
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        _warn(
            f"Config key 'fallback_model' for node '{node_name}' must be a "
            f"non-empty string; ignoring it (no fallback configured)."
        )
        return None
    if value == model:
        _warn(
            f"Config key 'fallback_model' for node '{node_name}' equals its "
            f"'model' ({model}); the fallback re-asks the same model with "
            f"routing, options and temperature reset."
        )
    return value


def load_factory_config() -> dict[str, Any]:
    """Load and parse the consumer's factory.json.

    Returns an empty dict on missing or malformed files (never raises) so the
    resolver can degrade gracefully to DEFAULT_MODEL. No caching: review.py
    resolves judge configs a handful of times per run. The file location is
    resolved at call time from ``CONFIG_PATH_ENV``.
    """
    path = os.getenv(CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH)
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


def _declared_judges_sections(factory: dict[str, Any]) -> list[str]:
    """Return section names declared under the reserved ``judges-section`` key.

    Malformed declarations warn and are ignored so resolution degrades
    gracefully (same style as the rest of the module).
    """
    declared = factory.get(JUDGES_SECTION_KEY)
    if declared is None:
        return []
    if isinstance(declared, list) and all(isinstance(s, str) for s in declared):
        return list(declared)
    _warn(
        f"Config key '{JUDGES_SECTION_KEY}' must be a list of nested section "
        f"names; ignoring."
    )
    return []


def _nested_section_candidates(declared: list[str]) -> list[str]:
    """Ordered nested-section scan list: declared sections first (consumer
    intent), then the built-in defaults; deduplicated, order preserved."""
    candidates: list[str] = []
    for section in declared + list(DEFAULT_JUDGES_SECTIONS):
        if section not in candidates:
            candidates.append(section)
    return candidates


def resolve_model_config(node_name: str) -> dict[str, Any]:
    """Resolve the judge Model Config for ``node_name``.

    Returns: ``{"model": str, "routing": list[str] | None,
    "temperature": float, "options": dict | None, "max_tokens": int,
    "fallback_model": str | None}``

    ``max_tokens`` is always a positive integer: the configured value when
    valid, otherwise ``DEFAULT_MAX_TOKENS`` (D-0021). ``fallback_model`` is
    either a non-empty string or ``None``: a malformed value warns and
    resolves to ``None``, and one equal to ``model`` warns but is kept
    (D-0028).
    """
    factory = load_factory_config()
    # Validate the optional section declaration up-front (even when the
    # top-level resolves) so a malformed declaration is never silently
    # ignored; candidates are only consumed on a top-level miss.
    declared = _declared_judges_sections(factory) if isinstance(factory, dict) else []
    factory_cfg = factory.get(node_name) if isinstance(factory, dict) else None
    if factory_cfg is not None and not isinstance(factory_cfg, dict):
        _warn(f"Factory entry for node '{node_name}' is not an object; ignoring.")
        factory_cfg = None

    # Nested-section fallback (union resolution): only on top-level miss or
    # ignored top-level entry. Scans known/declared sections in order; the
    # first section holding the node wins.
    source_section: str | None = None
    if factory_cfg is None and isinstance(factory, dict):
        for section in _nested_section_candidates(declared):
            section_data = factory.get(section)
            if not isinstance(section_data, dict):
                continue
            entry = section_data.get(node_name)
            if entry is None:
                continue
            if not isinstance(entry, dict):
                _warn(
                    f"Factory entry for node '{node_name}' in section "
                    f"'{section}' is not an object; ignoring."
                )
                continue
            factory_cfg = entry
            source_section = section
            break

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
            max_tokens = _resolve_max_tokens(factory_cfg.get("max_tokens"), node_name)
        else:
            temperature = 0.0
            options = None
            max_tokens = DEFAULT_MAX_TOKENS
        source = node_env_var if node_model else "AGENT_MODEL"
        cfg: dict[str, Any] = {
            "model": overridden_model,
            "routing": None,
            "temperature": temperature,
            "options": options,
            "max_tokens": max_tokens,
            "fallback_model": _resolve_fallback_model(
                factory_cfg.get("fallback_model") if factory_cfg else None,
                node_name,
                overridden_model,
            ),
        }
        _warn(f"Model override active for '{node_name}' via {source}.")
    elif factory_cfg:
        # 3. Factory configuration
        cfg = {
            "model": factory_cfg["model"],
            "routing": factory_cfg.get("routing"),
            "temperature": factory_cfg.get("temperature", 0.0),
            "options": factory_cfg.get("options"),
            "max_tokens": _resolve_max_tokens(factory_cfg.get("max_tokens"), node_name),
            "fallback_model": _resolve_fallback_model(
                factory_cfg.get("fallback_model"),
                node_name,
                factory_cfg["model"],
            ),
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
            "max_tokens": DEFAULT_MAX_TOKENS,
            "fallback_model": None,
        }

    if source_section:
        print(
            f"[INFO] Judge config for '{node_name}' resolved from nested "
            f"section '{source_section}'.",
            file=sys.stderr,
        )
    print(
        f"[INFO] Resolved judge config (node={node_name}): model={cfg['model']}, "
        f"routing={'auto' if cfg['routing'] is None else 'pinned'}, "
        f"max_tokens={cfg['max_tokens']}",
        file=sys.stderr,
    )
    return cfg
