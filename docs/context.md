# Toolkit Architecture Context

`quality-gates-toolkit` packages the quality gates developed in a private
orchestrator project as a standalone, MIT-licensed toolkit: reusable GitHub
Actions workflows plus the Python tooling they execute. It is NOT the
orchestrator itself — no agent runtime, no merge automation, no repository
state.

## Scope boundary

The toolkit ships exactly what a consumer's CI needs to run the gates:

- `review.py` drives the LLM judges (OpenRouter) over a PR diff and posts
  one combined GitHub review carrying the versioned hidden verdict block.
- `diff_coverage_gate.py`, `secret_scan.py` are the deterministic gates.
- `judge_config.py` resolves per-judge model/routing configuration.
- `telemetry.py` provides tracing for the review run: OpenTelemetry with
  no-op degradation when the SDK is absent, local JSONL span logging, and
  opt-in OTLP/Langfuse export driven purely by environment variables.
- `redaction.py` scrubs secret-shaped values from span attributes and
  exported model names; it is reachable whenever Langfuse export is
  configured.
- `enrichment.py` adds enclosing-function context to Python diff chunks.

Orchestrator-runtime concerns (loop/phase span state machines, persistent
telemetry state files, merge automation) are deliberately out of scope and
were removed from the ported code; see DECISIONS.md D-0009.

## Decisions

Architecture decisions are recorded as D-0001… in `DECISIONS.md` at the
repository root (ADR-lite: amend in place; incompatible changes require a
new major toolkit ref). This file is the stable entry point the review
judges read for architecture context.
