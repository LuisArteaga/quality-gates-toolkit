# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each section below is mirrored verbatim into the matching
[GitHub Release](https://github.com/LuisArteaga/quality-gates-toolkit/releases)
(D-0018). Release 1.5.0 was never cut: its content shipped in the combined
1.6.0 release.

## [1.8.7] - 2026-09-26

### Fixed

- A judge answer is a verdict only when its `<findings>` block is **presented
  as a block**. A tag written inside a sentence (or in backticks) is the judge
  *describing* the format rather than emitting it, so prose that quoted an
  empty block no longer passes with nothing parsed, and prose that quoted a
  JSON example no longer fails on the illustration. The open tag has to stand
  at the start of the content, at the start of a line, or immediately after
  another tag — the last clause keeps a compact answer
  (`</reasoning><findings></findings>`) a verdict (D-0026 amendment, PR #76).
- A `<findings>` block that is present, non-empty and carries no readable
  finding is unparseable instead of passing silently: a block holding only
  prose, or only a malformed JSON line, declared no findings *and* no pass. It
  takes the existing path — one retry, then NEEDS REVIEW. A block carrying one
  readable finding beside an explanatory sentence still fails the answer with
  that finding, because the rule is readability rather than strict per-line
  format (D-0026 amendment, PR #76).

### Changed

- An answer whose `<findings>` tags appear only inside prose now reports that
  shape ("the tag appears only inside prose") instead of claiming the tags are
  missing, and the review body's label reads *Judge answer was not parseable
  (no readable `<findings>` block).* The retry instruction asks for a block on
  its own lines holding one JSON object per finding (D-0026 amendment, PR #76).

## [1.8.6] - 2026-09-25

### Fixed

- A judge answer without the `<findings>` block its prompt requires is no
  longer read as a **pass**. The verdict was derived from what the parser
  found rather than from the shape the prompt asks for, so an answer carrying
  only `<reasoning>` had nothing to parse and fell through to PASS — a merge
  gate approving a PR on an answer that declared nothing. An answer whose
  `<findings>` block is *missing* is now unparseable, while an **empty**
  `<findings>` block stays a legitimate pass (the normal passing answer under
  the finding-promotion threshold, D-0023). The `<reasoning>` block is not
  required for a verdict, so a findings-only answer still fails on its finding
  (D-0026, PR #71).
- A non-empty unparseable answer is retried **once** with an instruction
  naming the missing block, instead of blocking the merge with nothing to act
  on. An answer that is still unreadable after the retry leaves the judge
  NEEDS REVIEW; it is never nudged twice and never retried on the fallback
  model (that tail stays triggered by an empty answer), and the added call is
  bounded by `REVIEW_RETRY_BUDGET_SECONDS` / `REVIEW_CALL_TIMEOUT_SECONDS`
  like every other call (D-0026, PR #71).

### Changed

- The review body reports the three "no verdict" reasons distinctly: a
  crashed check (`Check failed to run: …`), an answer the engine could not
  read (`Judge answer was not parseable (no `<findings>` block).`), and the
  existing catch-all (`Insufficient context.`). Reporting the unreadable case
  as missing context sent the author looking for a document instead of at the
  model's output shape (D-0026, PR #71).
- The README documents the judge answer contract under *Judge configuration*,
  gains a troubleshooting entry for the unparseable-answer signature, and
  records one known limitation: an answer that merely *quotes* the
  `<findings>` block in prose is read as tagged (the extraction semantics were
  deliberately left unchanged; the residual hole is tracked separately).

## [1.8.5] - 2026-09-24

### Changed

- The judge review needs **no GitHub PAT**: the README's *Secrets* section now
  leads with "only `OPENROUTER_API_KEY` is ever needed", the `JUDGE_GH_TOKEN`
  subsection is retitled as optional (the tokenless path is the default), and
  the `judge-token` input description states the same. The toolkit's own
  `ci.yml` stopped forwarding `judge-token`, so every toolkit PR dogfoods the
  tokenless path — the review is posted by `github-actions[bot]` as a comment
  review carrying the identical body (verdict block, per-judge reasoning, KPI
  table), and the check's exit code remains the merge gate. A contract test
  pins the dogfood so a stray PAT forward cannot reappear unnoticed
  (D-0005, PR #66).
- The review authenticates with `GH_TOKEN` **only**. `review.py::main()` no
  longer reads the origin project's legacy `GH_PAT`, which took precedence
  over `GH_TOKEN` — a second, undocumented variable that made the effective
  token invisible in the run's log. A set `GH_PAT` is now logged as ignored
  instead of silently winning; the missing-token path stays fail-fast, and
  `GH_TOKEN` is documented as a review-run environment variable
  (D-0005, PR #66).

## [1.8.4] - 2026-09-23

### Added

- `semgrep-scan` console script (`scripts/semgrep_scan.py`): `semgrep scan`
  plus a bounded retry of the ruleset *configuration* load. The ruleset is a
  live registry artefact (`--config=auto` fetches it at run time), and a
  failed fetch — an unauthenticated call that can be rate-limited (observed
  as HTTP 403) — exits 7, the same code semgrep uses for an invalid ruleset.
  The wrapper retries that outcome twice, 2 s and 5 s apart, logs each
  attempt as `[semgrep-scan] …` together with the fetch-failure line when
  semgrep printed one, and reports the last attempt's output and exit code
  unchanged: nothing is hidden, and a genuine configuration error still
  fails exactly as before. The `semgrep` pre-commit hook runs the same
  wrapper and `security.yml` runs it from the toolkit checkout, so the
  policy cannot drift between local commits and CI (D-0025, PR #63).

### Changed

- `security.yml` takes a `toolkit-ref` input (default = the release tag), the
  same contract as the other toolkit-executing micro-workflows, because its
  Semgrep step now runs the toolkit's wrapper. Both composites forward it, so
  a caller overriding `toolkit-ref` also selects the wrapper.
- The README documents the semgrep ruleset-fetch policy (the network
  dependency, the retry bound, the `--quiet` interaction) under
  *Pre-commit hook*, and its troubleshooting list gains the "`exit code 7`
  with no message" signature (D-0025).

### Fixed

- A judge review whose GitHub submission is refused is no longer discarded:
  the exact body is written to `review_body.md` (overridable via
  `REVIEW_BODY_PATH`), dumped to the job log, annotated in the checks UI
  (`LLM judge verdicts: …`), and uploaded as the `llm-pr-review-body`
  artifact. The file exists only when the body was NOT delivered, so its
  presence is the signal and a green run stays artifact-free. Submission
  refusal still exits 1 and a non-PASS verdict still exits 1 — the
  persistence is observability, not a retry (D-0024, PR #61).

## [1.8.3] - 2026-09-23

### Changed

- Judge prompts state a **finding promotion threshold**: an item is reported
  as a finding only if it must change before merge — if the diff shipped
  as-is, a maintainer of the repository would be entitled to block the merge
  over it. Every other observation (a missing trailing newline at EOF, a
  whitespace or formatting wobble, a naming preference, an optional
  suggestion, or an observation the judge weighed and judged acceptable)
  stays in the judge's reasoning, optionally under a `Minor observations`
  heading. Previously a judge's only options were "say nothing" or "block the
  merge", so a cosmetic note failed an otherwise sound PR (observed: a single
  `[NIT] … is missing a trailing newline at EOF` as the sole finding of an
  architecture FAIL, costing a fix commit and a full CI cycle on one byte).
  The threshold is not a licence to downgrade real violations — a genuine
  criterion failure is always a finding — and the verdict stays
  **severity-blind**, so the verdict-block contract and the merge gate are
  unchanged (D-0023, PR #59).
- Judge prompts are versioned artefacts: a consumer that snapshots them
  verbatim (instead of importing
  `quality_gates_toolkit.review.JUDGE_PROMPTS`) must refresh that snapshot in
  the same release.

## [1.8.2] - 2026-09-23

### Added

- Per-call wall-clock ceiling for judge calls: `REVIEW_CALL_TIMEOUT_SECONDS`
  (default 300) abandons a call that exceeds it, logs
  `[OPENROUTER] timeout model=… provider=… after=…s`, and counts it in a new
  **Timeouts** column of the review's KPI table. `urlopen`'s socket timeout
  bounds one network operation, not one generation, so a slow provider
  previously cost minutes per call with no error at all (observed: 564.1s for
  a legitimate verdict, and one pinned route past 48 minutes).

### Changed

- A retry after a timeout **releases a pinned `routing` order**: the same
  model is retried with OpenRouter's own provider failover re-enabled,
  because pinned routing disables failover and would re-enter the same slow
  provider. A timeout is deliberately **not** a model-fallback trigger —
  `fallback_model` keeps its existing triggers (exhausted API-error retries
  and empty content) (D-0022, PR #57).
- The multi-batch path is bounded in total by `REVIEW_RETRY_BUDGET_SECONDS`:
  batches left unevaluated report NEEDS REVIEW naming how many were skipped,
  instead of a truncated review reading as a PASS.

## [1.8.1] - 2026-09-23

### Fixed

- Identity-less (installation-token) judge runs now always submit a comment
  review instead of the verdict-derived action. GitHub forbids the Actions
  token from approving a pull request at all, so an all-PASS run previously
  submitted `--approve`, was refused, and ended as a permanently red judge
  job with the review — and with it the hidden verdict block — never posted;
  the failing outcomes, by contrast, went through. `--approve` now requires
  a user-identity token that is not the PR author, FAIL / NEEDS REVIEW keeps
  exiting nonzero, so the check remains the merge gate. The README secrets
  section and the `llm-pr-review.yml` design notes were corrected
  accordingly, and D-0005 carries the amended contract (PR #55).

## [1.8.0] - 2026-09-23

### Added

- Bounded judge completions: `max_tokens` from `factory.json` is wired into
  every OpenRouter request, with a toolkit default of 32768 applied when a
  consumer config omits it. A degenerate generation can no longer burn a
  model's whole output ceiling (observed: 131,072 tokens over ~23 min with
  empty content, ~38% of a run's judge cost). The cap applies to the
  fallback model too, and an empty response that reached the cap skips the
  same-model nudge in favour of the fallback model (D-0021, PR #53).

### Changed

- The resolved judge config always carries a positive `max_tokens`
  (documented in the README judge-configuration section and in
  `config/factory.example.json`). This is the one intentional exception to
  the flat-config "resolves byte-identically to v1.3.0" golden contract: a
  config omitting the key resolves to the default instead of `None`
  (D-0021).

## [1.7.0] - 2026-09-12

### Added

- Per-language composite entry points: `python-checks.yml` (lint, test,
  security, secret-scan, diff-coverage, optional LLM review) and
  `js-checks.yml` (the three JS gates, optional secret-scan and LLM review).
  Monolingual callers get the one-call ergonomics with zero `Skipped`
  entries; new languages add composite files, not polyglot toggles; the
  polyglot composite stays as-is; exactly-one judge rule across composites
  (D-0020, PR #36).

## [1.6.0] - 2026-09-11

Combined release: the tag train skips 1.5.0 — both pending features below
shipped in 1.6.0 (`pyproject.toml` documents the skip).

### Added

- Importable judge API: the judge engine and its dependency closure ship as
  the `quality_gates_toolkit` package for cross-repo consumption
  (`pip install "quality-gates-toolkit @ git+...@v1.6.0"`); `scripts/` keeps
  the console scripts plus backward-compatibility import shims
  (D-0017, PR #30).
- Local Python tool pre-commit hooks: `mypy` (advisory, consumer env),
  `semgrep`, and `pip-audit` with pinned tool versions and
  `language_version: python3.12` (D-0016, PR #29).

## [1.4.0] - 2026-09-11

### Added

- Nested judge-config sections in `factory.json` with additive union
  resolution: top-level node config first, then the `ci_cd_pr_judges` default
  section and sections declared via the reserved `judges-section` key
  (D-0015, PR #25).

### Changed

- Consumer documentation expanded: full input reference, review-run
  environment variables, telemetry export, troubleshooting, and verdict
  consumption (PR #21).
- Python install contract pinned: the lint job installs best-effort
  (`pip install -e ".[dev]" || pip install -e .`), the test job stays strict
  (D-0014, PR #22).

## [1.3.0] - 2026-09-07

### Added

- Composite language toggles: `enable-js-lint`, `enable-js-test`,
  `enable-js-typecheck` (all default OFF, so existing Python callers are
  unaffected) and a composite-level `node-version` forwarded to the JS jobs
  (D-0013, PR #18).

## [1.2.0] - 2026-09-07

### Added

- `js-lint.yml` reusable ESLint gate (fixed `npm run lint` contract) plus the
  matching `js-lint` pre-commit hook (D-0012 amendment, PR #16).

## [1.1.0] - 2026-09-07

### Added

- JavaScript gates v1: `js-typecheck.yml` (`npm run typecheck`) and
  `js-test.yml` (`npm ci` + `npm test`) as standalone reusable micro-workflows
  with a fail-loud lockfile requirement, plus the matching `js-typecheck` and
  `js-test` pre-commit hooks (D-0012, PR #14).

## [1.0.4] - 2026-09-06

### Fixed

- The composite `llm-review` job runs only on `pull_request` events, so non-PR
  callers get skipped-job clarity instead of confusion (D-0011, PR #9).

## [1.0.3] - 2026-09-06

### Fixed

- Secret scanner false positives on npm/yarn lockfiles: suppression is by
  token shape (`sha512-…` integrity tokens) rather than by filename, so real
  credentials pasted into lockfiles still fire (D-0010, PR #7).
- Note: the v1.0.3 tag was re-cut at the corrected release commit; per-release
  `toolkit-ref` defaults are now bumped inside each release PR so a tag's own
  tree is self-consistent.

## [1.0.2] - 2026-09-05

### Fixed

- The lint workflow installs dev extras so strict mypy resolves `tests/`
  imports (PR #5).

## [1.0.1] - 2026-09-05

### Added

- README Secrets section: `OPENROUTER_API_KEY`, the optional `JUDGE_GH_TOKEN`
  trusted-identity boundary, and the fork-PR limitation (PR #3).

### Fixed

- The lint workflow installs the caller's dependencies so strict mypy sees
  their imports (PR #4).

## [1.0.0] - 2026-09-05

### Added

- Initial public release: reusable quality-gate workflows (composite
  `pr-checks.yml` chaining lint, test, diff-coverage, secret-scan, and the
  LLM judge review; standalone micro-workflows), Python tooling
  (`secret-scan` console script, diff-coverage gate), the `secret-scan`
  pre-commit hook, immutable per-release tag pinning with self-consistent
  `toolkit-ref` defaults (D-0007), and the ADR-lite decision log
  (D-0001–D-0006).

[unreleased]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.8.7...HEAD
[1.8.7]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.8.6...v1.8.7
[1.8.6]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.8.5...v1.8.6
[1.8.5]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.8.4...v1.8.5
[1.8.4]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.8.3...v1.8.4
[1.8.3]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.8.2...v1.8.3
[1.8.2]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.8.1...v1.8.2
[1.8.1]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.8.0...v1.8.1
[1.8.0]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.7.0...v1.8.0
[1.7.0]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.4.0...v1.6.0
[1.4.0]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.0.4...v1.1.0
[1.0.4]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/LuisArteaga/quality-gates-toolkit/releases/tag/v1.0.0
