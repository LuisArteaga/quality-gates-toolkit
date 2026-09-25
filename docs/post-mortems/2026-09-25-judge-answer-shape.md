# Post-mortem: an unreadable judge answer read as a pass

**Date:** 2026-09-25
**Status:** draft
**Severity:** medium
**Detected by:** human, from a consumer's blocked PR (social-engagement-engine #49) — no gate caught it; every deterministic gate was green throughout
**Follow-up issues:** #72 (the extraction hole this fix left open), #73 (the new flag's multi-batch fold is unasserted)

## Impact

`evaluate_response` derived a judge's verdict from the tags it found rather than
from the shape its prompt asks for, in both directions:

- **Silent:** an answer carrying `<reasoning>` and no `<findings>` block was read
  as **PASS**. One of four review dimensions could be recorded as passing on an
  answer that declared no findings — and never said it passed either.
- **Loud:** an answer carrying neither tag blocked the merge with an empty
  findings list and the catch-all *"Insufficient context."*, a cause the engine
  had never established. Observed on social-engagement-engine #49: five judge
  executions on one commit family (four pushes plus one `gh run rerun --failed`),
  every deterministic gate green, the node's own prose reading "Approve the
  direction" — the author had nothing to act on and resolved it by changing that
  node's provider configuration.

The parser entered with the bootstrap commit and shipped in **every** release —
17 tags, `v1.0.0` … `v1.8.5` — and in the importable judge package (D-0017), so
any consumer running the judges carried it. Blast radius is bounded by the other
three judges and the deterministic gates: the gate could approve one dimension
unverified, not a whole pull request on nothing. No occurrence of the *silent*
direction was confirmed in the wild — the observed answer carried neither tag and
took the loud branch.

## Detection

No gate detected it. The deterministic gates were green throughout; the LLM
judges reviewed the toolkit's own PRs and never flagged the parser; the parser's
unit tests pinned the *neither-tag* case and never asserted what a reasoning-only
answer produces, so the suite was green while the silent direction was broken.

It surfaced 20 days and 17 releases after introduction, from a human reading a
consumer's blocked PR. The parser's own tests were written against the extractor
("what do I read from a tagged answer") rather than against the contract ("what
shape is a verdict"), which is the gap the case list did not cover.

## Timeline

UTC, from artifacts only.

- **2026-09-05T08:36:27Z** — bootstrap commit `eceb66f` introduces
  `evaluate_response` with the permissive default
  (`git log -S "Response lacks both" -- scripts/review.py`).
- **2026-09-05 … 2026-09-24** — 17 releases (`git tag`: `v1.0.0` … `v1.8.5`,
  `v1.5.0` skipped) carry the parser; `a6da5bc` (2026-09-11) moves it into the
  `quality_gates_toolkit` package unchanged (D-0017).
- **2026-09-25** — social-engagement-engine #49: the loud direction is observed
  in the wild, five judge executions on one commit family, all deterministic
  gates green.
- **2026-09-25T18:24:21Z** — quality-gates-toolkit #70 filed
  (`gh issue view 70 --json createdAt`), naming both directions.
- **2026-09-25T20:22:48Z** — PR #71 opened (commit `9f175c8`, branch
  `fix/judge-verdict-parse-retry`).
- **2026-09-25T20:22:51Z → 20:30:16Z** — CI run `36185417398`: all five checks
  pass (llmreview 6m43s), four judges PASS on the first iteration, no fix commit.
- **2026-09-25T21:11:16Z** — PR #71 merged (`ba5c9ac`); issue #70 auto-closed. The
  fix ships in the `v1.8.6` patch release.

## Root cause

`verdict` was initialised to `"Pass"` and only became `"Fail"` when a line inside
`<findings>` parsed as JSON. The sole "unparseable" guard was
`if not reasoning and not findings_block` — which a reasoning-only answer does
not trip, because `reasoning` is truthy. One line, two consequences: an answer
with no findings block fell through to PASS, and an answer with no tags at all
was reported with a message ("lacks both tags") that the review body rendered as
*"Insufficient context."*.

Why the guards in place did not catch it:

- **The test suite asserted the extractor, not the contract.** The cases covered
  a tagged answer's findings, an empty block, and the absence of *both* markers.
  "One marker present, the other absent" — the dangerous combination — had no
  test, so the default verdict was never exercised against a malformed answer.
- **The diff-coverage gate judges changed lines.** The line was introduced with
  the parser and never touched again, so no later change could surface it.
- **The LLM judges review diffs, not the engine's own semantics.** Their prompts
  ask whether the change is sound; none asks "is this parser's default verdict
  safe when its input is malformed".
- **The extractor cannot tell an absent block from an empty one.**
  `parse_xml_tags` returns `""` for both, so the missing-block case had to be
  checked against the raw answer; treating "no findings parsed" as "no findings"
  is what made the absence invisible.

## What changed

PR #71 (`9f175c8`, all four judges PASS iteration 1, CI green first try):

- `_has_findings_block` + `_is_unparseable_content`: the required shape is the
  `<findings>` block, checked against the raw answer.
- `evaluate_response` reports NEEDS REVIEW when the block is missing, with a
  message that names the block instead of the catch-all.
- `_run_layered_retry` nudges an unparseable answer once with
  `UNPARSEABLE_CONTENT_INSTRUCTION` (bounded by `REVIEW_RETRY_BUDGET_SECONDS` /
  `REVIEW_CALL_TIMEOUT_SECONDS`); a retry that is still unparseable is returned
  as-is, and the fallback tail stays triggered by an empty answer only.
- The judge result carries an `unparseable` flag, rendered as its own review-body
  cell — distinct from a crashed check and from the catch-all.
- **D-0026** records the contract; the README documents it, plus a Troubleshooting
  entry and one Known-limitation bullet for the residual hole.

The guard now protects **the shape the parser requires**, not merely the tags it
happens to find: the suite pins all four boundaries (missing block → NEEDS
REVIEW, block without reasoning → a verdict, empty block → PASS, quoted tags →
the recorded hole).

## What is still owed

- **#72** — `parse_xml_tags` reads tags quoted in prose, so an answer that merely
  mentions the block is treated as a verdict (prose quoting an empty block passes;
  prose quoting example JSON fails on the example).
- **#73** — the new `unparseable` flag's multi-batch fold is never asserted with a
  True value.

## Lessons

- A parser that derives a verdict from what it found accepts a malformed answer in
  whichever direction its initial value points. State the required shape and test
  each boundary, not just the happy path.
- "No block found" and "empty block" are the same value to an extractor: separating
  them needs a presence check against the raw answer.
- A test that pins only the absence of *all* markers leaves the per-marker cases
  unasserted — one marker present and the other absent was the dangerous one.
- The diff-coverage gate cannot see a pre-existing line whose *behaviour* is wrong;
  only boundary tests can.
- A judge's verdict is a parsed artifact. "The engine could not read the answer" and
  "the judge lacked context" are different failures and must render differently, or
  the author is sent looking for a missing document instead of at the model's output.
