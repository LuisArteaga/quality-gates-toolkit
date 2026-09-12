# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each section below is mirrored verbatim into the matching
[GitHub Release](https://github.com/LuisArteaga/quality-gates-toolkit/releases)
(D-0018). Release 1.5.0 was never cut: its content shipped in the combined
1.6.0 release.

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

[unreleased]: https://github.com/LuisArteaga/quality-gates-toolkit/compare/v1.7.0...HEAD
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
