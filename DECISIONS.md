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

None.

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

