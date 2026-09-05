# quality-gates-toolkit

Reusable GitHub Actions workflows, pre-commit hooks, and Python tooling for
deterministic and LLM-assisted quality gates.

Public, MIT-licensed, self-contained: every workflow below runs with **zero
local setup** in the consumer repository — no vendored scripts, no PAT for
tooling checkouts. The toolkit's Python implementation is checked out from
this public repository at a pinned ref (`toolkit-ref`).

## Components

### Reusable workflows (`.github/workflows/`)

| Workflow | Purpose |
|---|---|
| `pr-checks.yml` | **Opinionated composite entry point.** Orchestrates all gates as jobs and centrally enforces the ordering policy (deterministic gates before LLM review — the cost gate). |
| `lint.yml` | ruff lint + format check + mypy with toolkit-pinned tool versions. |
| `test.yml` | pytest with coverage, floor enforcement (`coverage-floor` is required), uploads `coverage.json` as an artifact. |
| `diff-coverage.yml` | 100% changed-line coverage gate (consumes the coverage artifact; PR events only). |
| `security.yml` | Semgrep + pip-audit. |
| `secret-scan.yml` | The toolkit's own stdlib secret scanner over all tracked files. |
| `llm-pr-review.yml` | LLM judges over the PR diff, posting one combined review. Requires `openrouter-api-key`. |

### Python tooling (`scripts/`)

`review.py` (judge engine), `diff_coverage_gate.py`, `secret_scan.py`,
plus internal modules (`judge_config.py`, `telemetry.py`, `redaction.py`,
`enrichment.py`). `enrichment.py` optionally uses
`tree-sitter-language-pack` (dev extra) for enclosing-function-context
enrichment and degrades gracefully without it.

## Quick start (composite)

```yaml
jobs:
  quality:
    uses: LuisArteaga/quality-gates-toolkit/.github/workflows/pr-checks.yml@v1.0.0
    with:
      coverage-floor: 80
    secrets:
      openrouter-api-key: ${{ secrets.OPENROUTER_API_KEY }}
```

`coverage-floor` is deliberately **required** — a gate threshold is a policy
decision, not plumbing. All deterministic gates default ON; the LLM review
defaults OFF (it needs secrets). Minimal caller floor:

```yaml
permissions:
  contents: read
  pull-requests: write   # only needed when enable-llm-review is on
```

A nested reusable workflow can narrow but never elevate the caller's token
scope.

## Picking individual gates

Each micro-workflow is independently callable, e.g.:

```yaml
jobs:
  security:
    uses: LuisArteaga/quality-gates-toolkit/.github/workflows/security.yml@v1.0.0
    with:
      scan-paths: "src"
```

The composite's ordering policy (especially the LLM cost gate) is enforced
centrally — composing micro-workflows yourself means re-implementing it.

## Judge configuration

Judges read a consumer-owned config file (`config-path` input, default
`config/factory.json`, resolved relative to the caller repository root).
Node names are fixed by the toolkit: `syntax_lint`, `test_coverage`,
`architecture`, `security`. See [`config/factory.example.json`](config/factory.example.json).

**Routing modes** — set per node:

- `routing: null` (or omitted) — **auto-route**: OpenRouter chooses the
  provider per request (price-weighted, automatic failover). Simplest
  setup, no per-provider maintenance.
- `routing: ["Provider A", "Provider B"]` — **advanced**: pinned provider
  order, failover disabled. Quality-controlled: verdicts only ever come
  from providers you trust. Chosen when auto-routing was observed to hit
  cheap-but-degraded endpoints.

Environment overrides (highest precedence): `SECURITY_MODEL` (per-node) >
`AGENT_MODEL` (global) > `factory.json` > toolkit default.

## Pre-commit hook

```yaml
repos:
  - repo: https://github.com/LuisArteaga/quality-gates-toolkit
    rev: v1.0.0            # pin a tag
    hooks:
      - id: secret-scan    # --staged scan of your staged changes
```

## Versioning

- `uses:` pins an immutable release tag (e.g. `@v1.0.0`); `toolkit-ref`
  (default = that same tag) selects the Python implementation checkout.
  Overrides are deliberate.
- Public contracts (verdict-block format, gate ordering, routing modes,
  defaults) are recorded in [`DECISIONS.md`](DECISIONS.md) and only change
  with a new major ref.
- Third-party actions are SHA-pinned; lint tool versions are pinned in
  `lint.yml` and bump with toolkit releases.

## Known limitations

- Fork PRs cannot access repository secrets; run the LLM review only for
  same-repo PRs (see `ci.yml` for the conditional-enable pattern).
- The toolkit's own CI passes its PR head SHA as `toolkit-ref` — that
  checkout target does not exist for fork PRs.
- `pull_request_target` is deliberately not offered as a fork workaround
  (it would check out and run untrusted PR code with secrets).

## License

MIT — see [LICENSE](LICENSE).
