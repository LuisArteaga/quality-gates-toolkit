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

- 2026-09-05: The `github.token` fallback is an installation token, which
  gets HTTP 403 on `GET /user`, so the self-review identity guard cannot
  run. Submission now proceeds with the verdict-derived action and logs a
  warning; GitHub rejects review actions on one's own PR server-side, so
  the guard is a convenience, not a security boundary. Consumers needing
  trusted identity pass a user PAT as `judge-token` (convention:
  `JUDGE_GH_TOKEN`).

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

Workflows check out the Python implementation with `toolkit-ref` (default:
the concrete release tag, e.g. `v1.0.0` — never a branch or `main`). The
toolkit's own CI passes its PR head
SHA instead, dogfooding the PR's implementation via relative `./` references
for the workflow definitions.

### Rationale

Reruns of failed runs re-resolve floating refs, so `@main` silently applies
merged changes to old runs. Tagged defaults make updates deliberate;
same-commit dogfooding tests the changes under review rather than the
published release.

### Amendments

- 2026-09-06: The default `toolkit-ref` is the concrete release tag
  (`v1.0.0`), not a floating major prefix — GitHub resolves no `v1` alias,
  so a default naming an nonexistent ref would break default-consuming
  callers. Callers pin `uses:` to an immutable release tag; the tag's own
  workflow file carries the matching default, keeping workflow and
  implementation checkout in lockstep per release.

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

## D-0009 — Toolkit scope: review-runtime tooling only

- Date: 2026-09-05
- Status: Accepted

### Decision

The toolkit ships only what a consumer's CI executes: the judge engine, the
deterministic gates, and their direct support modules. Ported orchestrator-
runtime machinery with no consumer in this repository was deleted:
`telemetry.py`'s loop/phase span state machine, persistent telemetry state
files, retrospective span export, and security-block buffering;
`redaction.py` remains solely as the redaction layer for the opt-in
Langfuse/OTLP export path. The unused `review.sh` wrapper was removed — CI
invokes `review.py` directly. `docs/context.md` is the stable architecture
entry point for review judges; decisions live in `DECISIONS.md`.

### Rationale

The bootstrap ported files wholesale for parity, shipping ~400 lines of
dormant machinery that nothing in the toolkit can ever invoke (the
orchestrator runtime stays in the origin project). Dead code in a public
v1.0.0 is a maintenance and review-noise liability; deleting it keeps the
radical-simplicity contract the toolkit's own judges enforce.

## D-0010 — Integrity-token suppression in the secret scanner

- Date: 2026-09-07
- Status: Accepted

### Decision

`secret_scan.py` does NOT skip npm/yarn lockfiles by file name. Instead,
`scan_text` neutralizes the exact integrity token shape
(`sha512-<base64>==`, i.e. an algorithm-prefixed base64 digest) before
the `high-entropy-base64` heuristic runs; every other detector scans the
raw text untouched. The pre-existing whole-file skips (`.env.example`,
`uv.lock`) are unchanged.

### Rationale

Lockfile integrity fields embed base64 content hashes of PUBLIC package
tarballs. They are content addresses, not credentials, but their length
and entropy reliably trip the `high-entropy-base64` heuristic — observed
on the first npm consumer's PR, where every `sha512-` integrity string in
`package-lock.json` was reported as a finding. An earlier draft of this
decision skipped `package-lock.json`/`yarn.lock` by file name; review
rejected that as a scanner bypass (real credentials smuggled into a
lockfile-named file — e.g. tokens in authenticated registry "resolved"
URLs — would have evaded every detector). The token-shape carve-out only
affects the one heuristic the digests actually false-positive on, so a
lockfile carrying a genuine secret is still flagged.
