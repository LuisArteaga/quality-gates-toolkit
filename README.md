# quality-gates-toolkit

**Reusable CI gates for any repository: deterministic checks first, LLM review last, one `uses:` to wire it all.**

Reusable GitHub Actions workflows, pre-commit hooks, and Python tooling for
deterministic and LLM-assisted quality gates. Public, MIT-licensed and
self-contained: every workflow runs with **zero local setup** in the consumer
repository — no vendored scripts, no PAT for tooling checkouts — because the
toolkit's Python implementation is checked out from this public repository at a
pinned ref (`toolkit-ref`).

## Why quality-gates-toolkit

- **One call, whole suite.** A single `uses:` wires the deterministic gates and
  the LLM review together, and the deterministic gates always run first — a PR
  that fails an enforceable check never spends model budget (D-0001).
- **Skip-free by construction.** Per-language composites (D-0020) and the
  micro-workflows (D-0019) call only the gates you want, so the checks list
  carries no `Skipped` noise.
- **Zero local setup, pinned.** Callers need no toolkit checkout, no vendored
  scripts and no PAT — the review is posted tokenless by default (D-0005), and
  the toolkit implementation is fetched at an immutable release tag (D-0007).
- **Verdicts you can gate on.** Every review carries the versioned hidden
  verdict block (D-0002), and the check exits nonzero on FAIL / NEEDS REVIEW,
  so branch protection can enforce the outcome.

## Quick start (composite)

```yaml
jobs:
  quality:
    uses: LuisArteaga/quality-gates-toolkit/.github/workflows/pr-checks.yml@v1.8.9
    with:
      coverage-floor: 80
    secrets:
      openrouter-api-key: ${{ secrets.OPENROUTER_API_KEY }}
```

`coverage-floor` is deliberately **required** — a gate threshold is a policy
decision, not plumbing. All deterministic gates default ON; the LLM review
defaults OFF (it needs secrets) and runs only on `pull_request` events —
on other triggers (e.g. `push`) the judge job skips. Minimal caller floor:

```yaml
permissions:
  contents: read
  pull-requests: write   # only needed when enable-llm-review is on
```

A nested reusable workflow can narrow but never elevate the caller's token
scope.

Language composites (D-0020) are the skip-free variant for single-language
repositories — same defaults, one language:

```yaml
jobs:
  quality:
    uses: LuisArteaga/quality-gates-toolkit/.github/workflows/python-checks.yml@v1.8.9
    with:
      coverage-floor: 80
```

`js-checks.yml` takes no `coverage-floor` (no coverage artifact contract):
its three JS gates default ON, plus secret scan on and the LLM review off.

For repositories that are not Python-only, the
[Which entry point?](#which-entry-point) table maps your language mix to the
right composite.

### Language toggles

The composite's gate toggles are language-scoped (D-0013):

- **Python gate group** — the unprefixed toggles (`enable-lint`,
  `enable-test`, `enable-security`, `enable-secret-scan`,
  `enable-diff-gate`, plus the security sub-toggles) default **ON**.
- **JS gate group** — `enable-js-lint`, `enable-js-test`,
  `enable-js-typecheck` default **OFF**: the JS harness is npm-only and
  fails loudly without a `package-lock.json` in the repository root
  (D-0012), so a Python-only caller must never need one.
- `node-version` (default `22`) feeds all three JS jobs, mirroring how
  `python-version` feeds the Python gates.

The ordering policy spans both groups: the LLM review runs only after
every **enabled** deterministic gate is green. A JS-only caller disables
the Python gates and opts in explicitly:

```yaml
jobs:
  quality:
    uses: LuisArteaga/quality-gates-toolkit/.github/workflows/pr-checks.yml@v1.8.9
    with:
      coverage-floor: 0          # nominal — Python test gate disabled below
      enable-lint: false
      enable-test: false
      enable-security: false
      enable-diff-gate: false
      enable-js-test: true
      enable-js-typecheck: true
      enable-js-lint: true
```

`coverage-floor` remains a required input even for JS-only callers —
`workflow_call` cannot express conditionally-required inputs, and the
policy contract should not weaken silently; pass a nominal `0` when the
Python test gate is off. `secret-scan` is language-agnostic and stays on.

That example demonstrates the toggle contract — for a *pure* JS/TS
repository it is the noisy shape: all six disabled Python gates render as
`Skipped` checks. Pure-JS repositories use the `js-checks.yml` language
composite (skip-free with the same one-call ergonomics, D-0020) or call
the JS micro-workflows directly — see
[Which entry point?](#which-entry-point).

## Components

### Reusable workflows (`.github/workflows/`)

| Workflow | Purpose |
|---|---|
| `pr-checks.yml` | **Opinionated composite entry point.** Orchestrates all gates as jobs and centrally enforces the ordering policy (deterministic gates before LLM review — the cost gate). Language-scoped toggles (D-0013): Python gates on by default, JS gates opt-in. |
| `python-checks.yml` | **Python language composite (D-0020).** The Python gate group as one `uses:` — skip-free for Python-only callers (no disabled-gate noise). Optional embedded LLM review (default off) and secret scan (default on). |
| `js-checks.yml` | **JS language composite (D-0020).** The three JS gates as one `uses:` — skip-free for JS/TS callers. Optional embedded LLM review (default off) and secret scan (default on). |
| `lint.yml` | ruff lint + format check + mypy with toolkit-pinned tool versions. Installs the caller project (`pip install -e ".[dev]" || pip install -e .`, best-effort) plus `extra-pip-packages` first, so mypy sees the caller's dependency surface. |
| `test.yml` | pytest with coverage, floor enforcement (`coverage-floor` is required), uploads `coverage.json` as an artifact. Installs the caller via `pip install -e ".[dev]"` — strict, no fallback (D-0014): pytest comes from the caller's dev extra. |
| `diff-coverage.yml` | 100% changed-line coverage gate (consumes the coverage artifact; PR events only). |
| `security.yml` | Semgrep + pip-audit. Semgrep runs through the toolkit's `semgrep-scan` wrapper, which retries a failed ruleset fetch a bounded number of times (D-0025). |
| `secret-scan.yml` | The toolkit's own stdlib secret scanner over all tracked files. Best-effort regex detection — not a Gitleaks replacement; pair it with Gitleaks for defense in depth if you want broader coverage. |
| `llm-pr-review.yml` | LLM judges over the PR diff, posting one combined review. Requires `openrouter-api-key` only — no PAT: the review is posted by `github-actions[bot]` as a comment review. |
| `js-test.yml` | Runs the caller's `npm test` on a caller-chosen Node version. Harness-only (D-0012): the project owns the test runner via `package.json`. |
| `js-typecheck.yml` | Runs the caller's `npm run typecheck` under the same JS harness contract. |
| `js-lint.yml` | Runs the caller's `npm run lint` under the same JS harness contract. |

### Python tooling (`scripts/` + `quality_gates_toolkit/`)

The importable `quality_gates_toolkit` package is the judge-engine
implementation (`review.py` plus support modules `judge_config.py`,
`telemetry.py`, `redaction.py`, `enrichment.py`) — see
[Importable judge API](#importable-judge-api). The `scripts` package keeps
the standalone tools `diff_coverage_gate.py` and `secret_scan.py` (the
console script behind the `secret-scan` pre-commit hook), their sibling
`semgrep_scan.py` (the `semgrep-scan` console script: `semgrep scan` plus
the bounded ruleset-fetch retry, D-0025), plus
backward-compatibility shims for the moved modules (D-0017).
`enrichment.py` optionally uses `tree-sitter-language-pack` (dev extra)
for enclosing-function-context enrichment and degrades gracefully without
it.

## Caller prerequisites

"Zero local setup" means no toolkit checkout and no PAT for tooling — the
gates still expect the *caller* project to be self-contained:

- **Python repos:** pip-installable (`pyproject.toml`, `setup.py`, or
  `setup.cfg`). The two Python gates install the caller differently, and
  the asymmetry is deliberate (D-0014):
  - **lint** installs `pip install -e ".[dev]" || pip install -e .` — if
    the `[dev]` extra is absent or unresolvable it falls back to a
    runtime-only install so mypy still sees your dependency surface. A
    caller without `[dev]` passes lint only while the linted code imports
    nothing beyond runtime dependencies (pytest imports in linted test
    code still fail mypy).
  - **test** installs `pip install -e ".[dev]"` with no fallback —
    pytest comes from your `[dev]` extra, so a caller without one warns
    at install time and fails at the pytest step.
  Declare a `[dev]` extra containing your test toolchain to make both
  gates green. pip treats a missing extra as a warning, not an error.
- **JS repos:** `package-lock.json` in the repository root plus the fixed
  script contracts (`test`, `typecheck`, `lint`) in `package.json` — see
  [JavaScript / TypeScript gates](#javascript--typescript-gates).
- **Judge config** (optional): `config/factory.json` relative to the caller
  root. A missing or malformed file is not fatal — judges fall back to the
  toolkit default model and log a `[WARN]`.

## Which entry point?

| Your repository | Entry point | Checks-list appearance |
|---|---|---|
| Python-only, wants one `uses:` | Composite `python-checks.yml` (D-0020) | Every gate runs — no `Skipped` entries by construction |
| Pure JS/TS, wants one `uses:` | Composite `js-checks.yml` (D-0020) | Every gate runs — no `Skipped` entries by construction |
| Polyglot (Python + JS), wants the opinionated suite | Composite `pr-checks.yml` | Every **enabled** gate runs; disabled gates (the JS group by default) appear as `Skipped` |
| Skip-free with hand-picked gates | Micro-workflows directly — the toolkit's own `ci.yml` is the live example (D-0019) | Every called gate runs |

GitHub Actions cannot hide a disabled job: jobs in a workflow file are
static, and a job turned off by `if:` — including a job that calls a
reusable workflow — always renders as `Skipped` (it reports Success for
branch protection, but the noise is real; upstream:
[community/44490](https://github.com/orgs/community/discussions/44490),
[community/72708](https://github.com/orgs/community/discussions/72708)).
The polyglot composite stays the opinionated default for polyglot callers —
inert `Skipped` checks are the price of one `uses:` for the whole suite —
while single-language repositories get the same one-call ergonomics
skip-free through their language composite, and the micro-workflows remain
the maximum-control path.

**Exactly one judge per PR.** Wiring more than one composite on the same PR
(e.g. `python-checks.yml` + `js-checks.yml` in a monorepo) means
`enable-llm-review` — and any other language-agnostic gate you keep enabled
in more than one composite, i.e. `enable-secret-scan` — must be turned on in
**exactly one** of them (D-0020): the judge reviews the whole PR diff
regardless of which composite invokes it, so one review per PR is both
sufficient and cost-correct. Two enabled judges mean double cost and two
verdict blocks.

## Secrets

The workflows declare two secret *inputs* (`openrouter-api-key`,
`judge-token`). **Only `OPENROUTER_API_KEY` is ever needed: no GitHub PAT is
required to post a review.** Without `judge-token` the review is posted by
`github-actions[bot]` as a comment review carrying the identical body, and the
check's exit code is the merge gate (D-0005). The toolkit's own CI runs
exactly this PAT-free path on every one of its PRs. The repo secret name
`JUDGE_GH_TOKEN` below is the convention this repository documents for the
optional input — the input names are the contract. Deterministic-only usage
(`enable-llm-review: false`, the default) reads no secrets at all.

### `OPENROUTER_API_KEY`

- **Required when** the LLM review is enabled.
- **Why:** the judges call OpenRouter to review the PR diff. The value is
  forwarded to `llm-pr-review.yml` as its `openrouter-api-key` input.
- **Without it:** the review job fails fast with an explicit error once
  enabled; deterministic gates are unaffected.
- Create the key in the OpenRouter dashboard (Keys) and consider a per-key
  spend limit.

### `JUDGE_GH_TOKEN` (optional — the tokenless path is the default)

- **Not needed for the default contract.** When it is not set, the review
  falls back to the caller's `github.token` and is authored by
  `github-actions[bot]`: the review is posted as a **comment review** (state
  `COMMENTED`) with the identical body — hidden verdict block, per-judge
  reasoning and KPI table — and the check's exit code, not the review state,
  is the merge gate (FAIL / NEEDS REVIEW exits nonzero; see
  [Consuming verdicts](#consuming-verdicts)). Nothing is lost by leaving the
  secret unset, and the toolkit's own CI is the live example.
- **Why an installation token cannot do more:** GitHub forbids the Actions
  token from approving a pull request at all — the repository setting
  "Allow GitHub Actions to create and approve pull requests" governs it,
  independent of who authored the PR. The toolkit therefore never submits
  `--approve` without a user identity; an all-PASS verdict from a plain
  consumer still posts its verdict block, per-judge reasoning and KPI
  table, only as a comment.
- **When to pass a user PAT:** only for consumer-side integration models —
  neither case is a toolkit prerequisite.
  - *Verdict-driven review state:* with a PAT whose owner differs from the
    PR author, the review state follows the verdict — `--approve` on
    all-PASS, `--request-changes` on FAIL / NEEDS REVIEW.
  - *Trusted-identity automerge:* if your pipeline verifies that the review
    author equals a known judge identity, a bot-authored review does not
    match. Pass a PAT whose account is the trusted judge identity.
  - *Self-review guard:* the `github.token` fallback is an installation
    token (HTTP 403 on `GET /user`), so the guard that downgrades a review
    on the judge's own PR to a comment cannot run. A user PAT makes the
    guard functional — though it is a convenience, not a boundary: GitHub
    rejects review actions on one's own PR server-side.
- **Required scopes:**
  - Classic PAT: `repo` for private repositories; `public_repo` suffices
    for public ones.
  - Fine-grained PAT: access to the consumer repository with
    **Pull requests: Read and write** (plus the mandatory metadata read).
    No contents access needed — the diff is computed from the local git
    checkout; the token is only used for the review API calls.
- Forwarded as `judge-token`; the workflow falls back to `github.token`
  automatically when it is declared but unset.

### Fork PRs

Fork PRs cannot access repository secrets, so the LLM review can never
authenticate for forked contributions. Enable it conditionally for same-repo
PRs only — on a composite, pass the guard as the review toggle:

```yaml
enable-llm-review: ${{ github.event.pull_request.head.repo.full_name == github.repository }}
```

On direct micro-workflow calls, guard the judge job itself (the toolkit's
own `ci.yml` is the live example, D-0019):

```yaml
llmreview:
  needs: [lint, test, security, secretscan]
  if: github.event.pull_request.head.repo.full_name == github.repository
  uses: LuisArteaga/quality-gates-toolkit/.github/workflows/llm-pr-review.yml@v1.8.9
```

`pull_request_target` is deliberately not offered as a workaround: it would
check out and run untrusted PR code with secrets attached (see Known
limitations).

## Picking individual gates

Each micro-workflow is independently callable, e.g.:

```yaml
jobs:
  security:
    uses: LuisArteaga/quality-gates-toolkit/.github/workflows/security.yml@v1.8.9
    with:
      scan-paths: "src"
```

The composite's ordering policy (especially the LLM cost gate) is enforced
centrally — composing micro-workflows yourself means re-implementing it.

## Input reference

### Composite (`pr-checks.yml`)

| Input | Type | Default | Purpose |
|---|---|---|---|
| `python-version` | string | `"3.12"` | Python for lint, test, and security. |
| `node-version` | string | `"22"` | Node for the JS gates. |
| `lint-paths` | string | `"."` | Space-separated paths for ruff, mypy, and Semgrep. |
| `cov-paths` | string | `"."` | Space-separated import paths, measured with repeated `--cov` flags. New top-level packages must be added here (see [Troubleshooting](#troubleshooting)). |
| `coverage-floor` | number | **required** | Minimum total coverage; a policy decision. |
| `extra-pip-packages` | string | `"none"` | Space-separated PyPI packages installed after the caller project; the token `none` skips. |
| `prefetch-tree-sitter` | boolean | `false` | Cache and prefetch tree-sitter parsers (callers whose tests parse code). |
| `enable-lint`, `enable-test`, `enable-security` | boolean | `true` | Python gate group toggles. |
| `enable-semgrep`, `enable-pip-audit` | boolean | `true` | Sub-toggles inside the security gate. |
| `enable-secret-scan` | boolean | `true` | Toolkit secret scanner (language-agnostic, always available). |
| `enable-diff-gate` | boolean | `true` | 100% changed-line coverage (pull_request events only). |
| `enable-js-lint`, `enable-js-test`, `enable-js-typecheck` | boolean | `false` | JS gate group (needs a `package-lock.json`). |
| `enable-llm-review` | boolean | `false` | LLM judges after all deterministic gates; pull_request events only. |
| `config-path` | string | `"config/factory.json"` | Judge config path relative to the caller repository root. |
| `diff-exclude` | string | `""` | Space-separated git pathspecs excluded from the judge diff (e.g. `uv.lock package-lock.json`). |
| `batch-budget-chars` | string | `""` (effective `200000`) | Per-batch character budget for splitting the judge diff. Raise it (e.g. `500000`) so large PRs are judged whole — small batches make judges report "tests missing" for files whose tests landed in another batch. |
| `toolkit-ref` | string | `"v1.8.9"` | Ref of the toolkit checkout — secret scanner, judges, and the Semgrep wrapper `security.yml` runs. Overrides are deliberate. |

Secrets: `openrouter-api-key` (needed when `enable-llm-review` is on) and
`judge-token` (optional) — see [Secrets](#secrets).

### Language composites (`python-checks.yml` / `js-checks.yml`)

Per-language entry points (D-0020): each declares only its own language's
knobs plus the judge plumbing. A composite defaults its own language's
gates ON (the caller chose that entry point); `enable-llm-review` defaults
`false` and `enable-secret-scan` `true` on both.

| Input | Type | Default | Scope / purpose |
|---|---|---|---|
| `python-version` | string | `"3.12"` | python-checks — Python for lint, test, and security. |
| `node-version` | string | `"22"` | js-checks — Node for the three JS gates. |
| `lint-paths` | string | `"."` | python-checks — paths for ruff, mypy, and Semgrep. |
| `cov-paths` | string | `"."` | python-checks — import paths measured with repeated `--cov` flags. New top-level packages must be added here (see [Troubleshooting](#troubleshooting)). |
| `coverage-floor` | number | **required** | python-checks — minimum total coverage; a policy decision. |
| `extra-pip-packages` | string | `"none"` | python-checks — as on the polyglot composite. |
| `prefetch-tree-sitter` | boolean | `false` | python-checks — parser cache/prefetch for tests and judge enrichment. |
| `enable-lint`, `enable-test`, `enable-security` | boolean | `true` | python-checks — Python gate group. |
| `enable-semgrep`, `enable-pip-audit` | boolean | `true` | python-checks — sub-toggles inside the security gate. |
| `enable-diff-gate` | boolean | `true` | python-checks — 100% changed-line coverage (pull_request events only). |
| `enable-js-lint`, `enable-js-test`, `enable-js-typecheck` | boolean | `true` | js-checks — the JS gate group (needs a `package-lock.json`). |
| `enable-secret-scan` | boolean | `true` | both — toolkit secret scanner (language-agnostic). |
| `enable-llm-review` | boolean | `false` | both — LLM judges after all deterministic gates; pull_request events only. Enable in exactly one composite per PR (D-0020). |
| `config-path` | string | `"config/factory.json"` | both — judge config path relative to the caller repository root. |
| `diff-exclude` | string | `""` | both — space-separated git pathspecs excluded from the judge diff (e.g. `uv.lock package-lock.json`). |
| `batch-budget-chars` | string | `""` (effective `200000`) | both — per-batch character budget for splitting the judge diff. Raise it (e.g. `500000`) so large PRs are judged whole — small batches make judges report "tests missing" for files whose tests landed in another batch. |
| `toolkit-ref` | string | `"v1.8.9"` | both — ref of the toolkit checkout — secret scanner, judges, and the Semgrep wrapper `security.yml` runs. Overrides are deliberate. |

### Micro-workflows

| Workflow | Inputs (default) | Secrets |
|---|---|---|
| `lint.yml` | `python-version` `"3.12"` · `lint-paths` `"."` · `extra-pip-packages` `"none"` | — |
| `test.yml` | `python-version` `"3.12"` · `cov-paths` `"."` · `coverage-floor` (required) · `extra-pip-packages` `"none"` · `prefetch-tree-sitter` `false` | — |
| `security.yml` | `python-version` `"3.12"` · `scan-paths` `"."` · `enable-semgrep` `true` · `enable-pip-audit` `true` · `toolkit-ref` `"v1.8.9"` | — |
| `secret-scan.yml` | `toolkit-ref` `"v1.8.9"` | — |
| `diff-coverage.yml` | `toolkit-ref` `"v1.8.9"` · `coverage-artifact` `"coverage-json"` | — |
| `llm-pr-review.yml` | `toolkit-ref` `"v1.8.9"` · `config-path` `"config/factory.json"` · `diff-exclude` `""` · `prefetch-tree-sitter` `false` · `batch-budget-chars` `""` | `openrouter-api-key` (required) · `judge-token` (optional) |
| `js-test.yml`, `js-typecheck.yml`, `js-lint.yml` | `node-version` `"22"` | — |

## JavaScript / TypeScript gates

`js-test.yml`, `js-typecheck.yml`, and `js-lint.yml` bring the
deterministic-gate pattern to npm projects. The contract (D-0012): **the
harness owns the environment, the project owns the tools.**

- The toolkit owns: the Node runtime (`node-version` input, default `22`),
  the checkout, `npm ci`, and the npm dependency cache.
- The project owns: all JS/TS tooling and its configuration via
  `package.json` (test runner, TypeScript, ESLint, etc.).
- Fixed script contracts, no command inputs in v1: `js-test.yml` always
  runs `npm test`; `js-typecheck.yml` always runs `npm run typecheck`;
  `js-lint.yml` always runs `npm run lint`. A missing script fails the
  job loudly (npm: "Missing script").
- npm only in v1: `npm ci` fails loudly without a lockfile — and the
  setup-node dependency cache requires one (`package-lock.json` in the
  repository root). pnpm/yarn may be added later as additive inputs.

```yaml
jobs:
  js-test:
    uses: LuisArteaga/quality-gates-toolkit/.github/workflows/js-test.yml@v1.8.9
    with:
      node-version: "22"
```

The same scripts also ship as pre-commit hooks (`js-test`, `js-typecheck`,
`js-lint`) — see below. Composite consumers don't wire these workflows by
hand: `pr-checks.yml` orchestrates them via the `enable-js-*` toggles (see
[Language toggles](#language-toggles)).

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

**Fallback model** — `fallback_model` (per node, optional) is asked when the
configured `model` did not answer usably: its API retries were exhausted, its
answer was empty (including a cap-saturating one, which skips the retry), or
its answer was still unreadable after the one retry the answer contract gives
it. The fallback call carries the node's `max_tokens` but not its `routing`,
`options` or `temperature`; its answer is final and goes through the same
parser, so a fallback can never turn an unreadable answer into a PASS. When it
answers, the review body says so and the KPI table's Model column names it.
See D-0027.

`fallback_model` must name a model the provider actually serves — the toolkit
does not check the id against OpenRouter's model list, because resolution
stays offline (a transient API hiccup must not fail a review), so a
nonexistent id fails on the last attempt rather than being reported at
resolution. Two shapes *are* reported there: a malformed value (a non-string,
or an empty/blank string) warns and is ignored, leaving the node with no
fallback, and a value equal to `model` warns — it re-asks the same model,
differing only in the reset `routing`/`options`/`temperature` above, so it is
kept and the ladder still spends at most three calls, but the run log names
the no-op rescue path. See D-0028.

**Nested judge sections** — consumers whose `factory.json` also holds
non-judge sections (e.g. an orchestrator config) may nest the four judge
nodes under a section instead of the top level:

```json
{
  "other_tool": { "model": "...", "routing": null },
  "ci_cd_pr_judges": {
    "syntax_lint": { "model": "...", "routing": null },
    "test_coverage": { "model": "...", "routing": ["Provider A"] },
    "architecture": { "model": "...", "routing": null },
    "security": { "model": "...", "routing": null }
  }
}
```

Resolution scans top level first (flat configs behave exactly as before),
then the known section `ci_cd_pr_judges`. Consumers using different section
names declare them via the optional reserved top-level key
`"judges-section": ["my_judges", "ci_cd_pr_judges"]` — declared sections are
scanned before the known default. A nested hit is logged (`[INFO]` with the
section name); the verdict protocol is unaffected. See D-0015.

**Completion-token cap** — every judge request carries a bound on the
completion length, so a degenerate generation cannot burn a model's whole
output ceiling (observed: 131,072 tokens over ~23 min, empty content):

- `max_tokens` (per node, positive integer) — the request's completion
  ceiling. Reasoning tokens count against it. Consumers that omit it get
  the toolkit default **32768**: observed judge completions (reasoning
  included) on a ~2.2k-line diff run 0.8k–12.2k tokens, so the default
  keeps ~2.5x headroom while bounding a runaway call to minutes.
- Invalid values (`0`, negative, float, string) warn and fall back to the
  default — a malformed config value can never remove the bound. The
  resolved value is logged with the rest of the judge config.
- The value must fit the model's context: OpenRouter rejects a request
  whose prompt plus `max_tokens` exceeds the context length (HTTP 400,
  non-retryable). The 32768 default leaves room for the ~28k-token prompts
  the judges send even on a 128k-context model.
- The cap applies to **every** attempt including the fallback model: it is
  a latency/cost bound, not a routing or quality setting.
- An **empty** response that reached the cap skips the same-model nudge and
  goes straight to the fallback model (when one is configured): the
  cap-saturating empty generation *is* the pathology, so re-asking the same
  route is predicted to repeat it. A retry that is still empty takes the
  fallback path too (D-0027). A capped-but-non-empty response still evaluates
  normally, so truncation never silently corrupts a verdict.

See D-0021.

**Per-call wall-clock ceiling** — `urlopen`'s socket timeout bounds one
network operation, not one generation, so a slow provider can spend many
minutes on a single judge call while looking like ordinary latency
(observed: 564s for one legitimate verdict, and one pinned route past
48 minutes):

- `REVIEW_CALL_TIMEOUT_SECONDS` (default `300`) abandons a call that
  exceeds the ceiling, logs a distinct
  `[OPENROUTER] timeout model=… provider=… after=…s` line (`provider` is
  the *requested* route — a timed-out call reports no usage, so the serving
  provider is unknown), and retries it. The KPI table gained a **Timeouts**
  column, so a slow route is visible instead of hiding inside the Duration
  column.
- The retry **releases a pinned route**: `provider.order` is dropped and
  `allow_fallbacks` returns to OpenRouter's default. Pinned routing
  disables provider failover, so repeating it would re-enter the same slow
  provider — the reason a pinned judge can be 7x slower than the same
  model auto-routed. The **model is unchanged**: the verdict still comes
  from the model you configured, and the re-route is logged.
- The retry keeps the standard escalating schedule, and
  `REVIEW_RETRY_BUDGET_SECONDS` stays the outer bound of one call.
- The ceiling applies **per call**, so a many-batch judge (diff above
  `batch-budget-chars`) is additionally bounded by
  `REVIEW_RETRY_BUDGET_SECONDS` in total; batches left unevaluated make
  that judge NEEDS REVIEW rather than silently passing a truncated review.

See D-0022.

Judges also read the **caller's** `docs/context.md` and `docs/adr/*.md` (if
present) as architecture context — your documented decisions directly shape
the architecture verdict.

**Findings vs. observations** — a finding means "this must change before
merge", because any non-PASS verdict is merge-blocking (see
[Consuming verdicts](#consuming-verdicts)). The judge prompts state that
threshold explicitly: an item is reported as a finding only if the diff
shipped as-is would entitle a maintainer of the repository to block the merge
over it. Everything else stays in the judge's reasoning, optionally under a
`Minor observations` heading — a missing trailing newline at EOF, a
whitespace or formatting wobble, a naming preference, an optional suggestion,
or an observation the judge weighed and judged acceptable. `severity` is a
descriptive label only: the verdict is severity-blind, so `[NIT]`-style
reporting is a prompt contract, not a downgrade the gate performs (D-0023).
Prompt changes are versioned artefacts — a consumer that snapshots the judge
prompts verbatim must refresh that snapshot in the same release (see
[Versioning](#versioning)).

**The judge answer contract** — the verdict is read from the answer's
`<reasoning>` / `<findings>` blocks, so the shape of the answer decides what
the gate can do with it:

- An answer with an **empty `<findings>` block passes**: under the promotion
  threshold above, "no finding" is the normal passing answer (D-0023).
- The block has to be **presented as a block**: its open tag stands at the
  start of a line, or right after another tag — never inside a sentence. A
  tag written mid-sentence (or inside backticks) is the judge *describing*
  the format, not emitting it, so a quoted empty block does not pass and a
  quoted JSON example does not fail (D-0026).
- A block that is **present, non-empty and carries no readable finding**
  declares no findings *and* no pass: it is prose the judge wrote inside the
  tags, not a verdict, so it cannot pass on an empty parse (D-0026).
- An answer whose block is **missing or unreadable** is retried **once** with
  an instruction naming the block. A retry that is *still* unreadable is asked
  of the node's **fallback model** (when one is configured) rather than
  returned — an answer the engine cannot read is exactly the condition
  `fallback_model` exists for — and an answer that is unreadable on the
  fallback too leaves the judge NEEDS REVIEW. The retry and the fallback are
  each issued at most **once** per judge call, so the ladder spends at most
  three calls, all bounded by `REVIEW_RETRY_BUDGET_SECONDS` (D-0026, D-0027).
  A consumer with no `fallback_model` configured keeps the retry-only
  behaviour, unchanged and without error.
- A **finding written beside an explanatory sentence keeps failing** the
  answer with that finding: the rule is readability, not strict per-line
  format, so an unreadable line never discards a readable finding (D-0026).
- An **empty response** keeps its own path: one retry with an explicit
  instruction, then the fallback model when one is configured. A retry that is
  still empty reaches the fallback for the same reason an unreadable one does.
- When the fallback answered, the review body says so and names the model that
  produced the verdict, and the KPI table's **Model** column reports that
  model — so *the roster's model answered* and *the fallback rescued it* are
  distinguishable from the body alone (D-0027).
- The three ways a judge can end up with no verdict are reported distinctly:
  `Check failed to run: …` (the check raised), *Judge answer was not
  parseable (no readable `<findings>` block).* (the answer's shape), and
  *Insufficient context.* (the catch-all). See D-0026.

### Review-run environment variables

The workflows set these for you from the inputs above; when running
`review.py` manually, set them directly.

| Variable | Effect |
|---|---|
| `<NODE>_MODEL` (e.g. `SECURITY_MODEL`) | Per-node model override; highest precedence. |
| `AGENT_MODEL` | Global model override (above `factory.json`, below per-node). |
| `GH_TOKEN` | GitHub token the review posts with; `llm-pr-review.yml` sets it from `judge-token` or the caller's `github.token`. It is the only token variable the review reads — the origin project's legacy `GH_PAT` is reported and ignored (D-0005). |
| `REVIEW_CONFIG_PATH` | Judge config path; set from `config-path` (default `config/factory.json`). |
| `REVIEW_BATCH_BUDGET_CHARS` | Per-batch character budget for the judge diff; set from `batch-budget-chars` (effective default `200000`). |
| `REVIEW_RETRY_BUDGET_SECONDS` | OpenRouter retry budget in seconds before the run gives up (default `2700` = 45 min; retries are 429/5xx-aware, and it is also the total wall-clock bound of one multi-batch judge). |
| `REVIEW_CALL_TIMEOUT_SECONDS` | Per-call wall-clock ceiling in seconds (default `300`, must be a positive integer); a call exceeding it is abandoned and retried with a pinned route released (D-0022). |
| `REVIEW_DEBUG` | Set to `1` to log request payloads and error bodies. |
| `REVIEW_WORKSPACE_DIR` | Overrides the repository root the diff and docs context resolve against (default: `GITHUB_WORKSPACE/repo`). |
| `REVIEW_BODY_PATH` | Where a review body that could NOT be posted is written (default: `review_body.md` in the step's working directory). `llm-pr-review.yml` uploads exactly that file as the `llm-pr-review-body` artifact (D-0024). |
| `AGENT_LOG_PATH` | Overrides the local JSONL trace-log location (CI default: `agent_logs/` under the runner workspace; `/tmp/agent_logs` fallback). |

### Telemetry export (opt-in)

With the OpenTelemetry SDK installed and `OTEL_EXPORTER_OTLP_ENDPOINT`
(plus `OTEL_EXPORTER_OTLP_HEADERS` and/or `REVIEW_OTEL_API_KEY`) set, spans
are exported to your backend; `REVIEW_OTEL_PROJECT_NAME` and
`OTEL_SERVICE_NAME` label them. Without the SDK the tracer degrades to a
no-op. Details: [docs/context.md](docs/context.md).

## Importable judge API

The judge engine is an installable Python package
(`quality_gates_toolkit`, D-0017) — available from release v1.6.0 — so a
repo can calibrate its own evaluation tooling against exactly the code the
CI judges run, instead of vendoring a `review.py` snapshot that silently
drifts:

```bash
pip install "quality-gates-toolkit @ git+https://github.com/LuisArteaga/quality-gates-toolkit.git@v1.8.9"
```

```python
from quality_gates_toolkit.review import (
    SYSTEM_PROMPT_ARCH,
    SYSTEM_PROMPT_SECURITY,
    SYSTEM_PROMPT_SYNTAX_LINT,
    SYSTEM_PROMPT_TEST_COVERAGE,
    evaluate_response,
    load_architecture_context,
)
```

`evaluate_response` implements the hidden verdict-block protocol (D-0002, a
versioned public contract); `load_architecture_context` reads the same
`docs/context.md` / `docs/adr/*.md` context CI judges use. Support modules
(`telemetry`, `judge_config`, `redaction`, `enrichment`) live in the same
package; the judge-config resolution and env-var overrides documented above
apply identically when you drive the API directly.

**Why not `from scripts.review import ...`?** Both this toolkit and many
consumer repos ship a top-level `scripts` package, so an installed
`scripts.review` would be shadowed by the consumer's own package — the
distribution-named package is the collision-free import surface (D-0017).
`scripts` remains the console-script package (`secret-scan`) and carries
backward-compatibility shims that alias the moved modules; existing
`import review` / `from scripts.review import ...` code keeps working, but
new code should import from `quality_gates_toolkit`.

## Pre-commit hook

```yaml
repos:
  - repo: https://github.com/LuisArteaga/quality-gates-toolkit
    rev: v1.8.9            # pin a tag
    hooks:
      - id: secret-scan    # --staged scan of your staged changes
      - id: mypy           # runs YOUR environment's mypy (advisory)
      - id: semgrep        # pinned semgrep scan, isolated env
      - id: pip-audit      # pinned pip-audit, isolated env
      - id: js-typecheck   # full-project `npm run typecheck` (needs node_modules)
      - id: js-test        # full-project `npm test` (needs node_modules)
      - id: js-lint        # full-project `npm run lint` (needs node_modules)
```

Hook ownership split (D-0016) — version ownership follows dependency need:

| Hook | Language | Version ownership | Contract |
|---|---|---|---|
| `secret-scan` | `python` | toolkit-pinned | stdlib scanner in the isolated hook env; no consumer venv needed. |
| `mypy` | `system` | consumer-owned | runs `mypy` from your project environment; pass target paths via `args` (e.g. `args: ["src/"]`). |
| `semgrep` | `python` | toolkit-pinned (`semgrep==1.177.0`) | runs the toolkit's `semgrep-scan` wrapper — `semgrep scan` plus the ruleset-fetch retry (D-0025, see [Semgrep ruleset fetch](#semgrep-ruleset-fetch)); supply `--config` and paths via `args`. |
| `pip-audit` | `python` | toolkit-pinned (`pip-audit==2.10.1`) | supply arguments via `args` (e.g. `-r requirements.txt`). |
| `js-typecheck` / `js-test` / `js-lint` | `system` | consumer-owned | fixed npm scripts, full-project (see [JavaScript / TypeScript gates](#javascript--typescript-gates)). |

`language: python` hooks run in pre-commit's isolated environment with the
toolkit's pinned versions — the local mirror of the CI `lint.yml` pin
discipline; pin bumps ship as new toolkit releases. They also pin the env
interpreter (`language_version: python3.12`, the toolkit's floor): a bare
pre-commit installed under an older Python otherwise fails the env install
with `requires a different Python`. `mypy` is deliberately
`language: system`: type checking needs your project's dependency surface,
and an isolated environment would fail on every third-party import — the
same reason the js-* hooks run `npm` from your environment.

### Semgrep ruleset fetch

The `semgrep` gate behaves identically on both surfaces: the hook and
`security.yml` run the same wrapper (`scripts/semgrep_scan.py`), so one
policy covers local commits and CI (D-0025). The wrapper is `semgrep scan`
plus a bounded retry of the *configuration* load.

- **The ruleset is a live registry artifact.** `--config=auto` (the
  documented contract, and what `security.yml` passes) resolves its
  ruleset on `semgrep.dev` at run time — pinning the ruleset *name*
  (`p/default`, the current target of `/c/auto`) does not change that. A
  registry config updates on Semgrep's schedule, so a previously-green
  commit can gain findings with no code change: that is retroactive
  coverage working as intended, not a flake.
- **A failed fetch is not a finding.** The fetch is unauthenticated and
  can be rate-limited (observed: `HTTP 403`). Semgrep then exits 7 — the
  same code it uses for a genuinely invalid ruleset — so the wrapper
  retries that outcome twice, 2 s and 5 s apart, and reports each attempt
  on stderr as `[semgrep-scan] …`. The bound is small on purpose: a real
  configuration error costs a few extra seconds, while a transient one is
  gone. The retry does not depend on semgrep's message, because `--quiet`
  suppresses it while leaving the exit code at 7.
- **Nothing is hidden.** The wrapper replays semgrep's output verbatim and
  exits with the last attempt's status, so a failure that survives the
  retries reaches you unchanged (the same `exit 7`, the same diagnostics
  [Troubleshooting](#troubleshooting) describes).
- **Offline environments** pay the retry bound once and then fail as
  before: the gate is still closed, just delayed by ~7 s.

Two loud-fail paths to expect:

- **mypy hook errors before running** — your project environment lacks
  mypy (or isn't active). Install mypy plus your dependency surface there;
  remember the local hook is **advisory**: your local mypy version may
  differ from the CI pin, and `lint.yml`'s pinned mypy (2.3.1) remains the
  merge-gating authority.
- **pip-audit reports nothing** — bare `pip-audit` audits the (empty) hook
  environment, not your project. Pass explicit arguments, e.g.
  `args: ["-r", "requirements.txt"]`.

## Troubleshooting

- **A check shows as `Skipped`** — the gate is intentionally disabled, not
  failed or forgotten: GitHub renders a job turned off by its `if:` as
  `Skipped` (and reports Success for branch protection). The common case
  is a composite caller leaving the JS gate group off (D-0013);
  micro-workflow callers get a skip-free checks list by construction
  (D-0019).
- **The diff-coverage gate fails: "never imported by any test (absent from
  report)"** — a newly added top-level package is not measured. Add it to
  `cov-paths`; the gate judges only lines that appear in the coverage
  report.
- **mypy fails on every third-party import** — the caller project and its
  dependency surface were not installed. Make the project pip-installable
  and pass non-dev dependencies via `extra-pip-packages`; the lint
  environment mirrors the test environment (`pip install -e ".[dev]"`).
- **test job fails with a missing `pytest` or missing test imports** — your
  `[dev]` extra is missing or incomplete; pip only *warns* about a missing
  extra and installs the rest.
- **Judges falsely report "tests missing" on a large PR** — the diff was
  split into batches and the tests landed in a different batch than the
  changed files. Raise `batch-budget-chars` (e.g. `500000`).
- **Review job red, but the posted review carries no findings** — the judge
  transport failed (typically OpenRouter HTTP 429). Retries consume
  `REVIEW_RETRY_BUDGET_SECONDS` (default 45 min); rerun the failed job once
  quota resets. A review *with* findings is a real verdict, not an outage.
- **A judge reports "Judge answer was not parseable (no readable
  `<findings>` block)"** — that node's model answered without a readable
  block its prompt requires: either the block is missing, or what it put
  inside the tags carries no JSON finding (prose, or a block it only
  *mentioned* in a sentence). The one retry did not fix it, and either the
  node has no `fallback_model` configured or the fallback answered unreadably
  too. Nothing is wrong with the diff: re-run the job (the answer is a
  per-call sample) or change that node's model/provider in the judge config,
  since format adherence is model- and provider-specific (D-0026, D-0027). The
  full answer, and which model produced it, is in the judge's reasoning block
  and the body's fallback notice.
- **Review job red and no review was posted at all** — the submission was
  refused (a token that cannot review, a repository policy, a PR that
  vanished). The verdicts are not lost: the job log carries the full body,
  the run uploads it as the `llm-pr-review-body` artifact, and the checks UI
  shows a `LLM judge verdicts: …` annotation. Nothing was posted, so a rerun
  cannot double-post (D-0024).
- **A judge takes tens of minutes** — a slow provider route. The call is
  abandoned at `REVIEW_CALL_TIMEOUT_SECONDS` (default 300s) and retried on
  the auto route; the KPI table's **Timeouts** column shows how often that
  happened. Lower the knob if a route is consistently slow, or drop the
  pinned `routing` for that node so OpenRouter can fail over per request.
- **Semgrep failed with `exit code 7` and no message** — the ruleset could
  not be loaded (a transient registry failure), not a problem with your
  rules. The wrapper retried twice before reporting it; a re-run usually
  passes — see [Semgrep ruleset fetch](#semgrep-ruleset-fetch). If you
  passed `--quiet` in the hook `args`, drop it to see semgrep's own
  `[ERROR] Failed to download configuration …` line.
- **secret-scan false positive** — there is deliberately no inline
  suppression (a consumer-side skip mechanism would weaken the scanner).
  Token-shape fixes (e.g. the npm integrity-hash suppression, D-0010) ship
  in the scanner itself — open an issue with the matched text shape.
- **Lockfile churn dominates the judge diff** — pass
  `diff-exclude: "uv.lock package-lock.json"`.

## Consuming verdicts

The posted review carries the hidden `llm-pr-review-verdicts` block — a
versioned public contract specified in [`DECISIONS.md`](DECISIONS.md)
(D-0002). For merge gating:

- The review exits nonzero on any FAIL / NEEDS REVIEW verdict, so the red
  check alone is a merge gate — make the check required in branch
  protection. Check names derive from the caller's job ids and the called
  workflow names; the toolkit's own `ci.yml` is a live example.
- To automerge on verdicts, parse the hidden block (an HTML comment in the
  review body). Verify the review author against a trusted judge identity —
  without `judge-token` that author is `github-actions[bot]`, so pass a PAT
  only if your automerge loop pins a *different* trusted identity (see
  [Secrets](#secrets)); that check is the reason `judge-token` exists.
- A red run does not always mean a posted review: when the submission was
  refused, the same body is still retrievable from the `llm-pr-review-body`
  artifact (uploaded only on that path, D-0024) and the checks UI annotates
  the verdicts. So `has_review: false` distinguishes "judged but not
  delivered" from "not judged yet" — read the artifact before treating the
  run as opaque.

## Versioning

- `uses:` pins an immutable release tag (e.g. `@v1.8.9`); `toolkit-ref`
  (default = that same tag) selects the toolkit's Python-artifact checkout
  — secret scanner, judges, and the Semgrep wrapper `security.yml` runs.
  Overrides are deliberate.
- The `pyproject.toml` version field tracks the same release train (bumped
  together with the toolkit-ref pin sites in each release PR) and names the
  tag pip consumers install for the [importable judge API](#importable-judge-api).
- Each tagged release is documented in [`CHANGELOG.md`](CHANGELOG.md) (Keep a
  Changelog format) and mirrored into a matching
  [GitHub Release](https://github.com/LuisArteaga/quality-gates-toolkit/releases);
  creating that release page is the final step of the release checklist
  (D-0018). The checklist's first step is a manual dispatch of the
  composite canary (D-0019), so a broken composite cannot reach a tag
  unexercised.
- Public contracts (verdict-block format, gate ordering, routing modes,
  defaults) are recorded in [`DECISIONS.md`](DECISIONS.md) and only change
  with a new major ref.
- Third-party actions are SHA-pinned; lint tool versions are pinned in
  `lint.yml` and bump with toolkit releases.

## Known limitations

- Fork PRs cannot access repository secrets; run the LLM review only for
  same-repo PRs (both gating patterns under [Fork PRs](#fork-prs)).
- Enabling `enable-llm-review` on more than one composite in the same PR
  produces two full judge reviews (double cost, two verdict blocks on one
  thread). Keep it enabled in exactly one composite per PR (D-0020).
- The toolkit's own CI passes its PR head SHA as `toolkit-ref` — that
  checkout target does not exist for fork PRs.
- `pull_request_target` is deliberately not offered as a fork workaround
  (it would check out and run untrusted PR code with secrets).
- A judge that *illustrates* the format with a correctly shaped block of its
  own — line-delimited JSON whose tags stand at a block boundary, fenced or
  not, with no verdict block anywhere in the answer — is still read as its
  verdict, because that answer is structurally identical to one. The
  mid-sentence variant is not read (a tag inside a sentence is not a block),
  and no deterministic rule can separate a well-formed illustration from a
  verdict (D-0026).

## License

MIT — see [LICENSE](LICENSE).
