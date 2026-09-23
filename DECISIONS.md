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

- 2026-09-08: "Deterministic gates ON" is scoped by the language contract
  (D-0013): it applies to the Python gate group. The JS gate group
  (`enable-js-*`) defaults OFF — enabling it requires a `package-lock.json`
  in the caller root (D-0012), so defaulting it ON would break every
  Python-only caller on upgrade. Opting in is the JS consumer's deliberate
  act.

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
- 2026-09-23 (#43): Identity-less (installation-token) submission no longer
  uses the verdict-derived action. GitHub forbids installation tokens from
  approving pull requests outright — the repository setting "Allow GitHub
  Actions to create and approve pull requests" governs it, independent of
  authorship — so the all-PASS path submitted `--approve`, was refused, and
  ended as a permanently red judge job with the verdicts lost (no review
  body, hence no verdict block for consumer automation to parse).
  Installation tokens now always submit `--comment`; `--approve` requires a
  user-identity token that is not the PR author; FAIL / NEEDS REVIEW still
  maps to `--request-changes` for user tokens. The 2026-09-05 rationale
  conflated the approve prohibition with the server-side self-approval
  rejection — they are different rules with different scopes. The
  exit-code gate (any FAIL / NEEDS REVIEW ⇒ exit 1) is unchanged and
  remains the merge gate.

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

## D-0011 — LLM review runs only on pull_request events

- Date: 2026-09-06
- Status: Accepted

### Decision

The composite's `llmreview` job carries a
`github.event_name == 'pull_request'` guard in addition to the
gate-ordering conditions. A caller that triggers the composite on other
events (e.g. `push`) never runs the judges — the job skips.

### Rationale

Judges without a pull request to review are meaningless: the judge
workflow fail-fasts on an empty PR number, so a push-triggered caller with
`enable-llm-review: true` had a permanently red judge job on every push to
the default branch — a broken pipeline signal with zero review value
(observed as a trap on a consumer repository, 2026-09-07). The guard keeps
the composite's contract that consumers pass repo-specific *values* while
the toolkit owns the *policy* invariants; direct micro-workflow callers
keep the loud fail-fast as their diagnostic.

## D-0012 — JS gates: the harness owns the environment, the project owns the tools

- Date: 2026-09-08
- Status: Accepted

### Decision

The JavaScript/TypeScript gates (`js-test.yml`, `js-typecheck.yml`,
`js-lint.yml`) fix the harness and the script contract — nothing else. The
toolkit owns the Node runtime (`node-version` input, default `22`), the
checkout, `npm ci`, and the npm dependency cache. The consumer owns every
tool: test runner, TypeScript, ESLint, and all their configuration live in
the caller's `package.json`. The script contract is fixed in v1 —
`js-test.yml` always runs `npm test`, `js-typecheck.yml` always runs
`npm run typecheck`, `js-lint.yml` always runs `npm run lint`, with no
command inputs. Package-manager support is npm only: `npm ci` fails
loudly without a lockfile, and the setup-node dependency cache requires
one too. The same scripts ship as `language: system` pre-commit hooks
(`js-test`, `js-typecheck`, `js-lint`) that run full-project, not
staged-scoped.

### Rationale

The Python gates pin their tools in the toolkit (ruff/mypy versions in
`lint.yml`) because the toolkit itself defines those gates' semantics. A
JS project's toolchain is consumer identity, not plumbing: jest, vitest,
mocha, and tsc configurations differ per project, and the toolkit would
serve nobody by picking winners. The fixed script names give the toolkit a
stable interface — the npm equivalent of a Makefile target contract — and
a missing script fails the job loudly via npm's own "Missing script" error,
keeping diagnostics in the consumer's vocabulary. npm-only v1 follows
D-0009's scope minimalism: pnpm/yarn can be added later as additive
`workflow_call` inputs without breaking existing callers, while a
mis-chosen default package manager would break every caller from day one.
The new workflows take no `toolkit-ref`: like `lint.yml`, they run no
Python implementation checkout. Deferred by the same scope rule: JS
diff-coverage (needs a cobertura artifact design); composite language
toggles shipped as D-0013.

### Amendments

- 2026-09-08: The deferred ESLint gate ships as `js-lint.yml` under the
  unchanged harness contract — fixed script `npm run lint`, matching
  `language: system` hook `js-lint`. The deferral's consumer premise still
  holds: the gate becomes runtime-verifiable once the first npm consumer
  adopts ESLint with a `lint` script; until then its contract is enforced
  statically by the workflow contract tests.
- 2026-09-08: The deferred composite language toggles ship as D-0013 —
  the micro-workflow harness contract itself is unchanged; the composite
  gains `enable-js-*` toggles (default OFF) that call these workflows.

## D-0013 — Composite language contract

- Date: 2026-09-08
- Status: Accepted

### Decision

The composite's gate toggles are language-scoped. The pre-existing
unprefixed toggles (`enable-lint`, `enable-test`, `enable-security`,
`enable-secret-scan`, `enable-diff-gate`, plus the security sub-toggles)
govern the **Python gate group** and keep their ON defaults. The JS gate
group is controlled by three new toggles — `enable-js-lint`,
`enable-js-test`, `enable-js-typecheck` — which default **OFF**. A
composite-level `node-version` input (default `22`) is forwarded to all
three JS jobs, mirroring how `python-version` feeds the Python gates. The
D-0001 ordering policy spans both groups: the LLM review runs only after
every enabled deterministic gate of either language is green.

### Rationale

Renaming the unprefixed toggles to `enable-python-*` (the shape sketched in
the originating issue, "naming TBD") would be an incompatible contract
change — GitHub declares no input aliases, so every caller passing the old
names would fail validation — and per this file's header such a change
requires a new major ref. Keeping the historical names and adding an
explicitly language-prefixed JS group preserves every existing caller
byte-for-byte. The JS defaults are OFF because the JS harness fails loudly
without a `package-lock.json` (D-0012): defaulting ON would turn every
Python-only caller red on its next toolkit bump, violating the
backward-compatibility requirement. As with the ESLint gate (D-0012
amendment), the Python-only toolkit repository cannot runtime-exercise
`npm ci` gates; the JS micro-workflows are byte-unchanged by this decision,
so the composite's JS path takes its first runtime canary when the first
consumer enables the toggles — until then the contract is enforced
statically by the workflow contract tests.

### Amendments

- 2026-09-12 (D-0020): the toggle contract remains valid for the polyglot
  composite, but composites are the scaling answer that came after — each
  language group now also ships a dedicated `<lang>-checks.yml` whose own
  gates default ON (the caller chose that language's entry point), and
  new languages add a new composite file instead of growing
  `pr-checks.yml`.

## D-0014 — Python install contract: lint is best-effort, test is strict

- Date: 2026-09-08
- Status: Accepted

### Decision

The two Python gates install the caller project with deliberately
different fallback behavior:

- `lint.yml` installs `pip install -e ".[dev]" || pip install -e .`. The
  fallback keeps lint working when the `[dev]` extra is absent or
  unresolvable: the lint toolchain (ruff, mypy) is installed by the
  toolkit itself, so a runtime-only caller install still yields a viable
  mypy environment.
- `test.yml` installs `pip install -e ".[dev]"` with no fallback. pytest
  is not toolkit-provided — it must come from the caller's `[dev]` extra,
  so a runtime-only fallback cannot rescue the job.

This records lint.yml's existing behavior as public contract; it is
pinned by workflow contract tests and documented in the README's Caller
prerequisites section.

### Rationale

Mirroring lint's fallback into test.yml would be dead code: pip treats a
missing `[dev]` extra as a warning and exits successfully either way
(verified: `pip install --dry-run -e ".[does-not-exist]"` → warning,
exit 0), so both shapes fail later at the pytest step — the fallback
would change the failure site, not the outcome. Removing lint's fallback
would hard-fail lint-only callers whose dev extra is broken, for no
benefit: lint's own tools are toolkit-pinned and the runtime install
supplies mypy's dependency surface. The asymmetry tracks toolchain
ownership — each gate installs exactly what the caller must provide for
it.

### Amendments

None.

## D-0015 — Nested judge-config sections: additive union resolution

- Date: 2026-09-08
- Status: Accepted

### Decision

`judge_config.resolve_model_config` resolves a judge node's config as a
union with fixed precedence:

1. Top-level `factory[node_name]` — unchanged v1.3.0 behavior, still first.
2. On a top-level miss: bounded nested-section scan. Sections scanned are
   the known default `ci_cd_pr_judges` plus any additional section names
   the consumer declares under the reserved top-level key
   `"judges-section"` (list of strings, scanned before the known default,
   deduplicated). The first section holding the node wins.
3. Env overrides and the `DEFAULT_MODEL` fallback as before, unchanged.

The scan is bounded to known/declared section names — never a generic
dict-of-dicts walk. A nested hit logs `[INFO]` with the section name; a
malformed `judges-section` declaration warns and is ignored. No accepted
key is renamed or removed in 1.x (deprecation-by-warning only), and the
verdict-block protocol (D-0002) is untouched.

### Rationale

The first nested consumer (agentic-planner-core) co-locates orchestrator
sections (`cli_orchestration`, `refine_graph_nodes`) with its judge section
in one `factory.json`; before this change every judge silently fell back to
`DEFAULT_MODEL`. A generic scan of every top-level dict-of-dicts would risk
silently resolving a judge from an unrelated section whose node names
collide, so the scan is bounded to known/declared names — deterministic,
documentable, and reproducible. Flat consumers keep byte-identical
resolution (golden contract tests pin the shipped example config's
resolution, and the review body / verdict block is independent of config
sourcing), so the change is purely additive and needs no major ref.

The optional `judges-section` declaration key ships deliberately despite
having no current consumer with non-standard section names: it is part of
this decision's contract (issue #24's design), and contract keys are
cheapest to introduce while the nested-resolution contract is young —
before external consumers accumulate and a later addition becomes another
additive release nobody asked for. Removing it later would be the 1.x
deprecation path, not this.

### Amendments

None.

## D-0016 — Local Python hook ownership split

- Date: 2026-09-11
- Status: Accepted

### Decision

The toolkit's local (pre-commit) hooks split by version ownership, which
follows the tool's dependency need:

- `language: python` — tools with no dependency on the consumer project
  run in pre-commit's isolated environment at toolkit-pinned versions:
  `secret-scan` (console script from this package, existing) plus new
  `semgrep` (`semgrep==1.177.0`, entry `semgrep scan`) and `pip-audit`
  (`pip-audit==2.10.1`, entry `pip-audit`). Pin bumps ship as new toolkit
  releases, never on a floating ref. These hooks also pin the env
  interpreter with `language_version: python3.12` (the package's floor):
  pre-commit otherwise builds the isolated env with the interpreter it
  itself runs under, which can be older and would fail the env install
  with `requires a different Python` (observed with a uv-managed
  pre-commit running under 3.11).
- `language: system` — tools that must see the consumer project's own
  dependency surface run from the consumer's environment at consumer-owned
  versions: the `js-*` npm hooks (existing) plus new `mypy` (entry `mypy`,
  consumer passes target paths via its own `args`). Local mypy is
  **advisory**; the CI `lint.yml` pin (mypy 2.3.1) stays authoritative.
- `ruff` ships no hook: the upstream `astral-sh/ruff-pre-commit` hook is
  canonical and fast-moving; consumers keep using it directly and pin its
  rev to match their CI.

### Rationale

pre-commit's `language: python` builds an isolated environment that does
not see the consumer's installed dependencies — a toolkit-pinned mypy
there would fail on every third-party import, the same failure class that
keeps the `js-*` hooks on `language: system` (ESLint in an isolated env
cannot see project plugins). Conversely, dependency-free scanners gain
exactly the consistency the CI side already promises ("a consistent,
known-good toolchain per toolkit-ref") with zero consumer setup. The split
generalizes D-0012's "the harness owns the environment, the project owns
the tools" from a JS-only rule to a dependency-need rule: the toolkit pins
what is self-contained, the consumer owns what needs its project. The
advisory/authoritative split for mypy keeps one source of merge-gating
truth (CI) while local hooks give fast feedback. The observed consumer
skew this guards against (a consumer pinning ruff-pre-commit v0.3.0
against toolkit CI's ruff 0.16.6) is the failure class the pinned isolated
envs eliminate for the self-contained tools.

### Amendments

None.

## D-0017 — The judge API ships as the importable `quality_gates_toolkit` package

- Date: 2026-09-11
- Status: Accepted

### Decision

The judge engine and its dependency closure (`review.py`, `telemetry.py`,
`judge_config.py`, `redaction.py`, `enrichment.py`) move from the top-level
`scripts` package to a new importable package named after the distribution,
`quality_gates_toolkit`, with package-relative imports and no `scripts.*`
imports anywhere inside it. The `scripts` package shrinks to its original
purpose — the console-script home (`secret-scan` entry point,
`diff_coverage_gate.py`, `secret_scan.py`) — plus five backward-compatibility
shims that replace themselves in `sys.modules` with
the moved implementation modules. The shim guarantees identity, not
re-exported copies: flat imports (`import review`), qualified imports
(`from scripts.review import ...`), direct execution
(`python3 scripts/review.py`, the llm-pr-review invocation), and
`mock.patch("review.<name>")` targets all bind the same module object.
Consumers calibrate against `quality_gates_toolkit.review`; the public
judge-prompt literals live in exactly one place. `pyproject.toml` ships
both packages and its version field now tracks the release train (bumped
together with the toolkit-ref pin sites in each release PR; the field
previously stayed at 1.0.0 while tags advanced to v1.4.0).

### Rationale

Cross-repo consumers cannot use the judge engine as-is: both this toolkit
and consumers (e.g. agentic-planner-core) ship a top-level package named
`scripts`, so pip-installing this distribution and importing
`scripts.review` would resolve the consumer's own package — a pip
dependency is unusable, and the workaround (vendoring a snapshot of
`review.py`) silently drifts from what CI actually runs. Moving the judge
closure under a distribution-named package removes the collision at the
root: package-relative imports mean the implementation never resolves
through the `scripts` name, so a consumer's local `scripts/` package is
irrelevant to it. The whole closure moves (not just the four prompt
constants) because a partial extraction would still drag `scripts.*`
imports into the consumer's import graph. A narrower namespace (`qg_judge`)
was rejected: the API is the toolkit's Python implementation, not a
separate distribution, and the distribution-named package avoids inventing
a second public brand. The shim form was chosen over an explicit
re-export list because re-export copies break `mock.patch` — patching the
shim's attribute would not reach the implementation's callers — and would
drift with every new private name; `sys.modules` self-replacement is
honored by CPython's import machinery (the final `sys.modules` entry wins
after `exec_module`, and the parent package attribute is set from it),
which is the same mechanism Pillow uses to keep `import PIL` working.

### Amendments

None.

## D-0018 — Release notes: CHANGELOG.md and GitHub Releases from one entry

- Date: 2026-09-12
- Status: Accepted

### Decision

Every tagged release publishes two consumer-facing change surfaces derived
from one authored entry: a Keep a Changelog-format section in the root
`CHANGELOG.md`, written in the same release PR that bumps the toolkit-ref
pin sites, and a GitHub Release created for the tag whose notes body mirrors
that section verbatim. Creating the GitHub Release is the final step of the
release checklist (tag → release page). The changelog is the portable
surface — it travels with the tree, so `git show <tag>:CHANGELOG.md`
documents any pinned version; the Releases are the GitHub-native surface —
watch notifications, `.github/release.yml` note generation, and Dependabot's
release-notes embedding in consumers' reusable-workflow update PRs.

### Rationale

The toolkit's consumers pin immutable tags and bump pins manually (often via
Dependabot), so releases must be discoverable where those consumers look:
GitHub's own features key off Releases, while the broader ecosystem
convention (Keep a Changelog; Renovate reads both surfaces) keys off a root
`CHANGELOG.md`. Authoring both from one entry avoids the drift class this
repo already guards against (per-release pin-site sweeps exist because
duplicated version facts rot). Full release automation via release-please
was rejected for now: it would displace the curated release-PR process
(scripted per-release pin-site bumps with expected-count asserts).
`DECISIONS.md` stays the ADR-lite decision log — decisions, not release
changes — so it is neither renamed nor merged with the changelog.

### Amendments

None.

## D-0019 — Self-test harness on micro-workflows; composite verified by a scheduled canary

- Date: 2026-09-12
- Status: Accepted

### Decision

The toolkit's own `ci.yml` calls the micro-workflows directly (`lint`,
`test`, `security`, `secretscan`, then the judges gated by the fork guard
and the D-0001 needs ordering) with the PR head SHA as `toolkit-ref` — it
is a self-test harness against the PR commit and deliberately loses the
"reference consumer" role: no real consumer can reference the PR commit,
so the self-caller was always a harness, not an exemplar. The composite
keeps GitHub-side runtime verification through a new `composite-canary.yml`
that triggers ONLY on `schedule` (nightly) + `workflow_dispatch`, runs the
composite from the default-branch tip (`toolkit-ref: github.sha`) with the
LLM review disabled, and is dispatched manually as the first step of the
release checklist so a broken composite cannot reach a tag unexercised.
Composite positioning is unchanged for Python-only and polyglot callers;
single-language non-Python repositories are steered to the micro-workflow
path, which is skip-free by construction. A sandbox consumer repository
remains the designated future upgrade path for pre-merge and JS-path
verification (recorded, not built here).

### Rationale

GitHub renders jobs statically: a job disabled by `if:` — including a job
that calls a reusable workflow — always appears as `Skipped` in the checks
list (it reports Success for branch protection), and no upstream mechanism
hides it (community discussions 44490 and 72708, both open). With the JS
gate group defaulting OFF (D-0013), every toolkit PR therefore wore three
permanent `Skipped` checks. Calling the micro-workflows directly removes
that noise from the toolkit's own checks list; the canary restores the
composite's exercise without touching any PR checks list (a PR-event
canary would reintroduce duplicate checks) and never spends judge tokens
on an unattended schedule. The pre-tag dispatch also covers dormancy:
GitHub auto-disables scheduled workflows in a public repository after 60
days without repository activity (warning email to the last editor well
before that), and a disabled workflow cannot be dispatched — so a broken
or disabled composite surfaces before a tag is cut. The canary's
permission shape follows the first composite consumer's finding: the
caller must grant `pull-requests: write` even though the judge job is
skipped, because escalation is validated statically before jobs start
(ot-telemetry-engine PR #62). The cost gate (D-0001) needs no
success-or-skipped clauses in `ci.yml` — it has no gate toggles, so the
default `needs` success semantics already demand every deterministic gate
green before the judges start.

### Amendments

None.

### Inspiration & References

- [community/44490](https://github.com/orgs/community/discussions/44490)
  and [community/72708](https://github.com/orgs/community/discussions/72708)
  — no native path/profile-aware handling for reusable-workflow jobs; the
  skipped-check rendering is unavoidable, so the fix must be caller-side
  topology.
- [community/171037](https://github.com/orgs/community/discussions/171037)
  — the sandbox-consumer pattern (enterprise pre-merge verification of
  shared workflows), recorded as the future upgrade path.
- [OpenAstronomy/github-actions-workflows](https://github.com/OpenAstronomy/github-actions-workflows)
  — per-workflow catalog without an orchestrator; precedent for
  single-responsibility workflows and the README decision matrix.
- [GitHub blog: using reusable workflows](https://github.blog/developer-skills/github/using-reusable-workflows-github-actions/)
  — decision-table documentation for overlapping mechanisms.
- ot-telemetry-engine PR #62 — empirical permission-validation finding
  (static escalation check even for skipped composite jobs).

## D-0020 — Per-language composites; new languages add files, not polyglot toggles

- Date: 2026-09-12
- Status: Accepted

### Decision

Every language group ships a dedicated composite entry point named
`<lang>-checks.yml`. The first two are `python-checks.yml` (lint, test,
security with its sub-toggles, secretscan, diffcoverage, optional
llmreview) and `js-checks.yml` (js-lint, js-test, js-typecheck, optional
secretscan, optional llmreview). Each NEW language ships a NEW composite
file instead of growing `pr-checks.yml`; the polyglot composite stays
as-is for this release line — existing consumers keep working
byte-for-byte, and removal, if ever, is a v2 major decision (D-0007).

Each language composite restates the D-0001 ordering policy internally
(accepted trade-off: the `needs:` chain is duplicated per file in
exchange for zero skipped-check noise for monolingual callers) and
carries the D-0011 pull_request guard, the D-0005 explicit secret
forwarding, and a `toolkit-ref` input defaulting to the release tag
(same-tag relative-ref resolution, as in the polyglot composite). A
language composite defaults its OWN language's gates ON — the caller
chose that entry point — inverting the polyglot's JS-OFF default;
`enable-llm-review` defaults false and `enable-secret-scan` is true on
both. No coverage/diff-coverage job exists on the JS composite: the JS
harness owns the environment (D-0012) and has no coverage.json artifact
contract.

A standalone "LLM composite" is deliberately rejected: GitHub Actions
`needs:` cannot cross workflow boundaries, so a judge workflow triggered
independently cannot structurally wait for another composite's
deterministic gates — it would have to poll check-run state instead
(observational gating), which duplicates the merge gate's fail-fast
logic, adds latency, and is race-prone (check runs register late; a poll
can read the previous run's results). D-0001 is structural, not
observational. Instead, each language composite embeds an optional
`llmreview` job behind `enable-llm-review` (default false), and the
standalone judge surface (`llm-pr-review.yml`) remains available to
callers who want to own the ordering themselves — exactly as the polyglot
composite calls it internally.

The exactly-one rule: a repository wiring multiple language composites on
one PR must enable `enable-llm-review` (and any other language-agnostic
gate it keeps enabled in more than one composite, i.e.
`enable-secret-scan`) in EXACTLY ONE composite. The judge reviews the
whole PR diff regardless of which composite invokes it, so one review per
PR is both sufficient and cost-correct; two enabled reviews mean double
cost and two verdict blocks. The v1 mitigation is the documented rule — a
static guard is impossible across workflow files, and runtime
duplicate-detection (the judge noticing an existing verdict block from
the same event) is a candidate follow-up, not in scope.

### Rationale

The polyglot composite's toggles (D-0013) scale badly in the checks list:
jobs are static, so every disabled gate renders as a permanent `Skipped`
entry (the D-0019 finding) — a toolkit with ~100 gates across many
languages would flood every caller's checks list. Per-language composites
keep the one-`uses:` ergonomics while making monolingual callers
skip-free by construction; the D-0013 toggle contract remains valid for
the polyglot shape (amended below). Embedding the judge per composite
rather than adding a third workflow keeps the cost gate structural.
Check-run naming is unchanged (the caller's job id prefixes the
composite's job ids, e.g. `ci / lint / lint`), so branch-protection
required-check sets configured against the polyglot composite keep
working when a caller switches to a language composite under the same
caller job id. First runtime canary: per D-0019 the toolkit's own CI
stays a micro-workflow self-test harness, so the new composites are
contract-tested statically and take their first runtime exercise with
the first consumer — the same pattern the JS gates shipped under (their
runtime canary was the first npm consumer).

### Amendments

None.

### Inspiration & References

- [community/26632](https://github.com/orgs/community/discussions/26632)
  — job dependencies cannot cross workflow-file boundaries ("You can't.
  What you can do is use the same workflow file"); the structural basis
  for embedding the judge per composite instead of a standalone LLM
  workflow.
- [community/44490](https://github.com/orgs/community/discussions/44490)
  and [community/72708](https://github.com/orgs/community/discussions/72708)
  — static `Skipped` rendering of `if:`-disabled jobs; the checks-list
  noise this decision removes for monolingual callers.
- ot-telemetry-engine PR #62 — static permission validation fires even
  for skipped composite jobs; the language composites' llmreview
  permission shape follows the same caller contract.

## D-0021 — Judge requests carry a bounded completion cap

- Date: 2026-09-23
- Status: Accepted

### Decision

Every judge request sent to OpenRouter carries `max_tokens`:

1. **Wiring.** The resolved `max_tokens` travels the whole call chain
   (`resolve_model_config` → `call_llm_for_review` → `_run_layered_retry` →
   `_call_with_api_retry` → `call_openrouter_api` → `build_payload`) and is
   serialized into the request payload. It is placed in the payload before
   the `options` merge, which stays last-wins — the documented escape hatch
   keeps its existing semantics.
2. **Default at the resolution boundary.** `resolve_model_config` applies
   `DEFAULT_MAX_TOKENS = 32768` when the consumer config omits `max_tokens`,
   so every resolved config carries a positive integer. The effective value
   is part of the `[INFO] Resolved judge config` log line. This is the
   single boundary: the transport stays a pure builder that omits the key
   when no cap was resolved.
3. **Validation.** A configured value must be a positive integer; `0`,
   negatives, floats, strings, and booleans warn and fall back to the
   default. A malformed config value can never remove the bound.
4. **Fallback persistence.** The cap persists on the fallback attempt.
   ADR-0021-lineage resets (`routing=None`, `options=None`,
   `temperature=0.0`) are model/route-selection concerns; `max_tokens` is a
   request-level latency/cost bound, so it applies regardless of which
   model serves the call — and the fallback model may have a larger
   ceiling.
5. **Cap-saturated empty responses skip the same-model nudge.** When the
   response content is empty AND the reported `completion_tokens` reached
   the cap, the ladder goes straight to the fallback model (when one is
   configured) instead of re-prompting the same model on the same route.
   Without a fallback, the empty body is returned unchanged and maps to
   `NEEDS REVIEW` as before. A capped-but-NON-empty response is not a
   saturation: a truncated verdict that still carries
   `<reasoning>`/`<findings>` evaluates normally, so truncation never
   silently corrupts a verdict.

### Rationale

A judge emits a structured verdict (`<reasoning>` + `<findings>`); 131,072
completion tokens is never legitimate work. On
LuisArteaga/speakdatawith.com PR #55 (Quality Gates run 35002979361) the
`syntax_lint` attempt 1 produced exactly that: the model's maximum output
over 1401.8 s, empty content, $0.0465 (~38% of the run's judge cost), and
the empty-content nudge then succeeded in 64.5 s on retry. Nothing in the
request bounded the generation: `max_tokens` was parsed by
`resolve_model_config` but never sent, so the provider's own default
applied — and `urlopen(timeout=300)` bounds individual socket operations,
not one generation, while `REVIEW_RETRY_BUDGET_SECONDS` governs retry waits
rather than a single call.

32768 is chosen from evidence, not taste: observed judge completions
(reasoning tokens included, since they count against the cap) on a
~2.2k-line diff ran 0.8k–12.2k tokens, so the default keeps ~2.5x headroom
for the largest legitimate verdict while bounding a runaway generation to
minutes instead of tens of minutes. Making the value a config knob keeps
the decision with the consumer whose diffs are unusually large.

Applying the default at the resolution boundary has one visible cost: the
flat-config golden contract ("resolves byte-identically to v1.3.0") gains
a single intentional exception, since a config omitting `max_tokens` now
resolves to 32768 rather than `None`. That is the point of the decision —
`None` meant "unbounded", which is the defect — and the golden test pins
the new value instead of hiding it.

The `>=` comparison (rather than the AC's "==") is deliberate: OpenRouter
reports `completion_tokens` that can slightly exceed the requested
`max_tokens` (their own example: `max_tokens: 300` → `completion_tokens:
302`), so an equality test would miss the pathology at the boundary.

### Amendments

None.

### Inspiration & References

- [OpenRouter API parameters — Max Tokens](https://openrouter.ai/docs/api_reference/parameters)
  — "upper limit for the number of tokens the model can generate in
  response"; also that an absent sampling parameter is omitted upstream
  rather than replaced by a hardcoded value, so the unbounded request ran
  on the provider default.
- [OpenRouter — Reasoning Tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)
  — `max_tokens` covers reasoning and visible output together; a limit
  consumed by reasoning returns `finish_reason: "length"` with empty
  `content` (the exact pathology this decision detects), and the worked
  example `max_tokens: 300` → `completion_tokens: 302` motivating the
  `>=` test. Also documents the hard upper bound (context length minus
  prompt length) and that Anthropic models require `max_tokens` to be
  strictly above the reasoning budget.
- Issue #38 and its evidence (speakdatawith.com PR #55 review KPIs,
  Quality Gates run 35002979361) — the runaway call, its cost share, and
  the successful nudge retry.
- [community.openai.com — `max_tokens` semantics](https://community.openai.com/t/why-was-max-tokens-changed-to-max-completion-tokens/938077)
  — the `max_tokens` vs `max_reasoning_tokens` split in the wider
  ecosystem; rationale for bounding the request rather than the reasoning
  budget (a per-model reasoning knob cannot bound untagged models).

## D-0022 — Judge calls carry a per-call wall-clock ceiling, and a timeout retry releases a pinned route

- Date: 2026-09-23
- Status: Accepted

### Decision

Every OpenRouter judge call is bounded in wall-clock terms, and a call that
exceeds that bound is retried with its provider route released:

1. **Ceiling.** `REVIEW_CALL_TIMEOUT_SECONDS` (default `300`) caps one
   transport call. The transport runs on a daemon worker thread and the
   waiting side enforces the deadline (`thread.join`); a worker that
   outlives it cannot be killed, so it is abandoned — its result is
   discarded, and it dies when its own socket operations time out or the
   process exits. This closes the exact gap: `urlopen(timeout=...)` bounds
   individual socket operations, not one generation, so a slow provider
   looks like ordinary latency for as long as it streams. The abandoned
   call may still complete (and be billed) server-side; the point is that
   the run stops waiting for it and re-routes immediately.
2. **Retryable, distinctly logged.** A timeout is retryable with no
   server-requested wait and gets its own log line
   (`[OPENROUTER] timeout model=… provider=… after=…s ceiling=…s
   attempt=… next_route=auto`) plus a step-summary row, so a slow route is
   never confused with ordinary latency. `provider=` names the *requested*
   route (pinned order, lowercased as sent, or `auto`): a timed-out call
   reports no usage, so the actually-serving provider is unknown.
3. **Re-route on retry.** After a timeout, every subsequent attempt of that
   call drops the pinned route (`routing=None`), which re-enables
   OpenRouter's own provider failover and keeps the retry off the slow
   provider. The **model is unchanged**: this is a latency fix, not a
   verdict-quality change. The retry keeps the standard escalating
   schedule, and `REVIEW_RETRY_BUDGET_SECONDS` remains the outer bound of
   one call.
4. **KPI field.** The KPI table gained a `Timeouts` column; the count
   travels as `usage["timeouts"]` (summed per judge and in the Total row)
   and as `llm.timeouts` on the LLM span, so a slow route is visible in the
   posted review rather than only in the log.
5. **Per-judge bound on the batch path.** The ceiling is per **call**, so a
   many-batch judge needs its own bound or a run can still be long. The
   multi-batch path may spend at most `REVIEW_RETRY_BUDGET_SECONDS` in
   total; batches left unevaluated past that point produce a NEEDS REVIEW
   verdict naming how many were skipped — a truncated review must never
   read as a PASS.
6. **A timeout alone is NOT a model-fallback trigger.** The attempt is
   retried on the same model, auto-routed. `fallback_model` stays reserved
   for the two triggers ADR-0021/D-0021 already define: exhausted
   API-error retries and empty content. A timeout can still *lead* to the
   fallback, but only through ordinary exhaustion of the retry budget,
   which the existing ladder already handles.

The verdict block (D-0002), the exit-code contract (D-0014), the completion
cap (D-0021), and the empty-content nudge ladder are unaffected: a timeout
is not empty content, and the model that produces the verdict is the
configured one.

### Rationale

Judge duration was previously unbounded in two independent ways. The
completion cap (D-0021) bounds the degenerate *generation*; it does not
bound *duration* — the motivating 564.1s response was a legitimate PASS at
19,540 tokens, well under the cap, and an attempt pinned to a narrower
route ran past 48 minutes before it was cancelled. A pinned `routing` list
made that worse than a latency problem: `provider.order` with
`allow_fallbacks: false` also removes the escape hatch, so every retry
re-entered the same provider, and the measured variance on one model was
roughly 7x on route alone (pinned/narrow lists 564.1s vs. auto-routed
20.4–46.0s on near-identical diffs).

Releasing the route rather than switching the model is the smallest change
that addresses both halves: the slow route is abandoned, and the retry can
reach a provider that serves the same model quickly. It also works when no
`fallback_model` is configured — relying on the fallback alone would leave
the sticky-pinned defect in place for every consumer that has not set one.

The per-judge bound is deliberately expressed with the existing retry
budget rather than a new knob: one scale, and the same number that already
means "the maximum wall clock one budgeted call may spend". Exceeding it
yields NEEDS REVIEW (merge-blocking) instead of a silent PASS for a review
that only saw part of the diff.

### Amendments

None.

### Inspiration & References

- [OpenRouter — Provider Routing](https://openrouter.ai/docs/guides/routing/provider-selection)
  — `order` pins the provider sequence and `allow_fallbacks` (default
  `true`) governs provider failover; the pinned-plus-no-fallbacks shape is
  what makes a slow provider sticky across retries.
- [OpenRouter — How model routing works: providers, fallbacks & auto](https://openrouter.ai/blog/insights/model-routing)
  — provider failover is automatic and on by default, model-layer fallbacks
  are opt-in, and `sort` can target `price`/`throughput`/`latency`; the
  documented route-level knobs are what the re-route releases.
- [OpenRouter — Provider failover vs model fallbacks](https://openrouter.ai/blog/insights/reliability-failover)
  — the two reliability layers are separate; separately confirms that a
  failed request is not billed.
- [ScrapingBee — How to handle timeouts in Python Requests](https://www.scrapingbee.com/blog/python-requests-timeout/)
  — connect/read timeouts are per socket operation and "none of this is
  strict wall-clock"; the reason a client-side deadline wrapper is required
  instead of relying on `urlopen(timeout=...)`.
- Issue #44 and its measurements (social-engagement-engine PR #19 runs
  35724530627, 35727504470, 35707444272; cancelled attempt 35719748088) —
  the 7x route variance, the 564.1s legitimate PASS, and the >48-minute
  cancelled run.

## D-0023 — A judge finding means "must change before merge"; the verdict stays severity-blind

- Date: 2026-09-23
- Status: Accepted

### Decision

What a judge may report as a **finding** is defined by a promotion threshold
stated in the judge prompts — not by the severity label carried with it:

1. **Threshold.** An item becomes a finding only if it must change before
   merge: if the diff shipped as-is, a maintainer of the repository would be
   entitled to block the merge over it. Every other observation belongs in
   the reasoning block, optionally under a `Minor observations` heading —
   cosmetic notes (a missing trailing newline at EOF, a whitespace or
   formatting wobble, a naming preference), optional suggestions, and
   observations the judge weighed and judged acceptable. The rule is one
   shared text spliced into every judge's scoring-rule section, so the four
   judges cannot drift apart, and it is stated next to the PASS/FAIL
   definition because that is where the judge decides whether to emit a
   finding.
2. **Not a downgrade licence.** A genuine failure of the judge's own criteria
   — a convention violation, a missing test for changed logic, a verifiable
   vulnerability — is always a finding, however small the fix.
3. **Severity stays descriptive.** `evaluate_response` still derives FAIL
   from the presence of any parsed finding; the `severity` value is recorded
   and rendered in the review body but participates in no verdict semantics.
   The hidden verdict block (D-0002) and the all-or-nothing merge gate are
   unchanged.
4. **Prompt-level, deliberately.** The gate-level alternative — `BLOCKER` /
   `WARNING` fail while `NIT` / `SUGGESTION` annotate only — is explicitly
   **not** adopted: it would make the merge gate a function of a
   model-assigned label rather than of the presence of a blocking item, and
   it would put severity semantics into the verdict contract every consumer
   parses. The alternative is recorded here so it is not reintroduced by
   accident; adopting it later is a new decision, not an amendment.

### Rationale

Findings are the merge gate, so a judge's only alternatives were "say
nothing" or "block the merge". Without a stated threshold, helpful judge
behaviour read as failure: on social-engagement-engine PR #19 the
architecture judge returned FAIL whose single finding was
`[NIT] config/factory.json is missing a trailing newline at EOF`, while its
own reasoning called the change "internally consistent" and the item
"fixable in seconds". The consumer spent a fix commit and a full CI cycle on
one byte, and the gate's signal — "this PR is architecturally wrong" —
became "someone forgot a newline".

The prompts already permitted observations in reasoning (the security judge,
in the same review set, weighed mutable-tag pinning of the toolkit ref and
reported it there rather than as a finding), so the missing piece was the
threshold, not a new mechanism. Fixing it in the prompts keeps the
probabilistic judgement where it belongs and leaves the deterministic
contracts — verdict block, exit-code gate, severity rendering — untouched.
Prompt text is a versioned artefact: consumers that snapshot the prompts
verbatim refresh that snapshot in the same release, and the change is called
out in the CHANGELOG section of that release.

### Amendments

None.

### Inspiration & References

- Issue #46 — the problem statement, the recommended prompt-level option, and
  the gate-level alternative recorded (and rejected) above.
- social-engagement-engine PR #19, run 35724530627 job 106735158434 — the
  architecture verdict whose only finding was the missing trailing newline,
  and the cost of the false block (one fix commit plus a full CI cycle on a
  docs/config-only diff).
- The judge-neutrality preamble (`JUDGE_NEUTRALITY_INSTRUCTIONS`, issue #61) —
  the other cross-cutting, shared judge-prompt text, layered on at composition
  rather than per judge, for the same reason: it is a property of the finding
  contract, not of one judge's criteria. It has no entry here (it predates the
  toolkit's decision log); this entry records only the finding threshold.

## D-0024 — An undelivered review body is persisted, never discarded

- Date: 2026-09-23
- Status: Accepted

### Decision

When a run ends after the judges have spoken without delivering their review,
the body is handed over instead of discarded:

1. **Persist, log, annotate.** `review.py` writes the exact body to
   `review_body.md` (overridable via `REVIEW_BODY_PATH`) in the step's
   working directory, dumps the body to the job log, and emits an
   `::error::` annotation naming every judge's verdict
   (`syntax_lint=PASS test_coverage=FAIL …`). Three channels, because they
   fail independently: the log is always readable, the artifact needs
   repository access and expires, and the annotation is the only one visible
   in the checks UI without opening anything.
2. **The file exists only when the body was NOT delivered.** Its presence is
   the signal — "the verdicts are here, not on the PR". `llm-pr-review.yml`
   uploads exactly that path as the `llm-pr-review-body` artifact, gated on
   `failure()` *and* on the file being present, with
   `if-no-files-found: error`. A green run therefore produces no artifact,
   and neither does a red run whose review WAS posted (a real FAIL verdict is
   red but delivered).
3. **The guard sits at the body's lifetime.** It wraps everything from
   `build_review_body` until the body is delivered, not the submission call
   alone, so any later failure in that region takes the same path. A refused
   submission is the observed case; the placement is what makes the
   guarantee structural rather than incidental.
4. **Byte-identical, one format.** The persisted bytes are the bytes that
   would have been posted — the summary table, findings, reasoning, KPI table
   and hidden verdict block — so no consumer parses a second format.
5. **No second submission, no changed exit codes.** Persistence is
   observability, not a retry: a refusal is a policy/identity answer, and a
   retry could double-post. A submission failure still exits 1 and a non-PASS
   verdict still exits 1.
6. **No new exposure.** The body is the content that would have been posted
   publicly on the PR, and the artifact's audience (anyone with repository
   read access) is a subset of the review's. Nothing is added to the body, so
   no redaction change is implied.

### Rationale

The judges' verdicts exist only inside the review body. On
social-engagement-engine PR #19 (run 35707444272, job 106679897161) all four
judges ran for 2m17s and passed, GitHub refused the submission, and the run
ended with one error line: `gh api …/pulls/19/reviews` returned `[]`, so the
verdicts, all four reasoning sections and the KPI table existed only in
memory. The consumer's contract — parse the hidden verdict block from the
newest review, treat `has_review: false` as "not posted yet" — could not
distinguish "judged but not delivered" from "not judged yet", and the run's
cost had already been paid.

Writing the file only on the failure path (rather than always, then deleting
on success) is what makes the artifact itself the signal and keeps green runs
clean; `if: failure()` alone would upload on every red run, including ones
whose review was posted, which is why the workflow pins both halves of the
condition. Dumping the body to the log as well is deliberate redundancy: an
artifact requires repository access and expires, while the log is part of the
run record, and the annotation is the only channel that surfaces the verdicts
where a consumer looks first — the checks UI.

The artifact name is fixed (`llm-pr-review-body`) and the upload deliberately
does **not** set `overwrite`: a duplicate name can only arise from the
documented exactly-one-rule violation (two judge composites in one PR), where
both bodies are in the job logs anyway and a loud conflict is better than a
silently overwritten record.

The exit-code gate is untouched. The toolkit's merge gate stays "the check is
red", and this decision only makes that red state self-documenting.

### Amendments

None.

### Inspiration & References

- Issue #47 — the loss, the three-channel proposal, and the explicit
  constraints (no double-post, byte-identical body, unchanged exit codes).
- Issue #43 and D-0005 — the submission refusal this observes (an
  installation token cannot approve any PR; a repository policy governs
  review state) and the action-flag rules the persistence deliberately leaves
  untouched.
- social-engagement-engine PR #19, run 35707444272 job 106679897161 (verdicts
  lost) against run 35727504470 job 106744848077 (review posted, verdict block
  readable from `…/pulls/20/reviews`) — the two outcomes a consumer could not
  tell apart.
- [actions/upload-artifact](https://github.com/actions/upload-artifact) —
  `if-no-files-found` (`warn` default, `error`, `ignore`); the guarded step
  chooses `error` so a mismatch between the file gate and the upload path
  fails loudly rather than silently dropping the body.
- [GitHub Docs — Contexts reference](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts)
  — the availability table lists `hashFiles` for `jobs.<job_id>.steps.if`
  (and *not* for `jobs.<job_id>.if`), which is what makes the file-existence
  half of the step condition expressible at step level; the function resolves
  patterns against `GITHUB_WORKSPACE`
  ([expressions reference](https://docs.github.com/actions/reference/evaluate-expressions-in-workflows-and-actions)).

## D-0025 — A failed Semgrep ruleset fetch is retried, bounded, on both surfaces

- Date: 2026-09-23
- Status: Accepted

### Decision

The Semgrep gate resolves its ruleset on `semgrep.dev` at run time
(`--config=auto` is the documented contract and what `security.yml` passes).
A failed fetch is a network condition, not a finding, so it no longer fails
the gate outright:

1. **One wrapper owns the policy.** `scripts/semgrep_scan.py` (console script
   `semgrep-scan`) is `semgrep scan` plus a bounded retry of the
   *configuration* load. The pre-commit hook runs it as a console script;
   `security.yml` runs the same file from the toolkit checkout. The policy is
   never re-implemented in YAML — two copies that can drift is the failure
   class D-0012/D-0016/D-0017 exist to prevent, and a shell loop in a workflow
   step is not reachable by the test suite.
2. **The retry keys on the configuration bucket, not on the message.** Semgrep
   exits 7 both for an invalid ruleset and for a ruleset it could not fetch,
   and `--quiet` keeps that exit code while suppressing the
   `[ERROR] Failed to download configuration …` line — so a message-only
   predicate would miss exactly the reported case. The message is still read,
   only to *name the cause* in the log. Semgrep's transport-failure shape
   (`Failed to download config from <url>: HTTP request failed: …`) can arrive
   with a different exit code and is caught by the same signature.
3. **Bounded and observable.** Three attempts in total, 2 s and 5 s apart
   (≤ ~7 s added wall-clock). Each retry logs
   `[semgrep-scan] attempt n/3: semgrep could not load its configuration
   (exit 7); retrying in 2s`, plus the fetch-failure line when semgrep printed
   one, and a retry that succeeds says so. The wrapper's own lines are not
   affected by `--quiet`.
4. **Nothing is hidden.** The last attempt's output and exit code are
   reported unchanged, so a genuine configuration error still fails with the
   code it always had and the exit-code contract consumers read stays true.
   Output is captured per attempt and replayed verbatim (stdout to stdout,
   stderr to stderr) — the retry decision has to read it, and the replayed
   bytes are the bytes semgrep printed.
5. **`security.yml` joins the toolkit-ref contract.** The wrapper ships with
   the toolkit, so `security.yml` declares the same `toolkit-ref` input as
   secret-scan/diff-coverage/llm-pr-review (default = the release tag), both
   composites forward it, and each release PR bumps it with the other pin
   sites (D-0007). The toolkit's own `ci.yml` passes the PR head SHA, as it
   already does for the secret scan and the judges (D-0019).

### Rationale

A merge-blocking gate that fails closed on someone else's rate limiter is a
false red: the consumer's cost is a wasted cycle and, worse, a habit of
re-running red security checks. The observed failure (a consumer's
`make verify`: exit 7 with no output, passing on an immediate re-run with no
code change) is exactly the transient class a bounded retry removes.

The alternatives from the issue were each rejected for a specific reason.
**Caching** has no first-party support left: semgrep removed its experimental
registry cache (`--registry-caching`, gated behind `--experimental`) from
osemgrep, and the pinned 1.177.0 has no such flag — a toolkit-built cache
would mean owning a cache directory, its invalidation, and an
`actions/cache` wiring, and would still miss the cold fetch of every fresh CI
job, which is where the failure was seen. **Vendoring the ruleset** is
deterministic but anti-security (new detection rules would never apply
retroactively) and is already out of scope for the pinning issue; it also
makes the toolkit the curator of a ruleset it has no standing to curate.
**Accept-and-document alone** leaves the reported bug in place. What ships is
retry *plus* the documentation half: after the bound, the failure mode is
documented with the consumer's response, and the wrapper's log line names the
cause even when `--quiet` hides semgrep's own message.

Retrying the whole configuration-error bucket rather than only a detected
download failure is the deliberate part. The bucket is small (the run failed
before scanning, so nothing is re-scanned) and the cost is bounded, while the
discriminator we would need — a visible message — is not reliably present.
Because the final status is passed through, the retry cannot turn a real
configuration error into a green run: it can only delay it.

The retry bound is sized from the evidence, not from theory: the failure
cleared on an immediate manual re-run, and anonymous registry rate limits are
short. Two retries at 2 s and 5 s sit far below the gate's other budgets
(the judge run's 45-minute retry budget, `REVIEW_CALL_TIMEOUT_SECONDS`) and
are invisible in a green run.

Putting the wrapper behind `security.yml`'s existing `toolkit-ref` input
extends one documented limitation: the toolkit's own `ci.yml` passes its PR
head SHA, which does not exist in this repository for fork PRs — the same
limitation the secret scan already carries, recorded under Known limitations.

### Amendments

None.

### Inspiration & References

- Issue #50 — the failure record (consumer `make verify`, exit 7 with no
  output, identical hook passing moments later, `curl` showing the registry
  endpoint alive) and the four candidate policies this entry resolves.
- Probe against semgrep 1.177.0 (this session): a nonexistent registry
  ruleset yields `exit code 7` with
  `[ERROR] Failed to download configuration from https://semgrep.dev/c/p/<name> HTTP 404.`
  and `[ERROR] invalid configuration file found (1 configs were invalid)` on
  stderr and **zero bytes** of output with `--quiet` — the two facts the retry
  predicate is built on. A clean local-config scan exits 0; a scan with one
  finding and `--error` exits 1 (never retried).
- [Semgrep CLI reference — exit codes](https://docs.semgrep.dev/cli-reference)
  — the exit-status table (7 = invalid configuration / missing
  configuration) and the `--config` semantics ("Use `--config auto` to
  automatically obtain rules tailored to this project; your project URL will
  be used to log in to the semgrep registry").
- [Semgrep CHANGELOG](https://github.com/semgrep/semgrep/blob/develop/CHANGELOG.md)
  — "Removed the Registry caching experimental feature
  (`--experimental --registry-caching`) in osemgrep" (`registry_caching`),
  which is why caching is not the toolkit's option to take.
- [Semgrep — How we resolved the 'HTTP request failed: timeout' issue in
  OCaml](https://semgrep.dev/blog/2023/http-request-failed-timeout-issue-in-ocaml)
  — Semgrep's own CI hit a config-download failure, and their fix was in the
  transport layer (happy-eyeballs), not a retry: the download path has no
  first-party retry to lean on, and an upstream fetch failure is a fatal
  error in the CLI.
- Issue #51 — the diagnosability half of the same failure (exit codes that
  mean "not a finding", and the `--quiet` trap); it stays a separate,
  independently closable issue, and this entry's log lines are what a retry
  looks like from a consumer's side.
- Issue #39 — the version-pinning axis of the same gate, including the
  explicit non-goal (no vendored ruleset) this entry keeps.
