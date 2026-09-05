# DECISIONS.md

Lightweight decision log for the quality-gates-toolkit's **public
contracts**. One entry per contract: decision, rationale, amendments
appended in place (never rewritten). Incompatible contract changes require
a new major ref (`v2`), not just an amendment. Consumer-side architecture
(merge nodes, trusted judge identity, automerge) is documented in the
consumers' own ADRs.

## D-0001 — Deterministic gates precede LLM review

- Date: 2026-09-05
- Status: Accepted

### Decision

The LLM review runs only after every **enabled** deterministic gate has
succeeded. A disabled gate (skipped job) imposes no constraint.

### Rationale

Avoids model cost when a PR already fails enforceable checks, and prevents
probabilistic review from replacing deterministic validation. Tolerating
`skipped` (rather than demanding success) keeps `enable-lint: false` +
`enable-llm-review: true` a coherent combination.

### Amendments

None.

## D-0002 — Versioned hidden verdict-block format

- Date: 2026-09-05
- Status: Accepted

### Decision

The hidden verdict block posted by `review.py` (consumed by review parsers
in consumer repositories) is a versioned public contract. The block format
only changes with a new major toolkit ref.

### Rationale

Consumers' merge automation parses this block; floating the format would
break parsers silently. Writer/parser lockstep is the consumer's
responsibility to pin via `toolkit-ref`.

### Amendments

None.

## D-0003 — Two routing modes

- Date: 2026-09-05
- Status: Accepted

### Decision

Judge configs support `routing: null` (auto-route: OpenRouter's
price-weighted load balancing with automatic provider failover) and
`routing: [providers]` (pinned order, failover disabled). Consumers choose
per node via their own `factory.json`; the toolkit ships an example
documenting both.

### Rationale

Auto-route is zero-maintenance; pinned routing exists because auto-routing
can select the cheapest endpoint, which historically routed verdicts
through quantized/degraded endpoints. Which mode to pick is consumer
policy, not toolkit policy.

### Amendments

None.

## D-0004 — Neutral public defaults

- Date: 2026-09-05
- Status: Accepted

### Decision

The composite's defaults pass the stranger test: deterministic gates ON,
LLM review OFF (requires secrets), `lint-paths`/`cov-paths` `"."`,
`extra-pip-packages` `"none"`, `prefetch-tree-sitter` false. `coverage-floor`
is **required** — a gate threshold is a policy decision of the consumer.

### Rationale

A public product's defaults must reflect the public contract, not the
origin repository's configuration. Consumers express their specific
behavior explicitly in their caller files.

### Amendments

None.

## D-0005 — Optional judge token, trusted-identity boundary

- Date: 2026-09-05
- Status: Accepted

### Decision

`judge-token` is optional with a `github.token` fallback; `openrouter-api-key`
is validated fail-fast only when the LLM review is enabled; secrets are
forwarded explicitly (never blanket-inherited).

### Rationale

Plain consumers need only a token that can post a review. The
trusted-identity requirement (review author must equal a known identity) is
a property of consumers running trusted automerge loops — it belongs in
their integration docs, not in the generic workflow contract.

### Amendments

None.

## D-0006 — Hybrid workflow architecture

- Date: 2026-09-05
- Status: Accepted

### Decision

Each gate ships as a standalone micro-workflow; `pr-checks.yml` is a thin
composite that orchestrates them as JOBS via relative `./` references and
owns the ordering policy. Coverage passes from test to diff-coverage as an
artifact.

### Rationale

Consumers get both products: the whole opinionated gate suite via one
`uses:`, or individual gates picked à la carte — without the ordering
policy (the hardest-won knowledge) being re-implemented wrongly per
consumer. Relative references pin micro-workflows to the same commit/tag
as the composite; job-level composition means each micro-workflow installs
only its own tooling.

### Amendments

None.

## D-0007 — Tagged toolkit execution

- Date: 2026-09-05
- Status: Accepted

### Decision

Workflows check out the Python implementation with `toolkit-ref` (default
`v1`, a stable tag — never `main`). The toolkit's own CI passes its PR head
SHA instead, dogfooding the PR's implementation via relative `./` references
for the workflow definitions.

### Rationale

Reruns of failed runs re-resolve floating refs, so `@main` silently applies
merged changes to old runs. Tagged defaults make updates deliberate;
same-commit dogfooding tests the changes under review rather than the
published release.

### Amendments

None.

## D-0008 — Bootstrap carve-out from the changed-line coverage gate

- Date: 2026-09-05
- Status: Accepted

### Decision

The toolkit's own `ci.yml` disables `enable-diff-gate` for now. The
floor-based total coverage gate (80% via `coverage-floor`) remains the
enforced policy for this repository.

### Rationale

The diff gate enforces 100% coverage on changed lines. The toolkit's Python
implementation was ported from a private orchestrator whose suites exercise
it end-to-end, but porting the full changed-line discipline (per-line
coverage of ~3,000 inherited lines in the bootstrap commit) would block the
v1.0.0 release without improving the ported code. The carve-out is scoped to
the bootstrap period: a follow-up issue re-enables the gate once inherited
gaps are closed.

### Amendments

None.
