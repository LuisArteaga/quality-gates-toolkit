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
  Posting needs no consumer PAT: without `judge-token` the review is a
  comment review by `github-actions[bot]` with the identical body, and the
  check's exit code is the merge gate (D-0005).
  A verdict is read from the `<findings>` block the judge prompt asks for: the
  block has to be PRESENTED as a block (a tag quoted inside a sentence is
  prose about the format, not a verdict), an answer without a readable block
  is retried once and then reported as unparseable rather than read as a
  pass, and an empty block is a legitimate pass (D-0026).
  An answer that is still unusable after that retry — unreadable, or empty —
  is asked of the node's `fallback_model` when one is configured, and a
  fallback answer that is unreadable too is reported the same way; the
  fallback attempt is final, so the ladder spends at most three calls, and a
  node without a `fallback_model` keeps the retry-only behaviour (D-0027).
  The fallback id is validated where the config is resolved (D-0028): a
  malformed value warns and leaves the node with no fallback rather than
  failing on the last attempt, a value equal to the node's `model` warns as
  the no-op rescue path it is, and a valid id is never checked against the
  provider's model list — resolution stays offline.
  When that submission is refused, the body is persisted, dumped to the job
  log and uploaded as a failure-path artifact instead of being discarded
  (D-0024).
- `diff_coverage_gate.py`, `secret_scan.py` are the deterministic gates.
  `semgrep_scan.py` wraps the external Semgrep scanner with the bounded
  ruleset-fetch retry that the pre-commit hook and `security.yml` both run
  (D-0025).
- `judge_config.py` resolves per-judge model/routing configuration, validating
  the two consumer-supplied knobs at that boundary: the completion cap always
  resolves to a positive integer (D-0021) and `fallback_model` always resolves
  to a non-empty string or `None` (D-0028).
- `telemetry.py` provides tracing for the review run: OpenTelemetry with
  no-op degradation when the SDK is absent, local JSONL span logging, and
  opt-in OTLP/Langfuse export driven purely by environment variables.
  Span-log destination: `AGENT_LOG_PATH` if set, else `.agent_logs/` beside
  the checkout, else `/workspace/.agent_logs` (container layouts), else
  `/tmp/agent_logs`. Export knobs: `OTEL_SERVICE_NAME`,
  `REVIEW_OTEL_PROJECT_NAME`, `REVIEW_OTEL_API_KEY`, `OTEL_EXPORTER_OTLP_*`.
- `redaction.py` scrubs secret-shaped values (known token shapes plus
  `KEY`/`TOKEN`/`SECRET`/`PASSWORD` assignment lines) from free-text span
  attribute values; it is reachable whenever Langfuse export is configured.
  Span values are only scrubbed at the attributes telemetry.py explicitly
  sets — it is not a wholesale span-value filter.
- `enrichment.py` adds enclosing-function context to Python diff chunks.
  The `tree-sitter-language-pack` is an optional dev extra; when absent,
  enrichment degrades gracefully and judges receive the raw diff.

Orchestrator-runtime concerns (loop/phase span state machines, persistent
telemetry state files, merge automation) are deliberately out of scope and
were removed from the ported code; see DECISIONS.md D-0009.

## Decisions

Architecture decisions are recorded as D-0001… in `DECISIONS.md` at the
repository root (ADR-lite: amend in place; incompatible changes require a
new major toolkit ref). This file is the stable entry point the review
judges read for architecture context.
