# Post-mortem: consumer and event values interpolated into workflow `run:` scripts

**Date:** 2026-09-28
**Status:** resolved — the fix is merged into `main` as PR #94 (merge commit
`dd37fb1`); this document landed with the v1.9.2 release PR, which adds only
the version pins and the release notes.
**Severity:** medium
**Detected by:** the LLM `security` judge on PR #88 (iteration 1), which flagged
the site that diff introduced and classified the pre-existing sibling as out of
scope; that run's post-mortem phase then enumerated the class and filed #89.
**Follow-up issues:** #95

## Impact

Nine `run:` bodies across four workflows interpolated a consumer- or
event-supplied value directly into shell script text: `lint.yml` (4),
`test.yml` (3), `llm-pr-review.yml` (1), `diff-coverage.yml` (1). Because
`${{ ... }}` is substituted into the script *before* the shell parses it, each
site was a script-injection surface on the runner for whatever runs the reusable
workflow — with the job's `GITHUB_TOKEN` and any in-scope secrets behind it.

- **Exposure window:** from the bootstrap commit `eceb66f`
  (2026-09-05T08:36:27Z) through tag `v1.9.1`
  (tag commit 2026-09-28T13:14:54+02:00) — 23 days and 23 tags. A YAML walk over
  `jobs.*.steps[*].run` counts 9 sites at `v1.0.0` and 9 at `v1.9.1`: one
  (`security.yml`) was fixed on 2026-09-28 by PR #88, one additional `lint.yml`
  site was added in between.
- **Blast radius:** this repository is a public, MIT-licensed collection of
  *reusable* workflows consumed by other repositories. The interpolated values
  are supplied by a caller's own `with:` block, so the injection becomes
  exploitable only when a caller wires untrusted context (a PR title, a branch
  name, an event field) into one of these inputs. No such caller is known in
  this estate and no exploitation was observed or suspected.
- **Nothing was lost or leaked as far as these artifacts can show:** no data
  loss, no service impact, no evidence of a secret reaching a log because of this
  class.

## Detection

PR #88's iteration-1 `security` judge flagged the `--config=${{ inputs.semgrep-config }}`
line that diff added as a blocking finding, quoting GitHub's guidance to route
inline-script values through an intermediate environment variable — and
explicitly classified the pre-existing `scan-paths` interpolation in the same
step as not introduced by that diff. The single detector that has ever run on
this class therefore also recorded, in the same breath, that the class predated
the diff. The run's post-mortem phase then enumerated the sites mechanically and
filed #89 (2026-09-28T10:10:46Z).

It stayed latent for 23 days because nothing else watched it:

- no contract test read `run:` bodies for interpolation (the repo's sweeps cover
  secrets-in-`secrets:`, SHA pins and permissions, none of them script text);
- the Semgrep gate cannot see the files: `ci.yml` passes
  `scan-paths: "scripts quality_gates_toolkit"`, and the `auto` ruleset carries
  no Actions rules — so "the security gate is green" meant *15 Python files
  scanned*, with zero workflow YAML in scope;
- the LLM `security` judge does understand the class, but it reviews a **diff**,
  so a class spread across files is visible only where a diff happens to touch
  it.

## Timeline

UTC, from artifacts (`git log --format=%cI`, `gh issue/pr view`):

- 2026-09-05T08:36:27Z — `eceb66f` "feat: bootstrap toolkit …": the sites enter
  `main`.
- 2026-09-05T15:26:20Z — tag `v1.0.0` published (tag commit 17:26:20+02:00);
  9 run-body sites shipped to consumers.
- 2026-09-28 — PR #88 (`feat/pin-security-scanner-versions`): the `security`
  judge names the class; that site is routed in the fix commit.
- 2026-09-28T10:10:46Z — #89 filed by PR #88's post-mortem phase, listing all
  nine sites and naming the gate blind spot.
- 2026-09-28T10:18:55Z — PR #88 merges (`320f039`); #89 stays open for the other
  nine sites.
- 2026-09-28T11:36:27Z — PR #94 opened: all nine sites routed plus a class-wide
  sweep.
- 2026-09-28 — PR #94: 5/5 checks pass, all four judges PASS (llmreview 13m2s,
  run `36416660363`) — the routed paths executed in the project's own CI.

## Root cause

The mechanism is one line of platform semantics: `${{ ... }}` is substituted into
the script text before the shell parses it, so an inlined value is program text.
Four guards were in a position to catch it and none was pointed at it:

1. **No test asserted the property.** Every `run:` body in this repo is written
   by hand; the contract suite had no rule about what may appear inside one.
2. **The security gate's scope excluded the files.** `scan-paths` names two
   Python packages, and the registry ruleset has no Actions rules — the gate
   scanned the tools the workflows *call*, never the workflows themselves.
3. **The judge's scope is a diff.** Its finding on PR #88 was correct and
   correctly bounded: it named the line the diff touched and, per its own rule
   set, left the pre-existing sibling alone. A diff-scoped reviewer cannot
   enumerate a class.
4. **The written decision read as local.** D-0029 recorded the rule for the
   `security.yml` step under the heading of scanner pins — accurate, but nothing
   in the README, the register, or the tests said *collection-wide*. The next
   workflow author had no reason to read it as a general invariant.

The shared root cause is the one #89 names: **the class had no deterministic
owner.** Everything that could see it was either scoped to another surface or
scoped to a diff.

## What changed

PR #94 (`fix/workflow-script-injection`, commit `58193fe`, merge commit
`dd37fb1`) — already on `main`, so it is not part of the v1.9.2 release PR's
diff, and the tag cut at that release's merge commit publishes it:

- All nine sites route their values through step `env:` — space-separated list
  inputs (`lint-paths`, `cov-paths`, `extra-pip-packages`, `diff-exclude`)
  expanded unquoted because word splitting is their contract, single values
  (`coverage-floor`, the PR base SHA) expanded quoted. No input name, type,
  default or semantic changed.
- **The guard that now protects it:**
  `tests/test_workflow_contracts.py::test_no_run_script_interpolates_untrusted_context`
  parses every workflow and fails the `test` gate if any `run:` body
  interpolates `${{ inputs.* }}`, `${{ github.event* }}` or
  `${{ github.head_ref }}` — with a second test pinning the pattern in both
  directions (guarded families match, workflow-controlled expressions do not) so
  the sweep cannot pass vacuously. A negative control confirmed it fires
  (reintroducing one site: 2 failed / 65 passed, file restored byte-identically).
- D-0029 point 4 is generalised in place and a dated amendment records the rule,
  the guard, and the rejected alternatives; the README states the invariant
  where workflow authors read; `docs/context.md` carries it for the architecture
  judge (`docs/adr/` does not exist in this repo).
- Verified in the project's own CI: the five checks on PR #94 execute the routed
  paths, including the `for p in $DIFF_EXCLUDE` loop with an empty value under
  `set -euo pipefail` — which also proves `env:` entries are exported even when
  the expression resolves to an empty string.

## What is still owed

- **#95** — the sweep is a pattern blocklist: `${{ format('{0}', inputs.x) }}`
  bypasses it, and the `github.event*` prefix over-matches the
  workflow-controlled `github.event_name`. Decide blocklist vs allowlist and pin
  the chosen boundary.
- **#96** — adjacent, not part of this incident: `diff-coverage.yml` has **no**
  in-repo runtime path (the D-0008 carve-out removed it from `ci.yml`, and the
  composite's diff-coverage job is `pull_request`-gated so the canary skips it),
  so this change's `BASE_SHA` route is verified only by a pattern assertion and a
  shell probe. The carve-out's promised follow-up issue was never filed.
- PR #94 awaits the human merge; merging closes #89.

## Lessons

- **A workflow `run:` body is shell source.** Any `${{ ... }}` in it is
  arbitrary program text, and the value's *shape* decides the fix: single values
  expand quoted, space-separated lists expand unquoted (word splitting is the
  contract, and parameter expansion results are never re-scanned for shell
  operators).
- **A gate's `scan-paths` is part of the gate.** "Security scan: green" meant 15
  Python files and zero workflow YAML. When a class lives in a file type the
  gate is not pointed at, a green gate is evidence about the wrong surface.
- **A diff-scoped judge cannot enumerate a class.** It named the one line the
  diff touched and correctly ignored its sibling; the durable detector for a
  class has to read the whole collection, not a diff.
- **A decision recorded under a narrow heading reads as narrow.** D-0029's rule
  was right and reachable, yet the next author had no reason to apply it
  elsewhere — a rule that should bind a whole collection has to say so in the
  collection's own documents (README + `docs/context.md` + the register).
- **`env:` entries are exported even when empty.** Proven in CI:
  `for p in $DIFF_EXCLUDE` with an empty value runs fine under `set -euo
  pipefail`, so an "empty list input" needs no `${VAR:-}` guard.
- **Reusable detector recipes for this class:**
  `jobs.*.steps[*].run` walked with `yaml.safe_load` (a raw grep false-positives
  on `with:`/`env:`/`if:` blocks — 10 sites by raw text, 9 by parse);
  `git show <tag>:<file>` piped into the same walk measures a class per release;
  `bash -n` (not `sh -n`) is the syntax check for these bodies, because the
  runner's shell is bash and the bodies use bash arrays.
