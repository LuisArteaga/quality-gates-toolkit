#!/usr/bin/env python3
"""Unit tests for scripts/review.py findings and reasoning parsing."""

import contextlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch

# review.py is importable via the conftest.py sys.path bootstrap (scripts dir).

import review  # noqa: E402


class ParseFindingsTests(unittest.TestCase):
    def _build_response(self, content: str) -> str:
        return json.dumps({"choices": [{"message": {"content": content}}]})

    def test_valid_finding_inside_block_retracted_outside_is_ignored(self):
        """AC: valid JSON inside <findings>; retracted JSON/text outside is ignored."""
        raw = self._build_response(
            "Hmm, I see something.\n"
            '{"severity": "bug", "message": "not really a bug, retracted"}\n'
            "Wait, that's wrong. Let me reconsider.\n"
            "<reasoning>This is the reasoning</reasoning>\n"
            "<findings>\n"
            '{"severity": "security", "message": "hardcoded secret in config"}\n'
            "</findings>\n"
        )
        verdict, reasoning, findings = review.evaluate_response(raw)
        self.assertEqual(verdict, "Fail")
        self.assertEqual(reasoning, "This is the reasoning")
        self.assertEqual(findings, ["security|hardcoded secret in config"])

    def test_empty_findings_block_passes(self):
        """AC: empty <findings> block results in Pass."""
        raw = self._build_response(
            "<reasoning>Some thinking text</reasoning>\n<findings>\n</findings>\n"
        )
        verdict, reasoning, findings = review.evaluate_response(raw)
        self.assertEqual(verdict, "Pass")
        self.assertEqual(reasoning, "Some thinking text")
        self.assertEqual(findings, [])

    def test_missing_tags_needs_review(self):
        """Edge case: no <findings> and no <reasoning> tags -> treat as Needs Review."""
        raw = self._build_response(
            'Draft: I think there is a bug.\n{"severity": "bug", "message": "maybe"}\n'
        )
        verdict, reasoning, findings = review.evaluate_response(raw)
        self.assertEqual(verdict, "Needs Review")
        self.assertIn("Response lacks both", reasoning)
        self.assertEqual(findings, [])

    def test_multiple_findings_blocks_uses_last(self):
        """Edge case: multiple blocks; only last block is parsed."""
        raw = self._build_response(
            "<reasoning>thinking</reasoning>\n"
            "<findings>\n"
            '{"severity": "bug", "message": "first block"}\n'
            "</findings>\n"
            "Wait, let me correct.\n"
            "<findings>\n"
            '{"severity": "bug", "message": "second block"}\n'
            "</findings>\n"
        )
        verdict, reasoning, findings = review.evaluate_response(raw)
        self.assertEqual(verdict, "Fail")
        self.assertEqual(findings, ["bug|second block"])

    def test_malformed_xml_no_closing_tag_extracts_to_end(self):
        """Edge case: missing closing tag extracts from opening to end."""
        raw = self._build_response(
            "<reasoning>reasoning block without close\n"
            "<findings>\n"
            '{"severity": "bug", "message": "no closing tag"}\n'
            "Some trailing text"
        )
        verdict, reasoning, findings = review.evaluate_response(raw)
        self.assertEqual(verdict, "Fail")
        self.assertEqual(findings, ["bug|no closing tag"])


class SystemPromptTests(unittest.TestCase):
    def test_prompt_instructs_findings_xml_block(self):
        """AC: system prompts instruct LLM to wrap findings and reasoning in tags."""
        self.assertIn("<findings>", review.SYSTEM_PROMPT_SECURITY)
        self.assertIn("</findings>", review.SYSTEM_PROMPT_SECURITY)
        self.assertIn("<reasoning>", review.SYSTEM_PROMPT_SECURITY)
        self.assertIn("</reasoning>", review.SYSTEM_PROMPT_SECURITY)

        self.assertIn("<findings>", review.SYSTEM_PROMPT_ARCH)
        self.assertIn("</findings>", review.SYSTEM_PROMPT_ARCH)
        self.assertIn("<reasoning>", review.SYSTEM_PROMPT_ARCH)
        self.assertIn("</reasoning>", review.SYSTEM_PROMPT_ARCH)

        self.assertIn("<findings>", review.SYSTEM_PROMPT_SYNTAX_LINT)
        self.assertIn("<reasoning>", review.SYSTEM_PROMPT_TEST_COVERAGE)


class JudgeNeutralityTests(unittest.TestCase):
    """Tests for the shared Judge Neutrality preamble (issue #61, ADR-0014
    enhancement). Verifies the neutrality frame is prepended to every judge
    prompt that reaches the LLM, without coupling it to the judge-specific
    ``augment_judge_prompt`` dispatch."""

    def test_neutrality_constant_has_judge_neutrality_header(self):
        """AC: the shared preamble carries the labeled neutrality section."""
        self.assertIn(
            "=== 0. JUDGE NEUTRALITY ===", review.JUDGE_NEUTRALITY_INSTRUCTIONS
        )

    def test_every_judge_prompt_prepends_neutrality(self):
        """AC: each JUDGE_PROMPTS entry starts with the neutrality preamble,
        so the frame precedes the judge-specific criteria for every judge."""
        for key in review.JUDGE_KEYS:
            self.assertTrue(
                review.JUDGE_PROMPTS[key].startswith(
                    review.JUDGE_NEUTRALITY_INSTRUCTIONS
                ),
                f"judge {key} prompt is missing the neutrality preamble",
            )

    def test_base_criteria_constants_untouched_by_neutrality(self):
        """AC: the raw criteria constants remain the judge-specific criteria
        (neutrality is layered on via JUDGE_PROMPTS, not baked into the
        constants) — keeps augment_judge_prompt's contract intact."""
        for base_prompt in (
            review.SYSTEM_PROMPT_SYNTAX_LINT,
            review.SYSTEM_PROMPT_TEST_COVERAGE,
            review.SYSTEM_PROMPT_ARCH,
            review.SYSTEM_PROMPT_SECURITY,
        ):
            self.assertNotIn("=== 0. JUDGE NEUTRALITY ===", base_prompt)

    def test_neutrality_covers_each_named_bias(self):
        """AC: the preamble names every bias it mitigates (metadata/ID,
        anchoring, ADR over-weighting, position-within-findings)."""
        text = review.JUDGE_NEUTRALITY_INSTRUCTIONS
        self.assertIn("author", text.lower())
        self.assertIn("automated agent", text.lower())
        self.assertIn("intended", text.lower())
        self.assertIn("compliance", text.lower())
        self.assertIn("position", text.lower())


class FindingPromotionThresholdTests(unittest.TestCase):
    """Tests for the finding-promotion threshold (issue #46, D-0023).

    A judge finding means "this must change before merge"; every other
    observation belongs in the reasoning. The threshold is stated in the
    prompts (prompt-level) rather than resolved by gating on severity, so the
    verdict stays severity-blind and the verdict-block contract is unchanged."""

    def test_every_judge_prompt_states_the_threshold_in_its_scoring_rule(self):
        """AC: each judge states the promotion rule where PASS/FAIL is defined,
        so a cosmetic note is routed to reasoning instead of failing the PR."""
        for key in review.JUDGE_KEYS:
            prompt = review.JUDGE_PROMPTS[key]
            self.assertIn(review.FINDING_PROMOTION_RULE, prompt, key)
            scoring_rule = prompt.index("=== 3. SCORING RULE ===")
            edge_cases = prompt.index("=== 4. EDGE-CASE HANDLING ===")
            threshold = prompt.index(review.FINDING_PROMOTION_RULE)
            self.assertTrue(scoring_rule < threshold < edge_cases, key)

    def test_base_criteria_constants_carry_the_threshold(self):
        """AC: the threshold also reaches consumers importing a base criteria
        constant directly (both surfaces are exported by the judge API)."""
        for base_prompt in (
            review.SYSTEM_PROMPT_SYNTAX_LINT,
            review.SYSTEM_PROMPT_TEST_COVERAGE,
            review.SYSTEM_PROMPT_ARCH,
            review.SYSTEM_PROMPT_SECURITY,
        ):
            self.assertIn(review.FINDING_PROMOTION_RULE, base_prompt)

    def test_threshold_names_the_merge_block_test_and_reasoning_destination(self):
        """AC: the rule gives the judge a decision test (would a maintainer be
        entitled to block the merge?) and says where non-blocking items go."""
        text = review.FINDING_PROMOTION_RULE.lower()
        self.assertIn("must change before merge", text)
        self.assertIn("block the merge", text)
        self.assertIn("reasoning", text)
        self.assertIn("minor observations", text)
        self.assertIn("downgrade", text)

    def test_threshold_survives_judge_augmentation(self):
        """AC: the augmentation appends to the prompt without displacing the
        threshold — the augmented prompt is what actually reaches the LLM."""
        for key in review.JUDGE_KEYS:
            syntax_result = None
            arch_context = ""
            marker = ""
            if key == "syntax_lint":
                syntax_result, marker = (True, [], 1), "SYNTAX VERIFICATION"
            elif key == "architecture":
                arch_context, marker = "ADR-0002 body", "ARCHITECTURE CONTEXT"
            prompt = review.augment_judge_prompt(
                key, review.JUDGE_PROMPTS[key], syntax_result, arch_context
            )
            self.assertIn(review.FINDING_PROMOTION_RULE, prompt, key)
            if marker:
                self.assertIn(marker, prompt, key)

    def test_verdict_stays_severity_blind(self):
        """AC: the threshold is enforced by the prompt, not by downgrading
        verdicts — a severity label never softens a finding (D-0023), so the
        gate-level alternative is pinned as deliberately not taken."""
        for severity in ("nit", "suggestion", "warning", "bug", "error", "security"):
            content = (
                "<reasoning>notes</reasoning>\n<findings>\n"
                + json.dumps({"severity": severity, "message": "observation"})
                + "\n</findings>\n"
            )
            raw = json.dumps({"choices": [{"message": {"content": content}}]})
            verdict, reasoning, findings = review.evaluate_response(raw)
            self.assertEqual(verdict, "Fail", severity)
            self.assertEqual(reasoning, "notes", severity)
            self.assertEqual(findings, [f"{severity}|observation"], severity)


def _build_judges_data(
    statuses,
    findings=None,
    errors=None,
    reasoning="ok",
    fallbacks=None,
    final_models=None,
):
    """Helper to assemble a judges_data dict for build_review_body tests."""
    data = {}
    for key in review.JUDGE_KEYS:
        data[key] = {
            "name": review.JUDGE_DISPLAY_NAMES[key],
            "prompt": review.JUDGE_PROMPTS[key],
            "status": statuses.get(key, "PASS"),
            "reasoning": reasoning,
            "findings": (findings or {}).get(key, []),
            "error": (errors or {}).get(key, None),
            "used_fallback": (fallbacks or {}).get(key, False),
            "final_model": (final_models or {}).get(key, None),
        }
    return data


class BuildReviewBodyTests(unittest.TestCase):
    def test_build_review_body_all_pass(self):
        """AC: all 4 PASS -> hidden block lists 4 'KEY: PASS' lines + summary table."""
        statuses = {k: "PASS" for k in review.JUDGE_KEYS}
        data = _build_judges_data(statuses)
        body = review.build_review_body(data)
        self.assertIn("### 🤖 Automated LLM PR Judges Summary", body)
        self.assertIn("| Judge | Status | Details |", body)
        for key in review.JUDGE_KEYS:
            self.assertIn(f"{key}: PASS\n", body)
        self.assertIn("<!-- llm-pr-review-verdicts", body)
        self.assertIn("-->", body)

    def test_build_review_body_mixed(self):
        """AC: one FAIL (findings), one NEEDS REVIEW (error), two PASS -> exact statuses."""
        statuses = {
            "syntax_lint": "PASS",
            "test_coverage": "FAIL",
            "architecture": "PASS",
            "security": "NEEDS REVIEW",
        }
        findings = {"test_coverage": ["error|[Q1] missing tests"]}
        errors = {"security": "boom"}
        data = _build_judges_data(statuses, findings=findings, errors=errors)
        body = review.build_review_body(data)
        self.assertIn("syntax_lint: PASS\n", body)
        self.assertIn("test_coverage: FAIL\n", body)
        self.assertIn("architecture: PASS\n", body)
        self.assertIn("security: NEEDS REVIEW\n", body)
        self.assertIn("1 violation found.", body)
        self.assertIn("Check failed to run: boom", body)

    def test_build_review_body_empty_diff(self):
        """AC: empty-diff outcome -> 4 PASS lines."""
        statuses = {k: "PASS" for k in review.JUDGE_KEYS}
        data = _build_judges_data(statuses, reasoning="")
        body = review.build_review_body(data)
        for key in review.JUDGE_KEYS:
            self.assertIn(f"{key}: PASS\n", body)


class ClipChunkTests(unittest.TestCase):
    def test_clip_chunk_under_budget_unchanged(self):
        """AC: chunk under budget is returned unchanged."""
        chunk = "short chunk content"
        self.assertEqual(review.clip_chunk(chunk, 1000), chunk)

    def test_clip_chunk_over_budget_appends_note(self):
        """AC: chunk > budget chars clipped with NOTE appended."""
        budget = 100
        chunk = "x" * (budget + 50)
        result = review.clip_chunk(chunk, budget)
        self.assertTrue(result.startswith("x" * budget))
        self.assertIn(f"[NOTE: diff truncated to {budget} chars", result)
        self.assertIn("return NEEDS REVIEW if you cannot fully evaluate", result)


class SplitDiffByFileTests(unittest.TestCase):
    def test_empty_diff_returns_empty_list(self):
        """AC: empty or whitespace-only diff → empty list."""
        self.assertEqual(review.split_diff_by_file(""), [])
        self.assertEqual(review.split_diff_by_file("   \n  "), [])

    def test_single_file_diff(self):
        """AC: single file diff → one (filename, section) pair."""
        diff = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,3 +1,4 @@\n"
            " line1\n"
            "+added\n"
            " line3\n"
        )
        chunks = review.split_diff_by_file(diff)
        self.assertEqual(len(chunks), 1)
        filename, section = chunks[0]
        self.assertEqual(filename, "foo.py")
        self.assertIn("diff --git a/foo.py b/foo.py", section)

    def test_multi_file_diff_preserves_order(self):
        """AC: multi-file diff → chunks in natural git diff order."""
        diff = (
            "diff --git a/alpha.py b/alpha.py\n"
            "--- a/alpha.py\n"
            "+++ b/alpha.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "diff --git a/beta.py b/beta.py\n"
            "--- a/beta.py\n"
            "+++ b/beta.py\n"
            "@@ -1 +1 @@\n"
            "-x\n"
            "+y\n"
        )
        chunks = review.split_diff_by_file(diff)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0][0], "alpha.py")
        self.assertEqual(chunks[1][0], "beta.py")

    def test_renamed_file_extracts_destination(self):
        """AC: renamed file → destination filename (b/ path)."""
        diff = (
            "diff --git a/old_name.py b/new_name.py\n"
            "rename from old_name.py\n"
            "rename to new_name.py\n"
            "@@ -1 +1 @@\n"
            "-x\n"
            "+y\n"
        )
        chunks = review.split_diff_by_file(diff)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0][0], "new_name.py")

    def test_new_file_mode(self):
        """AC: new file mode → included as a chunk with correct filename."""
        diff = (
            "diff --git a/new_file.py b/new_file.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new_file.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+line1\n"
            "+line2\n"
        )
        chunks = review.split_diff_by_file(diff)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0][0], "new_file.py")


class PackIntoBatchesTests(unittest.TestCase):
    def test_empty_chunks_returns_empty_list(self):
        """AC: no chunks → no batches."""
        self.assertEqual(review.pack_into_batches([], 1000), [])

    def test_single_chunk_under_budget_one_batch(self):
        """AC: single chunk under budget → one batch."""
        chunks = [("foo.py", "diff --git ...")]
        batches = review.pack_into_batches(chunks, 1000)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0], "diff --git ...")

    def test_multiple_small_files_pack_into_one_batch(self):
        """AC: multiple small files fit in one batch."""
        chunks = [
            ("a.py", "section_a"),
            ("b.py", "section_b"),
        ]
        batches = review.pack_into_batches(chunks, 1000)
        self.assertEqual(len(batches), 1)
        self.assertIn("section_a", batches[0])
        self.assertIn("section_b", batches[0])

    def test_overflow_creates_new_batch(self):
        """AC: adding a file that overflows starts a new batch."""
        chunks = [
            ("a.py", "x" * 60),
            ("b.py", "x" * 60),  # 60 + 1 + 60 = 121 > 100
        ]
        batches = review.pack_into_batches(chunks, 100)
        self.assertEqual(len(batches), 2)

    def test_oversized_single_file_clipped(self):
        """AC: a single file exceeding budget is clipped and gets its own batch."""
        big_section = "x" * 200
        chunks = [("big.py", big_section)]
        batches = review.pack_into_batches(chunks, 100)
        self.assertEqual(len(batches), 1)
        self.assertIn("[NOTE: diff truncated to 100 chars", batches[0])

    def test_oversized_file_flushes_current_batch(self):
        """AC: oversized file flushes the in-progress batch before clipping."""
        chunks = [
            ("a.py", "small"),
            ("big.py", "x" * 200),
            ("b.py", "small2"),
        ]
        batches = review.pack_into_batches(chunks, 100)
        # batch 1: "small", batch 2: clipped big, batch 3: "small2"
        self.assertEqual(len(batches), 3)
        self.assertEqual(batches[0], "small")
        self.assertIn("[NOTE: diff truncated", batches[1])
        self.assertEqual(batches[2], "small2")


class VerifyPythonSyntaxTests(unittest.TestCase):
    """Tests for the deterministic py_compile pre-check (ADR-0025)."""

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.test_dir.name).resolve()

    def tearDown(self):
        self.test_dir.cleanup()

    def _make_file(self, name: str, content: str) -> str:
        path = self.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)

    def _diff_for(self, *filenames: str) -> str:
        parts = []
        for fn in filenames:
            parts.append(
                f"diff --git a/{fn} b/{fn}\n"
                f"--- a/{fn}\n"
                f"+++ b/{fn}\n"
                f"@@ -1 +1 @@\n"
                f"-old\n"
                f"+new\n"
            )
        return "".join(parts)

    def test_valid_python_returns_pass(self):
        """AC: syntactically valid Python files → all_passed=True, no errors."""
        self._make_file("good.py", "x = 1\n")
        diff = self._diff_for("good.py")
        passed, errors, checked = review.verify_python_syntax(str(self.workspace), diff)
        self.assertTrue(passed)
        self.assertEqual(errors, [])
        self.assertEqual(checked, 1)

    def test_syntax_error_returns_fail(self):
        """AC: file with IndentationError → all_passed=False, error reported."""
        self._make_file("bad.py", "def foo():\nx = 1\n")
        diff = self._diff_for("bad.py")
        passed, errors, checked = review.verify_python_syntax(str(self.workspace), diff)
        self.assertFalse(passed)
        self.assertEqual(len(errors), 1)
        self.assertIn("bad.py", errors[0])
        self.assertEqual(checked, 1)

    def test_non_python_files_skipped(self):
        """AC: .json/.md files in diff → files_checked=0, no errors."""
        diff = self._diff_for("config.json", "readme.md")
        passed, errors, checked = review.verify_python_syntax(str(self.workspace), diff)
        self.assertTrue(passed)
        self.assertEqual(errors, [])
        self.assertEqual(checked, 0)

    def test_deleted_file_skipped(self):
        """AC: file not in workspace (deleted) → silently skipped."""
        diff = self._diff_for("deleted.py")
        passed, errors, checked = review.verify_python_syntax(str(self.workspace), diff)
        self.assertTrue(passed)
        self.assertEqual(errors, [])
        self.assertEqual(checked, 0)

    def test_mixed_valid_and_invalid(self):
        """AC: one valid + one invalid → all_passed=False, checked=2."""
        self._make_file("good.py", "x = 1\n")
        self._make_file("bad.py", "def foo():\nx = 1\n")
        diff = self._diff_for("good.py", "bad.py")
        passed, errors, checked = review.verify_python_syntax(str(self.workspace), diff)
        self.assertFalse(passed)
        self.assertEqual(len(errors), 1)
        self.assertIn("bad.py", errors[0])
        self.assertEqual(checked, 2)

    def test_nested_path(self):
        """AC: file in subdirectory → resolved and checked."""
        self._make_file("orchestrator/nested.py", "def bar():\n    pass\n")
        diff = self._diff_for("orchestrator/nested.py")
        passed, errors, checked = review.verify_python_syntax(str(self.workspace), diff)
        self.assertTrue(passed)
        self.assertEqual(checked, 1)


class ProviderPayloadTests(unittest.TestCase):
    def test_build_openrouter_provider_with_routing(self):
        """AC: routing list -> order lowercased, allow_fallbacks False."""
        provider = review.build_openrouter_provider(["Together", "SiliconFlow"])
        self.assertEqual(
            provider, {"order": ["together", "siliconflow"], "allow_fallbacks": False}
        )

    def test_build_openrouter_provider_no_routing(self):
        """AC: routing None -> no provider payload."""
        self.assertIsNone(review.build_openrouter_provider(None))
        self.assertIsNone(review.build_openrouter_provider([]))

    def test_build_payload_merges_options_and_temperature(self):
        """AC: options merged as top-level keys; temperature present; provider from routing."""
        payload = review.build_payload(
            "m",
            [{"role": "user", "content": "hi"}],
            ["DeepInfra"],
            0.2,
            {"thinking": "max"},
        )
        self.assertEqual(payload["model"], "m")
        self.assertEqual(payload["temperature"], 0.2)
        self.assertEqual(payload["thinking"], "max")
        self.assertEqual(
            payload["provider"], {"order": ["deepinfra"], "allow_fallbacks": False}
        )

    def test_build_payload_no_routing_no_provider_key(self):
        """AC: routing None -> no 'provider' key in payload."""
        payload = review.build_payload("m", [], None, 0.0, None)
        self.assertNotIn("provider", payload)

    def test_build_payload_includes_configured_max_tokens(self):
        """AC (issue #38): a configured completion cap reaches the payload."""
        payload = review.build_payload(
            "m", [{"role": "user", "content": "d"}], None, 0.0, None, 1024
        )
        self.assertEqual(payload["max_tokens"], 1024)

    def test_build_payload_omits_max_tokens_when_unset(self):
        """AC: no cap resolved -> the key is absent, provider default applies."""
        payload = review.build_payload("m", [], None, 0.0, None)
        self.assertNotIn("max_tokens", payload)

    def test_build_payload_options_still_merge_last(self):
        """AC: the options escape hatch keeps its last-wins semantics."""
        payload = review.build_payload(
            "m", [], None, 0.0, {"thinking": "max", "max_tokens": 99}, 1024
        )
        self.assertEqual(payload["thinking"], "max")
        self.assertEqual(payload["max_tokens"], 99)


class RunJudgeTests(unittest.TestCase):
    def _build_llm_response(self, content: str) -> str:
        return json.dumps({"choices": [{"message": {"content": content}}]})

    def test_run_judge_llm_failure_returns_needs_review(self):
        """AC: LLM call raising -> status 'NEEDS REVIEW' with error captured."""

        def raising_caller(judge_key, prompt, diff, api_key):
            raise RuntimeError("LLM down")

        status, reasoning, findings, error, used_fb, final_m = review.run_judge(
            "security",
            review.SYSTEM_PROMPT_SECURITY,
            "diff",
            "key",
            llm_caller=raising_caller,
        )
        self.assertEqual(status, "NEEDS REVIEW")
        self.assertEqual(findings, [])
        self.assertEqual(error, "LLM down")
        self.assertIn("Exception encountered: LLM down", reasoning)
        self.assertFalse(used_fb)

    def test_run_judge_pass_normalizes_uppercase(self):
        """AC: a Pass verdict normalizes to uppercase 'PASS'."""

        def passing_caller(judge_key, prompt, diff, api_key):
            return self._build_llm_response(
                "<reasoning>r</reasoning><findings></findings>"
            ), {"used_fallback": False, "final_model": "test-model", "attempt_count": 1}

        status, reasoning, findings, error, used_fb, final_m = review.run_judge(
            "syntax_lint",
            review.SYSTEM_PROMPT_SYNTAX_LINT,
            "diff",
            "key",
            llm_caller=passing_caller,
        )
        self.assertEqual(status, "PASS")
        self.assertIsNone(error)
        self.assertFalse(used_fb)
        self.assertEqual(final_m, "test-model")

    def test_run_judge_propagates_fallback_metadata(self):
        """AC: used_fallback=True from llm_caller propagates through run_judge."""

        def fallback_caller(judge_key, prompt, diff, api_key):
            return self._build_llm_response(
                "<reasoning>r</reasoning><findings></findings>"
            ), {
                "used_fallback": True,
                "final_model": "fallback-model",
                "attempt_count": 3,
            }

        status, reasoning, findings, error, used_fb, final_m = review.run_judge(
            "architecture",
            review.SYSTEM_PROMPT_ARCH,
            "diff",
            "key",
            llm_caller=fallback_caller,
        )
        self.assertEqual(status, "PASS")
        self.assertTrue(used_fb)
        self.assertEqual(final_m, "fallback-model")

    def test_run_judge_empty_diff_short_circuits_pass(self):
        """AC: empty diff → PASS without LLM call."""

        def never_called(judge_key, prompt, diff, api_key):
            raise AssertionError("LLM caller should not be invoked for empty diff")

        status, reasoning, findings, error, used_fb, final_m = review.run_judge(
            "security",
            review.SYSTEM_PROMPT_SECURITY,
            "",
            "key",
            llm_caller=never_called,
        )
        self.assertEqual(status, "PASS")
        self.assertEqual(findings, [])
        self.assertIsNone(error)
        self.assertFalse(used_fb)

    def test_run_judge_fast_path_single_call(self):
        """AC: diff under budget → one LLM call (fast path), no aggregation."""
        call_count = [0]

        def passing_caller(judge_key, prompt, diff, api_key):
            call_count[0] += 1
            return self._build_llm_response(
                "<reasoning>r</reasoning><findings></findings>"
            ), {"used_fallback": False, "final_model": "m", "attempt_count": 1}

        status, _, findings, error, _, _ = review.run_judge(
            "syntax_lint",
            review.SYSTEM_PROMPT_SYNTAX_LINT,
            "short diff",
            "key",
            llm_caller=passing_caller,
        )
        self.assertEqual(status, "PASS")
        self.assertEqual(call_count[0], 1)

    def test_run_judge_multi_batch_all_pass_aggregates_pass(self):
        """AC: multi-batch with all chunks PASS → judge PASS."""
        with patch.dict(os.environ, {"REVIEW_BATCH_BUDGET_CHARS": "50"}):
            diff = (
                "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
                "diff --git a/b.py b/b.py\n@@ -1 +1 @@\n-x\n+y\n"
            )

            def passing_caller(judge_key, prompt, diff, api_key):
                return self._build_llm_response(
                    "<reasoning>r</reasoning><findings></findings>"
                ), {"used_fallback": False, "final_model": "m", "attempt_count": 1}

            status, _, findings, error, _, _ = review.run_judge(
                "syntax_lint",
                review.SYSTEM_PROMPT_SYNTAX_LINT,
                diff,
                "key",
                llm_caller=passing_caller,
            )
            self.assertEqual(status, "PASS")
            self.assertEqual(findings, [])
            self.assertIsNone(error)

    def test_run_judge_multi_batch_one_fail_aggregates_fail(self):
        """AC: multi-batch with one chunk FAIL → judge FAIL, findings concatenated."""
        with patch.dict(os.environ, {"REVIEW_BATCH_BUDGET_CHARS": "50"}):
            diff = (
                "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
                "diff --git a/b.py b/b.py\n@@ -1 +1 @@\n-x\n+y\n"
            )
            responses = [
                self._build_llm_response(
                    "<reasoning>ok</reasoning><findings></findings>"
                ),
                self._build_llm_response(
                    "<reasoning>bad</reasoning><findings>\n"
                    '{"severity": "bug", "message": "issue found"}\n'
                    "</findings>"
                ),
            ]
            call_idx = [0]

            def mixed_caller(judge_key, prompt, diff, api_key):
                idx = min(call_idx[0], len(responses) - 1)
                call_idx[0] += 1
                return responses[idx], {
                    "used_fallback": False,
                    "final_model": "m",
                    "attempt_count": 1,
                }

            status, _, findings, error, _, _ = review.run_judge(
                "test_coverage",
                review.SYSTEM_PROMPT_TEST_COVERAGE,
                diff,
                "key",
                llm_caller=mixed_caller,
            )
            self.assertEqual(status, "FAIL")
            self.assertEqual(len(findings), 1)
            self.assertIn("issue found", findings[0])

    def test_run_judge_multi_batch_one_needs_review_aggregates_needs_review(self):
        """AC: multi-batch with one chunk NEEDS REVIEW → judge NEEDS REVIEW."""
        with patch.dict(os.environ, {"REVIEW_BATCH_BUDGET_CHARS": "50"}):
            diff = (
                "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
                "diff --git a/b.py b/b.py\n@@ -1 +1 @@\n-x\n+y\n"
            )
            responses = [
                self._build_llm_response(
                    "<reasoning>ok</reasoning><findings></findings>"
                ),
                self._build_llm_response(""),  # empty → NEEDS REVIEW
            ]
            call_idx = [0]

            def mixed_caller(judge_key, prompt, diff, api_key):
                idx = min(call_idx[0], len(responses) - 1)
                call_idx[0] += 1
                return responses[idx], {
                    "used_fallback": False,
                    "final_model": "m",
                    "attempt_count": 1,
                }

            status, _, findings, error, _, _ = review.run_judge(
                "security",
                review.SYSTEM_PROMPT_SECURITY,
                diff,
                "key",
                llm_caller=mixed_caller,
            )
            self.assertEqual(status, "NEEDS REVIEW")

    def test_run_judge_multi_batch_one_exception_aggregates_needs_review(self):
        """AC: multi-batch with one chunk raising → judge NEEDS REVIEW with error."""
        with patch.dict(os.environ, {"REVIEW_BATCH_BUDGET_CHARS": "50"}):
            diff = (
                "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
                "diff --git a/b.py b/b.py\n@@ -1 +1 @@\n-x\n+y\n"
            )
            call_idx = [0]

            def mixed_caller(judge_key, prompt, diff, api_key):
                call_idx[0] += 1
                if call_idx[0] == 1:
                    return self._build_llm_response(
                        "<reasoning>ok</reasoning><findings></findings>"
                    ), {"used_fallback": False, "final_model": "m", "attempt_count": 1}
                raise RuntimeError("chunk 2 failed")

            status, _, findings, error, _, _ = review.run_judge(
                "architecture",
                review.SYSTEM_PROMPT_ARCH,
                diff,
                "key",
                llm_caller=mixed_caller,
            )
            self.assertEqual(status, "NEEDS REVIEW")
            self.assertIsNotNone(error)
            self.assertIn("chunk 2 failed", error)

    def test_run_judge_multi_batch_fallback_propagates(self):
        """AC: any chunk using fallback → used_fallback=True, final_model=fallback."""
        with patch.dict(os.environ, {"REVIEW_BATCH_BUDGET_CHARS": "50"}):
            diff = (
                "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
                "diff --git a/b.py b/b.py\n@@ -1 +1 @@\n-x\n+y\n"
            )
            call_idx = [0]

            def mixed_caller(judge_key, prompt, diff, api_key):
                call_idx[0] += 1
                if call_idx[0] == 1:
                    return self._build_llm_response(
                        "<reasoning>r</reasoning><findings></findings>"
                    ), {
                        "used_fallback": False,
                        "final_model": "primary",
                        "attempt_count": 1,
                    }
                return self._build_llm_response(
                    "<reasoning>r</reasoning><findings></findings>"
                ), {
                    "used_fallback": True,
                    "final_model": "fallback-m",
                    "attempt_count": 3,
                }

            status, _, _, _, used_fb, final_m = review.run_judge(
                "security",
                review.SYSTEM_PROMPT_SECURITY,
                diff,
                "key",
                llm_caller=mixed_caller,
            )
            self.assertTrue(used_fb)
            self.assertEqual(final_m, "fallback-m")


class EmptyContentHelperTests(unittest.TestCase):
    def test_is_empty_content_empty_string(self):
        """AC: empty string content -> True."""
        raw = json.dumps({"choices": [{"message": {"content": ""}}]})
        self.assertTrue(review._is_empty_content(raw))

    def test_is_empty_content_whitespace_only(self):
        """AC: whitespace-only content -> True."""
        raw = json.dumps({"choices": [{"message": {"content": "  \n  "}}]})
        self.assertTrue(review._is_empty_content(raw))

    def test_is_empty_content_non_empty(self):
        """AC: non-empty content -> False."""
        raw = json.dumps({"choices": [{"message": {"content": "hello"}}]})
        self.assertFalse(review._is_empty_content(raw))


class CallLlmForReviewTests(unittest.TestCase):
    """Tests for the layered retry + fallback logic in call_llm_for_review.

    These tests mock _call_with_api_retry to control the response sequence
    without making real HTTP calls.
    """

    def _build_response(self, content: str) -> str:
        return json.dumps({"choices": [{"message": {"content": content}}]})

    @patch("review._call_with_api_retry")
    @patch("review.resolve_model_config")
    @patch("review.get_tracer")
    def test_first_attempt_non_empty_no_retry(self, mock_tracer, mock_cfg, mock_retry):
        """AC: non-empty content on first attempt -> no fallback, attempt_count=1."""
        from telemetry import DummyTracer

        mock_tracer.return_value = DummyTracer()
        mock_cfg.return_value = {
            "model": "primary-model",
            "routing": ["Together"],
            "temperature": 0.0,
            "options": None,
            "fallback_model": "fallback-model",
        }
        good_resp = self._build_response(
            "<reasoning>r</reasoning><findings></findings>"
        )
        mock_retry.return_value = good_resp

        body, metadata = review.call_llm_for_review(
            "syntax_lint", "sys prompt", "diff", "key"
        )
        self.assertEqual(body, good_resp)
        self.assertFalse(metadata["used_fallback"])
        self.assertEqual(metadata["final_model"], "primary-model")
        self.assertEqual(metadata["attempt_count"], 1)
        self.assertEqual(mock_retry.call_count, 1)

    def _request_payload_for_factory(self, factory: dict) -> dict:
        """Run one judge call end-to-end from a real config file and return
        the JSON payload actually sent to OpenRouter (issue #38: the config
        file is the only input, so a resolved-then-dropped field is caught).
        """
        from telemetry import DummyTracer

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "factory.json"
            path.write_text(json.dumps(factory), encoding="utf-8")
            response = MagicMock()
            response.status = 200
            response.read.return_value = self._build_response(
                "<reasoning>r</reasoning><findings></findings>"
            ).encode()
            context = MagicMock()
            context.__enter__.return_value = response
            context.__exit__.return_value = False
            env = {
                "REVIEW_CONFIG_PATH": str(path),
                "AGENT_MODEL": "",
                "SYNTAX_LINT_MODEL": "",
            }
            with patch("review.get_tracer", return_value=DummyTracer()):
                with patch.dict(os.environ, env):
                    with patch("urllib.request.urlopen", return_value=context) as call:
                        review.call_llm_for_review("syntax_lint", "sys", "diff", "key")
        return json.loads(call.call_args.args[0].data)

    def test_omitted_max_tokens_sends_the_toolkit_default_in_the_payload(self):
        """AC (issue #38): a config without max_tokens is still bounded."""
        payload = self._request_payload_for_factory(
            {"syntax_lint": {"model": "vendor/primary-model"}}
        )
        self.assertEqual(payload["model"], "vendor/primary-model")
        self.assertEqual(payload["max_tokens"], 32768)

    def test_configured_max_tokens_sends_the_consumer_value_in_the_payload(self):
        """AC (issue #38): a configured cap wins over the toolkit default."""
        payload = self._request_payload_for_factory(
            {"syntax_lint": {"model": "vendor/primary-model", "max_tokens": 1024}}
        )
        self.assertEqual(payload["max_tokens"], 1024)

    @patch("review._call_with_api_retry")
    @patch("review.resolve_model_config")
    @patch("review.get_tracer")
    def test_empty_then_nudge_succeeds(self, mock_tracer, mock_cfg, mock_retry):
        """AC: empty first, non-empty on nudge -> 2 attempts, no fallback."""
        from telemetry import DummyTracer

        mock_tracer.return_value = DummyTracer()
        mock_cfg.return_value = {
            "model": "primary-model",
            "routing": ["Together"],
            "temperature": 0.0,
            "options": None,
            "fallback_model": "fallback-model",
        }
        empty_resp = self._build_response("")
        good_resp = self._build_response(
            "<reasoning>r</reasoning><findings></findings>"
        )
        mock_retry.side_effect = [empty_resp, good_resp]

        body, metadata = review.call_llm_for_review(
            "syntax_lint", "sys prompt", "diff", "key"
        )
        self.assertEqual(body, good_resp)
        self.assertFalse(metadata["used_fallback"])
        self.assertEqual(metadata["final_model"], "primary-model")
        self.assertEqual(metadata["attempt_count"], 2)

    @patch("review._call_with_api_retry")
    @patch("review.resolve_model_config")
    @patch("review.get_tracer")
    def test_empty_twice_then_fallback_succeeds(
        self, mock_tracer, mock_cfg, mock_retry
    ):
        """AC: two empties then fallback succeeds -> used_fallback=True, 3 attempts."""
        from telemetry import DummyTracer

        mock_tracer.return_value = DummyTracer()
        mock_cfg.return_value = {
            "model": "primary-model",
            "routing": ["Together"],
            "temperature": 0.0,
            "options": None,
            "fallback_model": "fallback-model",
        }
        empty_resp = self._build_response("")
        good_resp = self._build_response(
            "<reasoning>r</reasoning><findings></findings>"
        )
        mock_retry.side_effect = [empty_resp, empty_resp, good_resp]

        body, metadata = review.call_llm_for_review(
            "syntax_lint", "sys prompt", "diff", "key"
        )
        self.assertEqual(body, good_resp)
        self.assertTrue(metadata["used_fallback"])
        self.assertEqual(metadata["final_model"], "fallback-model")
        self.assertEqual(metadata["attempt_count"], 3)

    @patch("review._call_with_api_retry")
    @patch("review.resolve_model_config")
    @patch("review.get_tracer")
    def test_empty_all_three_returns_empty(self, mock_tracer, mock_cfg, mock_retry):
        """AC: all 3 attempts empty -> returns empty body (evaluate_response will
        produce NEEDS REVIEW)."""
        from telemetry import DummyTracer

        mock_tracer.return_value = DummyTracer()
        mock_cfg.return_value = {
            "model": "primary-model",
            "routing": ["Together"],
            "temperature": 0.0,
            "options": None,
            "fallback_model": "fallback-model",
        }
        empty_resp = self._build_response("")
        mock_retry.side_effect = [empty_resp, empty_resp, empty_resp]

        body, metadata = review.call_llm_for_review(
            "syntax_lint", "sys prompt", "diff", "key"
        )
        self.assertTrue(review._is_empty_content(body))
        self.assertTrue(metadata["used_fallback"])
        self.assertEqual(metadata["final_model"], "fallback-model")
        self.assertEqual(metadata["attempt_count"], 3)

    @patch("review._call_with_api_retry")
    @patch("review.resolve_model_config")
    @patch("review.get_tracer")
    def test_no_fallback_model_skips_attempt_3(self, mock_tracer, mock_cfg, mock_retry):
        """AC: no fallback_model configured -> only 2 attempts, returns empty."""
        from telemetry import DummyTracer

        mock_tracer.return_value = DummyTracer()
        mock_cfg.return_value = {
            "model": "primary-model",
            "routing": ["Together"],
            "temperature": 0.0,
            "options": None,
            "fallback_model": None,
        }
        empty_resp = self._build_response("")
        mock_retry.side_effect = [empty_resp, empty_resp]

        body, metadata = review.call_llm_for_review(
            "syntax_lint", "sys prompt", "diff", "key"
        )
        self.assertTrue(review._is_empty_content(body))
        self.assertFalse(metadata["used_fallback"])
        self.assertEqual(metadata["attempt_count"], 2)

    @patch("review._call_with_api_retry")
    @patch("review.resolve_model_config")
    @patch("review.get_tracer")
    def test_fallback_uses_no_routing_no_options(
        self, mock_tracer, mock_cfg, mock_retry
    ):
        """AC: fallback call uses routing=None, options=None, temperature=0.0."""
        from telemetry import DummyTracer

        mock_tracer.return_value = DummyTracer()
        mock_cfg.return_value = {
            "model": "primary-model",
            "routing": ["Together"],
            "temperature": 0.5,
            "options": {"thinking": "max"},
            "fallback_model": "fallback-model",
        }
        empty_resp = self._build_response("")
        good_resp = self._build_response(
            "<reasoning>r</reasoning><findings></findings>"
        )
        mock_retry.side_effect = [empty_resp, empty_resp, good_resp]

        review.call_llm_for_review("security", "sys", "diff", "key")

        # Third call (fallback) should have routing=None, options=None, temp=0.0
        third_call = mock_retry.call_args_list[2]
        # _call_with_api_retry(model, messages, api_key, routing, temperature, options)
        self.assertIsNone(third_call.args[3])  # routing
        self.assertIsNone(third_call.args[5])  # options
        self.assertEqual(third_call.args[4], 0.0)  # temperature


class FallbackIndicatorTests(unittest.TestCase):
    def test_fallback_indicator_shown_when_used(self):
        """AC: used_fallback=True -> visible fallback notice in review body."""
        statuses = {k: "PASS" for k in review.JUDGE_KEYS}
        fallbacks = {"security": True}
        final_models = {"security": "z-ai/glm-5.2"}
        data = _build_judges_data(
            statuses, fallbacks=fallbacks, final_models=final_models
        )
        body = review.build_review_body(data)
        self.assertIn("⚠️ **Fallback Model Used**", body)
        self.assertIn("z-ai/glm-5.2", body)

    def test_no_fallback_indicator_when_not_used(self):
        """AC: used_fallback=False -> no fallback notice in review body."""
        statuses = {k: "PASS" for k in review.JUDGE_KEYS}
        data = _build_judges_data(statuses)
        body = review.build_review_body(data)
        self.assertNotIn("Fallback Model Used", body)

    def test_fallback_indicator_only_for_specific_judge(self):
        """AC: only the judge that used fallback shows the indicator."""
        statuses = {k: "PASS" for k in review.JUDGE_KEYS}
        fallbacks = {"test_coverage": True}
        final_models = {"test_coverage": "z-ai/glm-5.2"}
        data = _build_judges_data(
            statuses, fallbacks=fallbacks, final_models=final_models
        )
        body = review.build_review_body(data)
        # Should appear in the test_coverage section
        self.assertIn("Fallback Model Used", body)
        # Count occurrences — should be exactly 1
        self.assertEqual(body.count("Fallback Model Used"), 1)


class TestCoveragePromptTests(unittest.TestCase):
    """AC tests for the refocused semantic Test Coverage prompt (issue #149).

    The judge evaluates semantics only - Assertion Strength, Edge Cases,
    Implementation Leakage - from the diff alone. Changed-line coverage
    arithmetic moved to the deterministic Diff Coverage Gate (ADR-0052),
    which superseded the ADR-0030 CI-output transport; no augmentation
    path exists anymore.
    """

    def test_prompt_contains_three_semantic_criteria(self):
        """AC: exactly the three new semantic criteria are defined."""
        text = review.SYSTEM_PROMPT_TEST_COVERAGE
        self.assertIn("ASSERTION STRENGTH", text)
        self.assertIn("EDGE CASES", text)
        self.assertIn("IMPLEMENTATION LEAKAGE", text)

    def test_prompt_has_no_legacy_q_labels_or_criterion_names(self):
        """AC: no legacy Test-Coverage Q1-Q4 labels or criterion names remain."""
        text = review.SYSTEM_PROMPT_TEST_COVERAGE
        self.assertNotIn("Q1", text)
        self.assertNotIn("Q2", text)
        self.assertNotIn("Q3", text)
        self.assertNotIn("Q4", text)
        self.assertNotIn("Test Presence", text)
        self.assertNotIn("Test Quality", text)
        self.assertNotIn("Test Execution", text)
        self.assertNotIn("Coverage of Changed Code", text)

    def test_retired_ci_coverage_machinery_removed(self):
        """AC: no CI-output loading/augmentation path exists at all."""
        for name in (
            "load_ci_coverage_output",
            "build_test_coverage_ci_augmentation",
            "_get_ci_coverage_output_budget",
            "_tail_clip",
            "CI_COVERAGE_OUTPUT_MAX_CHARS",
        ):
            self.assertFalse(hasattr(review, name), f"{name} should be removed")

    def test_verdict_and_output_contract_unchanged(self):
        """AC: verdict vocabulary and reasoning/findings blocks are intact."""
        text = review.SYSTEM_PROMPT_TEST_COVERAGE
        self.assertIn("<reasoning>", text)
        self.assertIn("</reasoning>", text)
        self.assertIn("<findings>", text)
        self.assertIn("</findings>", text)
        # Scoring rule still maps findings to FAIL and empty findings to PASS.
        self.assertIn("PASS:", text)
        self.assertIn("FAIL:", text)


class AugmentJudgePromptTests(unittest.TestCase):
    """Tests for the pure per-judge prompt-augmentation dispatch extracted from
    main() (issue #45)."""

    def _syntax_result(self, passed, errors=None, checked=1):
        return (passed, errors or [], checked)

    def test_security_judge_unchanged(self):
        """AC: a judge with no augmentation (security) -> prompt unchanged."""
        self.assertEqual(
            review.augment_judge_prompt(
                "security",
                "base",
                self._syntax_result(True),
                "ARCH",
            ),
            "base",
        )

    def test_syntax_lint_passed_appends_pass_message(self):
        """AC: syntax_lint with passing py_compile -> PASS verification block."""
        result = review.augment_judge_prompt(
            "syntax_lint",
            "base",
            self._syntax_result(True, checked=2),
            "",
        )
        self.assertIn("=== DETERMINISTIC SYNTAX VERIFICATION ===", result)
        self.assertIn("Q1 (Syntax Validation) is PASS", result)
        self.assertIn("base", result)

    def test_syntax_lint_failed_appends_fail_message_with_errors(self):
        """AC: syntax_lint with failing py_compile -> FAIL block listing errors."""
        result = review.augment_judge_prompt(
            "syntax_lint",
            "base",
            self._syntax_result(
                False, errors=["foo.py: bad", "bar.py: worse"], checked=2
            ),
            "",
        )
        self.assertIn("Q1 (Syntax Validation) is FAIL", result)
        self.assertIn("- foo.py: bad", result)
        self.assertIn("- bar.py: worse", result)

    def test_syntax_lint_zero_checked_no_augmentation(self):
        """AC: syntax_lint with checked==0 (no .py files) -> no augmentation."""
        result = review.augment_judge_prompt(
            "syntax_lint",
            "base",
            self._syntax_result(True, checked=0),
            "",
        )
        self.assertEqual(result, "base")

    def test_syntax_lint_none_result_no_augmentation(self):
        """AC: syntax_lint with syntax_result=None -> no augmentation."""
        result = review.augment_judge_prompt("syntax_lint", "base", None, "")
        self.assertEqual(result, "base")

    def test_architecture_with_context_appends_context(self):
        """AC: architecture judge with non-empty arch_context -> context appended."""
        result = review.augment_judge_prompt(
            "architecture", "base", None, "ADR-0014: ..."
        )
        self.assertIn("=== REPOSITORY ARCHITECTURE CONTEXT ===", result)
        self.assertIn("ADR-0014: ...", result)

    def test_architecture_without_context_appends_fallback(self):
        """AC: architecture judge with empty arch_context -> fallback message."""
        result = review.augment_judge_prompt("architecture", "base", None, "")
        self.assertIn("Falling back to default rules", result)

    def test_test_coverage_no_augmentation_path(self):
        """AC: test_coverage receives its base prompt unchanged - the CI-output
        augmentation path was retired with ADR-0030 (issue #149); semantics
        are evaluated from the diff alone."""
        result = review.augment_judge_prompt("test_coverage", "base", None, "")
        self.assertEqual(result, "base")


class MainTests(unittest.TestCase):
    """Focused tests for review.main() covering the wiring (augment_judge_prompt
    dispatch, the judge loop, submit, exit paths).

    External I/O (telemetry, stdin, env, LLM call, gh CLI) is mocked, mirroring
    the test_entrypoint.py pattern. Covers the main()-level lines touched by
    issue #45 so the changed wiring is exercised by passing tests.
    """

    def _run_main(self, judge_statuses, diff="some diff"):
        """Invoke review.main() with all externals mocked.

        ``judge_statuses`` is a dict mapping judge_key -> status string
        returned by the mocked run_judge. Returns the captured call args so
        assertions can inspect the review action and body.
        """
        from telemetry import DummyTracer

        submit_calls = []

        def fake_submit(pr_number, action, body):
            submit_calls.append((pr_number, action, body))

        def make_run_judge():
            def fake_run_judge(
                judge_key, prompt, diff_arg, api_key, usage_records=None
            ):
                status = judge_statuses.get(judge_key, "PASS")
                return (status, "reasoning", [], None, False, "model-x")

            return fake_run_judge

        env = {
            "PR_NUMBER": "42",
            "GH_PAT": "tok",
            "OPENROUTER_API_KEY": "or-key",
            "GITHUB_WORKSPACE": "/tmp/nonexistent_workspace_xyz",
        }
        # Ensure GH_TOKEN not set so GH_PAT is used.
        clean_env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN",)}
        clean_env.update(env)

        stdin_mock = MagicMock()
        stdin_mock.read.return_value = diff

        with (
            patch("review.init_telemetry"),
            patch("review.get_tracer", return_value=DummyTracer()),
            patch("review.verify_python_syntax", return_value=(True, [], 0)),
            patch("review.load_architecture_context", return_value="ARCH_CTX"),
            patch("review.run_judge", side_effect=make_run_judge()),
            patch("review.submit_github_review", side_effect=fake_submit),
            patch("sys.exit") as mock_exit,
            patch("sys.stdin", stdin_mock),
            patch.dict(os.environ, clean_env, clear=True),
        ):
            review.main()

        return mock_exit, submit_calls

    def test_main_all_pass_approves_and_exits_zero(self):
        """AC: all judges PASS -> review action 'approve', submit called, exit(0)."""
        mock_exit, submit_calls = self._run_main({k: "PASS" for k in review.JUDGE_KEYS})
        mock_exit.assert_called_once_with(0)
        self.assertEqual(len(submit_calls), 1)
        pr_num, action, body = submit_calls[0]
        self.assertEqual(pr_num, "42")
        self.assertEqual(action, "approve")
        # Hidden verdict block lists all four judges as PASS.
        for key in review.JUDGE_KEYS:
            self.assertIn(f"{key}: PASS", body)

    def test_main_any_failed_requests_changes_and_exits_one(self):
        """AC: any judge FAIL -> review action 'request-changes', exit(1)."""
        statuses = {k: "PASS" for k in review.JUDGE_KEYS}
        statuses["test_coverage"] = "FAIL"
        mock_exit, submit_calls = self._run_main(statuses)
        mock_exit.assert_called_once_with(1)
        self.assertEqual(len(submit_calls), 1)
        _, action, _ = submit_calls[0]
        self.assertEqual(action, "request-changes")


class UndeliveredReviewBodyTests(unittest.TestCase):
    """Issue #47 / D-0024: a run that cannot post its review still hands the
    judges' verdicts over.

    Unlike ``MainTests``, ``sys.exit`` raises here exactly as it does in
    production. A non-raising exit mock would let ``main()`` continue past
    the guard, so the "nothing was persisted" assertions on the delivered
    paths would pass for the wrong reason.
    """

    def _run_main(
        self, judge_statuses, submit_error=None, body_path=None, kpi_error=None
    ):
        """Run review.main() with all externals mocked; return what it left behind.

        Returns ``(exit_codes, submit_calls, stdout)``. ``submit_calls``
        records every submission attempt *including* the one that raised, so
        the delivered bytes can be compared against what was persisted.
        """
        from telemetry import DummyTracer

        exit_codes: list[int] = []
        submit_calls = []

        def fake_exit(code=0):
            exit_codes.append(code)
            raise SystemExit(code)

        def fake_submit(pr_number, action, body):
            submit_calls.append((pr_number, action, body))
            if submit_error is not None:
                raise submit_error

        def fake_append_kpi_summary(judges_data):
            if kpi_error is not None:
                raise kpi_error

        def fake_run_judge(judge_key, prompt, diff_arg, api_key, usage_records=None):
            return (
                judge_statuses.get(judge_key, "PASS"),
                "reasoning",
                [],
                None,
                False,
                "model-x",
            )

        env = {
            "PR_NUMBER": "42",
            "GH_PAT": "tok",
            "OPENROUTER_API_KEY": "or-key",
            "GITHUB_WORKSPACE": "/tmp/nonexistent_workspace_xyz",
        }
        if body_path is not None:
            env["REVIEW_BODY_PATH"] = body_path
        clean_env = {k: v for k, v in os.environ.items() if k != "GH_TOKEN"}
        clean_env.update(env)

        stdin_mock = MagicMock()
        stdin_mock.read.return_value = "some diff"
        stdout = io.StringIO()

        with (
            patch("review.init_telemetry"),
            patch("review.get_tracer", return_value=DummyTracer()),
            patch("review.verify_python_syntax", return_value=(True, [], 0)),
            patch("review.load_architecture_context", return_value="ARCH_CTX"),
            patch("review.run_judge", side_effect=fake_run_judge),
            patch("review.submit_github_review", side_effect=fake_submit),
            patch("review.append_kpi_summary", side_effect=fake_append_kpi_summary),
            patch("sys.exit", side_effect=fake_exit),
            patch("sys.stdin", stdin_mock),
            patch("sys.stdout", stdout),
            patch.dict(os.environ, clean_env, clear=True),
        ):
            with self.assertRaises(SystemExit):
                review.main()

        return exit_codes, submit_calls, stdout.getvalue()

    def test_submission_failure_persists_the_body_byte_for_byte(self):
        """AC: a refused submission leaves the review body retrievable, with
        its verdict block intact and byte-identical to what would have been
        posted — a consumer never parses a second format."""
        statuses = {k: "PASS" for k in review.JUDGE_KEYS}
        statuses["test_coverage"] = "FAIL"
        error = RuntimeError(
            "Failed to submit GitHub review: gh pr review failed: refused"
        )
        with tempfile.TemporaryDirectory() as tmp:
            body_path = os.path.join(tmp, "nested", "review_body.md")
            exit_codes, submit_calls, stdout = self._run_main(
                statuses, submit_error=error, body_path=body_path
            )
            self.assertEqual(exit_codes, [1])
            self.assertEqual(len(submit_calls), 1)
            persisted = Path(body_path).read_bytes()

        self.assertEqual(persisted, submit_calls[0][2].encode("utf-8"))
        for key in review.JUDGE_KEYS:
            self.assertIn(f"{key}: {statuses[key]}", submit_calls[0][2])
        self.assertIn(submit_calls[0][2], stdout)
        self.assertIn(f"Review body written to {body_path}", stdout)

    def test_failure_annotation_names_every_judge_verdict(self):
        """AC: the verdicts are readable from the checks UI alone, without
        opening the log and without reading the toolkit's source."""
        statuses = {k: "PASS" for k in review.JUDGE_KEYS}
        statuses["architecture"] = "NEEDS REVIEW"
        with tempfile.TemporaryDirectory() as tmp:
            _, _, stdout = self._run_main(
                statuses,
                submit_error=RuntimeError("Failed to submit GitHub review: nope"),
                body_path=os.path.join(tmp, "review_body.md"),
            )

        expected = " ".join(f"{key}={statuses[key]}" for key in review.JUDGE_KEYS)
        self.assertIn(f"::error::LLM judge verdicts: {expected}", stdout)

    def test_green_run_persists_nothing(self):
        """AC: the artifact contract is "exists iff undelivered" — a
        delivered review leaves no body file behind."""
        with tempfile.TemporaryDirectory() as tmp:
            body_path = os.path.join(tmp, "review_body.md")
            exit_codes, _, stdout = self._run_main(
                {k: "PASS" for k in review.JUDGE_KEYS}, body_path=body_path
            )
            self.assertEqual(exit_codes, [0])
            self.assertFalse(os.path.exists(body_path))

        self.assertNotIn("::error::LLM judge verdicts", stdout)

    def test_failed_verdicts_with_a_delivered_review_persist_nothing(self):
        """AC: exit codes unchanged — a FAIL verdict still exits 1, and since
        the review WAS posted, the failure-path artifact has nothing to add."""
        statuses = {k: "PASS" for k in review.JUDGE_KEYS}
        statuses["security"] = "FAIL"
        with tempfile.TemporaryDirectory() as tmp:
            body_path = os.path.join(tmp, "review_body.md")
            exit_codes, submit_calls, stdout = self._run_main(
                statuses, body_path=body_path
            )
            self.assertEqual(exit_codes, [1])
            self.assertEqual(len(submit_calls), 1)
            self.assertFalse(os.path.exists(body_path))

        self.assertNotIn("::error::LLM judge verdicts", stdout)

    def test_guard_covers_a_failure_after_the_judges_spoke(self):
        """AC (#47 "the guard belongs where the body is built"): a failure
        that is not the submission call — here the step-summary write — takes
        the same path, so a future failure between the build and the exit
        cannot silently discard the verdicts."""
        with tempfile.TemporaryDirectory() as tmp:
            body_path = os.path.join(tmp, "review_body.md")
            exit_codes, submit_calls, stdout = self._run_main(
                {k: "PASS" for k in review.JUDGE_KEYS},
                body_path=body_path,
                kpi_error=RuntimeError("step summary is read-only"),
            )
            self.assertEqual(exit_codes, [1])
            self.assertEqual(submit_calls, [])
            self.assertTrue(os.path.exists(body_path))

        self.assertIn("step summary is read-only", stdout)
        for key in review.JUDGE_KEYS:
            self.assertIn(f"{key}=PASS", stdout)

    def test_unwritable_body_path_still_dumps_and_annotates(self):
        """AC: the file is one of three channels — a path that cannot be
        written must not cost the log dump or the annotation."""
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "not-a-directory")
            Path(blocker).write_text("")
            body_path = os.path.join(blocker, "review_body.md")
            exit_codes, _, stdout = self._run_main(
                {k: "PASS" for k in review.JUDGE_KEYS},
                submit_error=RuntimeError("Failed to submit GitHub review: nope"),
                body_path=body_path,
            )
            self.assertEqual(exit_codes, [1])

        self.assertIn(f"Could not write the review body to {body_path}", stdout)
        self.assertIn("::error::LLM judge verdicts: ", stdout)
        self.assertIn("syntax_lint: PASS", stdout)

    def test_body_path_env_override_wins_and_default_is_the_working_directory(self):
        """The override exists so the path is a knob, not a hidden constant;
        the default is the step's working directory, which is what
        llm-pr-review.yml uploads."""
        with patch.dict(os.environ, {"REVIEW_BODY_PATH": "/tmp/explicit.md"}):
            self.assertEqual(review.resolve_review_body_path(), "/tmp/explicit.md")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                review.resolve_review_body_path(),
                os.path.join(os.getcwd(), review.REVIEW_BODY_FILENAME),
            )


class LayeredRetryPolicyTests(unittest.TestCase):
    """Tests for ``_run_layered_retry`` directly.

    The policy was extracted out of ``call_llm_for_review`` (issue #51) so the
    empty-content retry + fallback progression is testable in isolation,
    without resolving a Model Config or standing up a telemetry tracer. These
    tests mock only the transport (``_call_with_api_retry``).
    """

    def _build_response(self, content: str) -> str:
        return json.dumps({"choices": [{"message": {"content": content}}]})

    def _messages(self):
        return [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "diff"},
        ]

    @patch("review._call_with_api_retry")
    def test_non_empty_first_attempt_no_retry(self, mock_retry):
        """AC: non-empty on first attempt -> 1 call, no fallback."""
        good = self._build_response("<reasoning>r</reasoning><findings></findings>")
        mock_retry.return_value = good

        body, used_fb, final_m, attempts = review._run_layered_retry(
            "syntax_lint",
            "primary",
            self._messages(),
            "fallback",
            "key",
            ["Together"],
            0.0,
            None,
        )
        self.assertEqual(body, good)
        self.assertFalse(used_fb)
        self.assertEqual(final_m, "primary")
        self.assertEqual(attempts, 1)
        self.assertEqual(mock_retry.call_count, 1)

    @patch("review._call_with_api_retry")
    def test_empty_then_nudge_succeeds(self, mock_retry):
        """AC: empty first, non-empty on nudge -> 2 calls, no fallback."""
        empty = self._build_response("")
        good = self._build_response("<reasoning>r</reasoning><findings></findings>")
        mock_retry.side_effect = [empty, good]

        body, used_fb, final_m, attempts = review._run_layered_retry(
            "syntax_lint",
            "primary",
            self._messages(),
            "fallback",
            "key",
            ["Together"],
            0.0,
            None,
        )
        self.assertEqual(body, good)
        self.assertFalse(used_fb)
        self.assertEqual(final_m, "primary")
        self.assertEqual(attempts, 2)
        # The nudge must append EMPTY_CONTENT_INSTRUCTION to the last turn only.
        second_call_messages = mock_retry.call_args_list[1].args[1]
        self.assertEqual(second_call_messages[0]["content"], "sys")
        self.assertEqual(
            second_call_messages[1]["content"],
            "diff" + review.EMPTY_CONTENT_INSTRUCTION,
        )

    @patch("review._call_with_api_retry")
    def test_empty_twice_then_fallback_succeeds(self, mock_retry):
        """AC: two empties then fallback -> 3 calls, used_fallback, fallback config."""
        empty = self._build_response("")
        good = self._build_response("<reasoning>r</reasoning><findings></findings>")
        mock_retry.side_effect = [empty, empty, good]

        body, used_fb, final_m, attempts = review._run_layered_retry(
            "security",
            "primary",
            self._messages(),
            "fallback",
            "key",
            ["Together"],
            0.5,
            {"thinking": "max"},
        )
        self.assertEqual(body, good)
        self.assertTrue(used_fb)
        self.assertEqual(final_m, "fallback")
        self.assertEqual(attempts, 3)
        # ADR-0021: fallback call uses routing=None, options=None, temperature=0.0.
        third = mock_retry.call_args_list[2]
        self.assertEqual(third.args[0], "fallback")  # model
        self.assertIsNone(third.args[3])  # routing
        self.assertEqual(third.args[4], 0.0)  # temperature
        self.assertIsNone(third.args[5])  # options
        # Fallback reuses the ORIGINAL messages, not the nudged ones.
        self.assertEqual(third.args[1][1]["content"], "diff")

    def _response_with_completion_tokens(self, content: str, completion: int) -> str:
        return json.dumps(
            {
                "choices": [{"message": {"content": content}}],
                "usage": {"completion_tokens": completion},
            }
        )

    @patch("review._call_with_api_retry")
    def test_cap_saturated_empty_goes_straight_to_the_fallback(self, mock_retry):
        """AC (issue #38): empty content at the cap skips the same-model nudge."""
        saturated = self._response_with_completion_tokens("", 32768)
        good = self._build_response("<reasoning>r</reasoning><findings></findings>")
        mock_retry.side_effect = [saturated, good]

        body, used_fb, final_m, attempts = review._run_layered_retry(
            "syntax_lint",
            "primary",
            self._messages(),
            "fallback",
            "key",
            ["Together"],
            0.0,
            None,
            32768,
        )
        self.assertEqual(body, good)
        self.assertTrue(used_fb)
        self.assertEqual(final_m, "fallback")
        self.assertEqual(attempts, 2)
        self.assertEqual(mock_retry.call_count, 2)
        # The second call is the FALLBACK model on the ORIGINAL prompt, not a
        # primary-model nudge with EMPTY_CONTENT_INSTRUCTION appended.
        second = mock_retry.call_args_list[1]
        self.assertEqual(second.args[0], "fallback")
        self.assertEqual(second.args[1][1]["content"], "diff")

    @patch("review._call_with_api_retry")
    def test_cap_saturated_empty_without_fallback_returns_empty_body(self, mock_retry):
        """AC: with no fallback the saturated empty body is returned as-is."""
        mock_retry.return_value = self._response_with_completion_tokens("", 32768)

        body, used_fb, final_m, attempts = review._run_layered_retry(
            "syntax_lint",
            "primary",
            self._messages(),
            None,
            "key",
            ["Together"],
            0.0,
            None,
            32768,
        )
        self.assertEqual(body, mock_retry.return_value)
        self.assertFalse(used_fb)
        self.assertEqual(final_m, "primary")
        self.assertEqual(attempts, 1)
        self.assertEqual(mock_retry.call_count, 1)

    @patch("review._call_with_api_retry")
    def test_empty_content_below_the_cap_still_nudges(self, mock_retry):
        """AC: a non-saturated empty response keeps the existing nudge path."""
        empty = self._response_with_completion_tokens("", 12000)
        good = self._build_response("<reasoning>r</reasoning><findings></findings>")
        mock_retry.side_effect = [empty, good]

        body, used_fb, final_m, attempts = review._run_layered_retry(
            "syntax_lint",
            "primary",
            self._messages(),
            "fallback",
            "key",
            ["Together"],
            0.0,
            None,
            32768,
        )
        self.assertEqual(body, good)
        self.assertFalse(used_fb)
        self.assertEqual(final_m, "primary")
        self.assertEqual(attempts, 2)
        nudge_messages = mock_retry.call_args_list[1].args[1]
        self.assertEqual(
            nudge_messages[1]["content"], "diff" + review.EMPTY_CONTENT_INSTRUCTION
        )

    @patch("review._call_with_api_retry")
    def test_capped_but_non_empty_response_is_not_a_saturation(self, mock_retry):
        """AC: a truncated verdict with tags still evaluates normally."""
        truncated = self._response_with_completion_tokens(
            "<reasoning>r</reasoning><findings></findings>", 32768
        )
        mock_retry.return_value = truncated

        body, used_fb, final_m, attempts = review._run_layered_retry(
            "syntax_lint",
            "primary",
            self._messages(),
            "fallback",
            "key",
            ["Together"],
            0.0,
            None,
            32768,
        )
        self.assertEqual(body, truncated)
        self.assertFalse(used_fb)
        self.assertEqual(attempts, 1)
        self.assertEqual(mock_retry.call_count, 1)

    @patch("review._call_with_api_retry")
    def test_max_tokens_persists_on_every_attempt(self, mock_retry):
        """AC (issue #38): the cap bounds the primary, nudge, AND fallback calls."""
        empty = self._build_response("")
        good = self._build_response("<reasoning>r</reasoning><findings></findings>")
        mock_retry.side_effect = [empty, empty, good]

        _, used_fb, final_m, _ = review._run_layered_retry(
            "security",
            "primary",
            self._messages(),
            "fallback",
            "key",
            ["Together"],
            0.0,
            {"thinking": "max"},
            4096,
        )
        self.assertTrue(used_fb)
        self.assertEqual(final_m, "fallback")
        self.assertEqual(
            [call.args[6] for call in mock_retry.call_args_list], [4096, 4096, 4096]
        )

    @patch("review._call_with_api_retry")
    def test_no_fallback_model_exhausts_at_two(self, mock_retry):
        """AC: no fallback_model -> 2 attempts, returns empty, no fallback."""
        empty = self._build_response("")
        mock_retry.side_effect = [empty, empty]

        body, used_fb, final_m, attempts = review._run_layered_retry(
            "syntax_lint",
            "primary",
            self._messages(),
            None,
            "key",
            ["Together"],
            0.0,
            None,
        )
        self.assertTrue(review._is_empty_content(body))
        self.assertFalse(used_fb)
        self.assertEqual(final_m, "primary")
        self.assertEqual(attempts, 2)
        self.assertEqual(mock_retry.call_count, 2)

    @patch("review._call_with_api_retry")
    def test_primary_api_error_triggers_fallback(self, mock_retry):
        """AC: attempt 1 exhausts API retries -> fallback, 2 attempts
        (ADR-0021 amendment 2026-08-31: 429/5xx/timeouts reach the fallback)."""
        good = self._build_response("<reasoning>r</reasoning><findings></findings>")
        mock_retry.side_effect = [
            Exception("LLM review failed after retries. Last error: HTTP 429"),
            good,
        ]

        body, used_fb, final_m, attempts = review._run_layered_retry(
            "syntax_lint",
            "primary",
            self._messages(),
            "fallback",
            "key",
            ["Together"],
            0.0,
            None,
        )
        self.assertEqual(body, good)
        self.assertTrue(used_fb)
        self.assertEqual(final_m, "fallback")
        self.assertEqual(attempts, 2)
        self.assertEqual(mock_retry.call_count, 2)
        # ADR-0021: fallback call uses routing=None, options=None, temp=0.0.
        fallback_call = mock_retry.call_args_list[1]
        self.assertEqual(fallback_call.args[0], "fallback")
        self.assertIsNone(fallback_call.args[3])
        self.assertEqual(fallback_call.args[4], 0.0)
        self.assertIsNone(fallback_call.args[5])
        self.assertEqual(fallback_call.args[1][1]["content"], "diff")

    @patch("review._call_with_api_retry")
    def test_primary_api_error_no_fallback_reraises(self, mock_retry):
        """AC: API-error exhaustion with no fallback_model -> propagates."""
        mock_retry.side_effect = Exception("LLM review failed after retries")

        with self.assertRaises(Exception):
            review._run_layered_retry(
                "syntax_lint",
                "primary",
                self._messages(),
                None,
                "key",
                ["Together"],
                0.0,
                None,
            )
        self.assertEqual(mock_retry.call_count, 1)

    @patch("review._call_with_api_retry")
    def test_nudge_api_error_no_fallback_reraises(self, mock_retry):
        """AC: nudge exhausts API retries with no fallback_model -> propagates."""
        empty = self._build_response("")
        mock_retry.side_effect = [
            empty,
            Exception("LLM review failed after retries. Last error: HTTP 429"),
        ]

        with self.assertRaises(Exception):
            review._run_layered_retry(
                "syntax_lint",
                "primary",
                self._messages(),
                None,
                "key",
                ["Together"],
                0.0,
                None,
            )
        self.assertEqual(mock_retry.call_count, 2)

    @patch("review._call_with_api_retry")
    def test_nudge_api_error_triggers_fallback(self, mock_retry):
        """AC: empty first, nudge exhausts API retries -> fallback, 3 attempts."""
        empty = self._build_response("")
        good = self._build_response("<reasoning>r</reasoning><findings></findings>")
        mock_retry.side_effect = [
            empty,
            Exception("LLM review failed after retries. Last error: HTTP 429"),
            good,
        ]

        body, used_fb, final_m, attempts = review._run_layered_retry(
            "architecture",
            "primary",
            self._messages(),
            "fallback",
            "key",
            ["Together"],
            0.0,
            None,
        )
        self.assertEqual(body, good)
        self.assertTrue(used_fb)
        self.assertEqual(final_m, "fallback")
        self.assertEqual(attempts, 3)
        self.assertEqual(mock_retry.call_count, 3)

    @patch("review._call_with_api_retry")
    def test_fallback_api_error_propagates(self, mock_retry):
        """AC: fallback also exhausts API retries -> propagates to run_judge
        (which maps the error to NEEDS REVIEW)."""
        mock_retry.side_effect = [
            Exception("primary 429"),
            Exception("fallback 429"),
        ]

        with self.assertRaises(Exception):
            review._run_layered_retry(
                "syntax_lint",
                "primary",
                self._messages(),
                "fallback",
                "key",
                ["Together"],
                0.0,
                None,
            )
        self.assertEqual(mock_retry.call_count, 2)


class EnrichChunkIntegrationTests(unittest.TestCase):
    """Integration tests verifying enrichment is wired into run_judge."""

    def _build_llm_response(self, content: str) -> str:
        return json.dumps({"choices": [{"message": {"content": content}}]})

    def test_enrich_chunk_appends_function_context(self):
        """AC: _enrich_chunk enriches a diff with enclosing function context."""
        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            (workspace_path / "foo.py").write_text(
                "def bar():\n    return 1\n", encoding="utf-8"
            )
            diff = (
                "diff --git a/foo.py b/foo.py\n"
                "--- a/foo.py\n"
                "+++ b/foo.py\n"
                "@@ -1,1 +1,1 @@\n"
            )
            enriched = review._enrich_chunk(diff, str(workspace_path))
            self.assertIn("=== ENCLOSING FUNCTION CONTEXT ===", enriched)
            self.assertIn("--- foo.py :: bar ---", enriched)
            self.assertIn("def bar():", enriched)

    def test_enrich_chunk_no_workspace_returns_unchanged(self):
        """AC: _enrich_chunk with empty/nonexistent workspace returns diff."""
        diff = "diff --git a/missing.py b/missing.py\n@@ -1 +1 @@\n"
        enriched = review._enrich_chunk(diff, "/nonexistent/path")
        self.assertEqual(enriched, diff)

    def test_run_judge_fast_path_enriches_before_llm(self):
        """AC: run_judge enriches the diff before passing to llm_caller."""
        captured_diffs: list[str] = []

        def capturing_caller(judge_key, prompt, diff, api_key):
            captured_diffs.append(diff)
            return self._build_llm_response(
                "<reasoning>r</reasoning><findings></findings>"
            ), {"used_fallback": False, "final_model": "m", "attempt_count": 1}

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            (workspace_path / "app.py").write_text(
                "def greet():\n    return 'hi'\n", encoding="utf-8"
            )
            with patch.dict(os.environ, {"GITHUB_WORKSPACE": str(workspace_path)}):
                diff = (
                    "diff --git a/app.py b/app.py\n"
                    "--- a/app.py\n"
                    "+++ b/app.py\n"
                    "@@ -1,1 +1,1 @@\n"
                )
                review.run_judge(
                    "syntax_lint",
                    review.SYSTEM_PROMPT_SYNTAX_LINT,
                    diff,
                    "key",
                    llm_caller=capturing_caller,
                )

        self.assertEqual(len(captured_diffs), 1)
        self.assertIn("=== ENCLOSING FUNCTION CONTEXT ===", captured_diffs[0])
        self.assertIn("--- app.py :: greet ---", captured_diffs[0])

    def test_run_judge_multi_batch_path_enriches_before_llm(self):
        """AC: multi-batch path enriches EACH batch before passing to llm_caller.

        Closes the residual coverage gap from issue #55: the fast path's
        enrichment is asserted by ``test_run_judge_fast_path_enriches_before_llm``,
        but the multi-batch branch (split -> pack -> per-batch enrichment per
        ADR-0032) was only asserted for verdict aggregation, never for its
        enrichment behaviour. If the ``_enrich_chunk(batch)`` call were removed,
        no other test would fail.
        """
        captured: list[str] = []

        def capturing_caller(judge_key, prompt, diff, api_key):
            captured.append(diff)
            return self._build_llm_response(
                "<reasoning>r</reasoning><findings></findings>"
            ), {"used_fallback": False, "final_model": "m", "attempt_count": 1}

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            (workspace_path / "a.py").write_text(
                "def alpha():\n    return 1\n", encoding="utf-8"
            )
            (workspace_path / "b.py").write_text(
                "def beta():\n    return 2\n", encoding="utf-8"
            )
            # Budget chosen so each file-section fits (no clip) but two sections
            # cannot share a batch -> guarantees the multi-batch path (2 calls).
            env = {
                "GITHUB_WORKSPACE": str(workspace_path),
                "REVIEW_BATCH_BUDGET_CHARS": "100",
            }
            with patch.dict(os.environ, env):
                diff = (
                    "diff --git a/a.py b/a.py\n"
                    "--- a/a.py\n"
                    "+++ b/a.py\n"
                    "@@ -1,1 +1,1 @@\n"
                    "-old\n"
                    "+new\n"
                    "diff --git a/b.py b/b.py\n"
                    "--- b/b.py\n"
                    "+++ b/b.py\n"
                    "@@ -1,1 +1,1 @@\n"
                    "-x\n"
                    "+y\n"
                )
                review.run_judge(
                    "architecture",
                    review.SYSTEM_PROMPT_ARCH,
                    diff,
                    "key",
                    llm_caller=capturing_caller,
                )

        # Multi-batch path produced one call per batch.
        self.assertEqual(len(captured), 2)
        # Each batch received its own enclosing-function context block.
        for chunk in captured:
            self.assertIn("=== ENCLOSING FUNCTION CONTEXT ===", chunk)
        self.assertIn("--- a.py :: alpha ---", captured[0])
        self.assertIn("--- b.py :: beta ---", captured[1])


class ApiRetryPolicyTests(unittest.TestCase):
    """Tests for ``_call_with_api_retry``: the 429-aware retry policy.

    The transport-level retry must survive real OpenRouter quota
    exhaustion (minutes, not seconds), honor ``Retry-After``, fail fast
    on non-retryable HTTP 4xx, and give up with a descriptive error once
    the wall-clock budget is spent (ADR-0021, amended 2026-08-31).
    """

    def _good_body(self) -> tuple[int, str]:
        return 200, json.dumps(
            {"choices": [{"message": {"content": "<reasoning>r</reasoning>"}}]}
        )

    def _in_band_error(self) -> tuple[int, str]:
        return 200, json.dumps({"error": {"message": "rate limited"}})

    def _http_error(self, code: int, headers: dict | None = None):
        hdrs = Message()
        for key, value in (headers or {}).items():
            hdrs[key] = str(value)
        return urllib.error.HTTPError(
            url="https://openrouter.ai/api/v1/chat/completions",
            code=code,
            msg="error",
            hdrs=hdrs,
            fp=None,
        )

    def test_transport_serializes_max_tokens_into_the_request_body(self):
        """AC (issue #38): the completion cap reaches the HTTP request JSON."""
        response = MagicMock()
        response.status = 200
        response.read.return_value = b'{"choices": []}'
        context = MagicMock()
        context.__enter__.return_value = response
        context.__exit__.return_value = False
        with patch("urllib.request.urlopen", return_value=context) as mock_urlopen:
            review.call_openrouter_api(
                "m", [{"role": "user", "content": "d"}], "key", None, 0.0, None, 32768
            )
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(json.loads(request.data)["max_tokens"], 32768)

    @patch("review.call_openrouter_api")
    def test_api_retry_forwards_max_tokens_to_the_transport(self, mock_call):
        """AC: the retry wrapper passes the resolved cap down unchanged."""
        mock_call.return_value = self._good_body()
        review._call_with_api_retry(
            "m", [{"role": "user", "content": "d"}], "key", None, 0.0, None, 2048
        )
        self.assertEqual(mock_call.call_args.kwargs["max_tokens"], 2048)

    @patch("review.call_openrouter_api")
    def test_api_retry_omits_max_tokens_when_none(self, mock_call):
        """AC: an unresolved cap stays absent rather than sent as null."""
        mock_call.return_value = self._good_body()
        review._call_with_api_retry(
            "m", [{"role": "user", "content": "d"}], "key", None, 0.0, None
        )
        self.assertIsNone(mock_call.call_args.kwargs["max_tokens"])

    @patch("review.random.uniform", return_value=1.0)
    @patch("review.time.sleep")
    @patch("review.call_openrouter_api")
    def test_429_honors_retry_after_header(self, mock_call, mock_sleep, _mock_jitter):
        """AC: a Retry-After header overrides the escalating schedule."""
        mock_call.side_effect = [
            self._http_error(429, {"Retry-After": "30"}),
            self._good_body(),
        ]

        body = review._call_with_api_retry(
            "m", [{"role": "user", "content": "d"}], "key", None, 0.0, None
        )

        self.assertEqual(
            json.loads(body)["choices"][0]["message"]["content"],
            "<reasoning>r</reasoning>",
        )
        self.assertEqual(list(mock_sleep.call_args_list), [((30.0,),)])

    @patch("review.random.uniform", return_value=1.0)
    @patch("review.time.sleep")
    @patch("review.call_openrouter_api")
    def test_429_without_header_uses_first_schedule_delay(
        self, mock_call, mock_sleep, _mock_jitter
    ):
        """AC: no Retry-After -> the first scheduled delay (5s) applies."""
        mock_call.side_effect = [self._http_error(429), self._good_body()]

        review._call_with_api_retry(
            "m", [{"role": "user", "content": "d"}], "key", None, 0.0, None
        )

        self.assertEqual(list(mock_sleep.call_args_list), [((5.0,),)])

    @patch("review.random.uniform", return_value=1.0)
    @patch("review.time.sleep")
    @patch("review.call_openrouter_api")
    def test_server_error_is_retryable(self, mock_call, mock_sleep, _mock_jitter):
        """AC: 5xx responses join the retryable class."""
        mock_call.side_effect = [self._http_error(503), self._good_body()]

        body = review._call_with_api_retry(
            "m", [{"role": "user", "content": "d"}], "key", None, 0.0, None
        )

        self.assertIn("choices", body)
        mock_call.assert_called()

    @patch("review.time.sleep")
    @patch("review.call_openrouter_api")
    def test_non_retryable_400_fails_fast(self, mock_call, mock_sleep):
        """AC: plain 4xx failures raise immediately without retrying."""
        mock_call.side_effect = self._http_error(400)

        with self.assertRaises(Exception) as ctx:
            review._call_with_api_retry(
                "m", [{"role": "user", "content": "d"}], "key", None, 0.0, None
            )

        self.assertIn("non-retryable", str(ctx.exception))
        mock_sleep.assert_not_called()
        self.assertEqual(mock_call.call_count, 1)

    @patch("review.random.uniform", return_value=1.0)
    @patch("review.time.sleep")
    @patch("review.call_openrouter_api")
    def test_urlerror_is_retryable(self, mock_call, mock_sleep, _mock_jitter):
        """AC: connection errors and timeouts join the retryable class."""
        import urllib.error

        mock_call.side_effect = [
            urllib.error.URLError("connection refused"),
            self._good_body(),
        ]

        body = review._call_with_api_retry(
            "m", [{"role": "user", "content": "d"}], "key", None, 0.0, None
        )

        self.assertIn("choices", body)
        self.assertEqual(list(mock_sleep.call_args_list), [((5.0,),)])

    @patch("review.random.uniform", return_value=1.0)
    @patch("review.time.sleep")
    @patch("review.call_openrouter_api")
    def test_in_band_error_payload_is_retried(
        self, mock_call, mock_sleep, _mock_jitter
    ):
        """AC: an HTTP-200 body carrying an OpenRouter error is retried."""
        mock_call.side_effect = [
            (200, json.dumps({"error": {"message": "rate limited"}})),
            self._good_body(),
        ]

        body = review._call_with_api_retry(
            "m", [{"role": "user", "content": "d"}], "key", None, 0.0, None
        )

        self.assertIn("choices", body)
        self.assertEqual(mock_call.call_count, 2)

    @patch("review.random.uniform", return_value=1.0)
    @patch("review.time.sleep")
    @patch("review.call_openrouter_api")
    def test_retry_budget_exhaustion_raises_descriptive_error(
        self, mock_call, mock_sleep, _mock_jitter
    ):
        """AC: schedule exhaustion names attempts, elapsed time, and error."""
        mock_call.side_effect = self._http_error(429)
        with patch.object(review, "API_RETRY_DELAYS_SECONDS", (1, 1)):
            with self.assertRaises(Exception) as ctx:
                review._call_with_api_retry(
                    "m",
                    [{"role": "user", "content": "d"}],
                    "key",
                    None,
                    0.0,
                    None,
                )

        self.assertIn("after 3 attempts", str(ctx.exception))
        self.assertIn("Last error:", str(ctx.exception))
        self.assertEqual(list(mock_sleep.call_args_list), [((1.0,),), ((1.0,),)])

    def test_append_step_summary_writes_header_then_rows(self):
        """AC: the step summary grows one header plus appended rows."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "step-summary.md")
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": path}):
                review.append_step_summary(["| m | 1 | 429 | retry |"])
                review.append_step_summary(["| m | 2 | 429 | give up |"])

            content = Path(path).read_text()
        self.assertEqual(content.count(review.STEP_SUMMARY_HEADER), 1)
        self.assertIn("| m | 1 | 429 | retry |", content)
        self.assertIn("| m | 2 | 429 | give up |", content)

    def test_append_step_summary_is_a_noop_outside_ci(self):
        """AC: without GITHUB_STEP_SUMMARY nothing is written anywhere."""
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                with patch.dict(os.environ, {}, clear=True):
                    review.append_step_summary(["| m | 1 | 429 | retry |"])
                self.assertEqual(os.listdir(tmp), [])
            finally:
                os.chdir(original_cwd)

    def test_transport_success_returns_status_and_body(self):
        """AC: the transport returns (status, body) on a 200 response."""
        response = MagicMock()
        response.status = 200
        response.read.return_value = b'{"choices": []}'
        context = MagicMock()
        context.__enter__.return_value = response
        context.__exit__.return_value = False
        with patch("urllib.request.urlopen", return_value=context):
            status, body = review.call_openrouter_api(
                "m", [{"role": "user", "content": "d"}], "key"
            )
        self.assertEqual(status, 200)
        self.assertEqual(body, '{"choices": []}')

    def test_transport_wraps_http_error_with_retry_context(self):
        """AC: HTTPError becomes OpenRouterHTTPError with status/headers/body."""
        hdrs = Message()
        hdrs["Retry-After"] = "30"
        error = urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            429,
            "Too Many Requests",
            hdrs,
            io.BytesIO(b'{"error": {"code": 429}}'),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(review.OpenRouterHTTPError) as ctx:
                review.call_openrouter_api(
                    "m", [{"role": "user", "content": "d"}], "key"
                )
        self.assertEqual(ctx.exception.status, 429)
        self.assertEqual(ctx.exception.retry_after, 30)
        self.assertIn("HTTP 429", str(ctx.exception))
        self.assertIn("Retry-After: 30", str(ctx.exception))
        self.assertIn("error", str(ctx.exception))

    def test_transport_error_without_body_or_header(self):
        """AC: missing fp and Retry-After degrade to empty context."""
        error = urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            400,
            "Bad Request",
            Message(),
            None,
        )
        wrapped = review.OpenRouterHTTPError(error)
        self.assertEqual(wrapped.status, 400)
        self.assertIsNone(wrapped.retry_after)
        self.assertEqual(wrapped.body_snippet, "")
        self.assertEqual(str(wrapped), "HTTP 400 from OpenRouter")

    def test_parse_retry_after_rejects_garbage(self):
        """AC: non-numeric or missing headers parse as None."""
        hdrs = Message()
        hdrs["Retry-After"] = "soon"
        self.assertIsNone(review.parse_retry_after(hdrs))
        self.assertIsNone(review.parse_retry_after(None))

    def test_error_body_snippet_survives_a_raising_stream(self):
        """AC: a body stream that explodes reads as an empty snippet."""
        error = urllib.error.HTTPError(
            "https://openrouter.ai", 500, "oops", Message(), None
        )
        self.assertEqual(review.OpenRouterHTTPError(error).body_snippet, "")
        exploding = MagicMock()
        exploding.read.side_effect = OSError("stream gone")
        broken = urllib.error.HTTPError(
            "https://openrouter.ai", 500, "oops", Message(), exploding
        )
        self.assertEqual(review.OpenRouterHTTPError(broken).body_snippet, "")

    def test_connection_and_timeout_errors_are_retryable(self):
        """AC: bare socket-level failures join the retryable class."""
        self.assertEqual(
            review.classify_api_error(ConnectionError("reset")), (True, None)
        )
        self.assertEqual(
            review.classify_api_error(TimeoutError("timed out")), (True, None)
        )

    def test_next_wait_variants(self):
        """AC: Retry-After wins; no-jitter returns the schedule; no schedule is zero."""
        self.assertEqual(review.next_wait(5, 30, jitter=True), 30.0)
        self.assertEqual(review.next_wait(5, None, jitter=False), 5.0)
        self.assertEqual(review.next_wait(None, None, jitter=False), 0.0)

    def test_message_size_counts_content_chars(self):
        """AC: the debug helper measures the message content, tolerating absence."""
        self.assertEqual(review.message_size({"content": "abcd"}), 4)
        self.assertEqual(review.message_size({}), 0)

    @patch("review.REVIEW_DEBUG", True)
    @patch("review.time.sleep")
    @patch("review.call_openrouter_api")
    def test_debug_mode_logs_payload_shape_and_error_body(self, mock_call, mock_sleep):
        """AC: REVIEW_DEBUG surfaces the payload shape and the error body.

        Asserts on the captured stdout of the debug logs themselves: the
        request line must carry the payload shape (model, per-message
        content sizes) and the failure line must carry the 429 body
        snippet, so the test fails if the debug logging is removed.
        """
        # An already-wrapped transport error (as the real flow produces it)
        # carries the body snippet the debug log prints.
        wrapped = review.OpenRouterHTTPError(
            urllib.error.HTTPError(
                "https://openrouter.ai/api/v1/chat/completions",
                429,
                "Too Many Requests",
                Message(),
                io.BytesIO(b'{"error": {"code": 429}}'),
            )
        )
        mock_call.side_effect = [
            wrapped,
            self._good_body(),
        ]
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            review._call_with_api_retry(
                "m", [{"role": "user", "content": "diff"}], "key", None, 0.0, None
            )
        output = captured.getvalue()
        self.assertIn("[DEBUG] OpenRouter request model=m", output)
        self.assertIn("messages=[4]", output)
        self.assertIn("[DEBUG] error body:", output)
        self.assertIn('"code": 429', output)

    @patch("review.REVIEW_DEBUG", False)
    @patch("review.time.sleep")
    @patch("review.call_openrouter_api")
    def test_debug_logging_is_gated_off_by_default(self, mock_call, mock_sleep):
        """AC: with REVIEW_DEBUG disabled no debug lines are printed."""
        wrapped = review.OpenRouterHTTPError(
            urllib.error.HTTPError(
                "https://openrouter.ai/api/v1/chat/completions",
                429,
                "Too Many Requests",
                Message(),
                io.BytesIO(b'{"error": {"code": 429}}'),
            )
        )
        mock_call.side_effect = [
            wrapped,
            self._good_body(),
        ]
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            review._call_with_api_retry(
                "m", [{"role": "user", "content": "diff"}], "key", None, 0.0, None
            )
        self.assertNotIn("[DEBUG]", captured.getvalue())

    @patch("review.time.sleep")
    @patch("review.call_openrouter_api")
    def test_retry_after_larger_than_budget_fails_without_sleeping(
        self, mock_call, mock_sleep
    ):
        """AC: a wait exceeding the remaining budget fails with the next wait named."""
        mock_call.side_effect = self._http_error(429, {"Retry-After": "3600"})
        with patch.object(review, "API_RETRY_BUDGET_SECONDS", 60):
            with self.assertRaises(Exception) as ctx:
                review._call_with_api_retry(
                    "m", [{"role": "user", "content": "d"}], "key", None, 0.0, None
                )
        self.assertIn("next wait", str(ctx.exception))
        mock_sleep.assert_not_called()

    def test_append_step_summary_silently_ignores_write_errors(self):
        """AC: an unwritable summary path is a no-op, never a crash."""
        with patch.dict(
            os.environ, {"GITHUB_STEP_SUMMARY": "/nonexistent-dir-xyz/summary.md"}
        ):
            review.append_step_summary(["| m | a | e | retry |"])


class CallDeadlineTests(unittest.TestCase):
    """Per-call wall-clock ceiling and the timeout re-route (issue #44, D-0022).

    ``urlopen(timeout=...)`` bounds individual socket operations, not a slow
    generation, so the ceiling is enforced around the transport call. A call
    abandoned at the ceiling must be retried with the pinned route released
    (pinned routing disables provider failover), logged distinctly, and
    counted in the KPI record. Hung calls block on an ``Event`` rather than
    ``time.sleep`` because these tests patch ``review.time.sleep``.
    """

    def _good_body(self) -> tuple[int, str]:
        return 200, json.dumps(
            {"choices": [{"message": {"content": "<reasoning>r</reasoning>"}}]}
        )

    def _usage_body(self) -> tuple[int, str]:
        return 200, json.dumps(
            {
                "model": "test-model",
                "provider": "together",
                "choices": [
                    {
                        "message": {
                            "content": "<reasoning>r</reasoning><findings></findings>"
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 34,
                    "total_tokens": 46,
                    "cost": 0.0012,
                },
            }
        )

    def _hung_transport(self, release: threading.Event):
        """A transport call that outlives any sane ceiling until released."""

        def _call(*args, **kwargs):
            release.wait(5)
            return self._good_body()

        return _call

    @staticmethod
    def _sequence(*items):
        """A ``side_effect`` that yields each item, calling it when callable.

        An iterable ``side_effect`` returns its items verbatim, so a hung
        transport has to be dispatched by a callable instead.
        """
        pending = list(items)

        def _dispatch(*args, **kwargs):
            item = pending.pop(0)
            return item(*args, **kwargs) if callable(item) else item

        return _dispatch

    @patch("review.random.uniform", return_value=1.0)
    @patch("review.time.sleep")
    @patch("review.call_openrouter_api")
    def test_call_over_the_ceiling_is_abandoned_and_retried_on_the_auto_route(
        self, mock_call, mock_sleep, _mock_jitter
    ):
        """AC: an over-ceiling call is abandoned, re-routed to auto, and retried."""
        release = threading.Event()
        mock_call.side_effect = self._sequence(
            self._hung_transport(release), self._good_body()
        )

        with patch.object(review, "CALL_TIMEOUT_SECONDS", 0.05):
            started = time.monotonic()
            body = review._call_with_api_retry(
                "m", [{"role": "user", "content": "d"}], "key", ["Novita"], 0.0, None
            )
            elapsed = time.monotonic() - started
        release.set()

        self.assertIn("choices", body)
        self.assertEqual(mock_call.call_count, 2)
        # The pinned route is released on the retry, the model is not.
        self.assertEqual(mock_call.call_args_list[0].kwargs["routing"], ["Novita"])
        self.assertIsNone(mock_call.call_args_list[1].kwargs["routing"])
        self.assertEqual(mock_call.call_args_list[1].args[0], "m")
        # AC: bounded wall-clock — the abandoned call costs the ceiling, not
        # the hung provider's full latency.
        self.assertLess(elapsed, 1.0)

    @patch("review.random.uniform", return_value=1.0)
    @patch("review.time.sleep")
    @patch("review.call_openrouter_api")
    def test_timeout_logs_model_provider_elapsed_and_the_reroute(
        self, mock_call, mock_sleep, _mock_jitter
    ):
        """AC: the abandoned call is logged distinctly, with its route named."""
        release = threading.Event()
        mock_call.side_effect = self._sequence(
            self._hung_transport(release), self._good_body()
        )

        with tempfile.TemporaryDirectory() as tmp:
            summary = os.path.join(tmp, "step-summary.md")
            captured = io.StringIO()
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary}):
                with patch.object(review, "CALL_TIMEOUT_SECONDS", 0.05):
                    with contextlib.redirect_stdout(captured):
                        review._call_with_api_retry(
                            "m",
                            [{"role": "user", "content": "d"}],
                            "key",
                            ["Novita"],
                            0.0,
                            None,
                        )
            summary_text = Path(summary).read_text()
        release.set()

        timeout_line = next(
            line
            for line in captured.getvalue().splitlines()
            if "[OPENROUTER] timeout" in line
        )
        self.assertIn("model=m", timeout_line)
        self.assertIn("provider=novita", timeout_line)
        self.assertIn("after=", timeout_line)
        self.assertIn("next_route=auto", timeout_line)
        self.assertIn("retry in 5s on auto route", summary_text)

    @patch("review.random.uniform", return_value=1.0)
    @patch("review.time.sleep")
    @patch("review.call_openrouter_api")
    def test_abandoned_attempts_are_collected_for_the_kpi_record(
        self, mock_call, mock_sleep, _mock_jitter
    ):
        """AC: each abandoned attempt appends its detail to the collector."""
        release = threading.Event()
        mock_call.side_effect = self._sequence(
            self._hung_transport(release), self._good_body()
        )
        records: list[dict] = []

        with patch.object(review, "CALL_TIMEOUT_SECONDS", 0.05):
            review._call_with_api_retry(
                "m",
                [{"role": "user", "content": "d"}],
                "key",
                ["Novita"],
                0.0,
                None,
                None,
                records,
            )
        release.set()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["model"], "m")
        self.assertEqual(records[0]["provider"], "novita")
        self.assertGreaterEqual(records[0]["after_seconds"], 0.05)

    def test_timeout_is_classified_retryable(self):
        """AC: a timeout joins the retryable class, with no server-sent wait."""
        error = review.OpenRouterCallTimeout("m", 300.0, ["Novita"])
        self.assertEqual(review.classify_api_error(error), (True, None))
        self.assertIn("300s per-call ceiling", str(error))

    def test_route_label_names_pinned_routes_and_auto(self):
        """AC: the log field names the requested route, lowercased as sent."""
        self.assertEqual(review.route_label(None), "auto")
        self.assertEqual(review.route_label([]), "auto")
        self.assertEqual(review.route_label(["Novita", "Z.AI"]), "novita+z.ai")

    @patch("review.call_openrouter_api")
    def test_transport_failure_is_reraised_on_the_calling_thread(self, mock_call):
        """AC: the wrapper preserves transport exceptions for classification."""
        mock_call.side_effect = urllib.error.URLError("connection refused")
        with patch.object(review, "CALL_TIMEOUT_SECONDS", 5):
            with self.assertRaises(urllib.error.URLError):
                review._call_transport_with_deadline(
                    "m", [{"role": "user", "content": "d"}], "key", None, 0.0, None
                )

    def test_call_llm_for_review_reports_the_timeout_count(self):
        """AC: the abandoned attempt reaches the judge's KPI record and metadata."""
        from telemetry import DummyTracer

        release = threading.Event()
        with (
            patch("review.get_tracer", return_value=DummyTracer()),
            patch("review.resolve_model_config") as mock_cfg,
            patch("review.call_openrouter_api") as mock_call,
            patch("review.time.sleep"),
            patch("review.random.uniform", return_value=1.0),
            patch.object(review, "CALL_TIMEOUT_SECONDS", 0.05),
        ):
            mock_cfg.return_value = {
                "model": "primary-model",
                "routing": ["Novita"],
                "temperature": 0.0,
                "options": None,
                "fallback_model": None,
                "max_tokens": 2048,
            }
            mock_call.side_effect = self._sequence(
                self._hung_transport(release), self._usage_body()
            )
            _, metadata = review.call_llm_for_review(
                "syntax_lint", "sys", "diff", "key"
            )
        release.set()

        self.assertEqual(metadata["timeouts"], 1)
        self.assertEqual(metadata["usage"]["timeouts"], 1)
        self.assertEqual(metadata["usage"]["provider"], "together")

    def test_multi_batch_path_stops_at_the_judge_wall_clock_budget(self):
        """AC (issue #44 constraint): the ceiling bounds a call, so the judge
        bounds the run — remaining batches are skipped, never silently PASS."""
        release = threading.Event()
        self.addCleanup(release.set)
        calls = [0]

        def caller(judge_key, prompt, diff, api_key):
            release.wait(0.3)
            calls[0] += 1
            return json.dumps(
                {"choices": [{"message": {"content": "<reasoning>r</reasoning>"}}]}
            ), {"used_fallback": False, "final_model": "m"}

        with patch.dict(os.environ, {"REVIEW_BATCH_BUDGET_CHARS": "50"}):
            diff = (
                "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
                "diff --git a/b.py b/b.py\n@@ -1 +1 @@\n-x\n+y\n"
            )
            with patch.object(review, "API_RETRY_BUDGET_SECONDS", 0.05):
                status, reasoning, findings, error, _, _ = review.run_judge(
                    "syntax_lint",
                    review.SYSTEM_PROMPT_SYNTAX_LINT,
                    diff,
                    "key",
                    llm_caller=caller,
                )

        self.assertEqual(status, "NEEDS REVIEW")
        self.assertEqual(calls[0], 1)
        self.assertIn("not evaluated", error)
        self.assertIn("not evaluated", reasoning)
        self.assertEqual(findings, [])


class UsageAccountingTests(unittest.TestCase):
    """KPI reporting for judge runs (ADR-0058): model/provider/tokens/cost."""

    def _usage_body(
        self,
        model="test-model",
        provider="together",
        prompt=12,
        completion=34,
        cost=0.0012,
    ) -> str:
        return json.dumps(
            {
                "model": model,
                "provider": provider,
                "choices": [
                    {
                        "message": {
                            "content": "<reasoning>r</reasoning><findings></findings>"
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": prompt + completion,
                    "prompt_tokens_details": {"cached_tokens": 3},
                    "completion_tokens_details": {"reasoning_tokens": 5},
                    "cost": cost,
                },
            }
        )

    def test_build_payload_requests_usage_accounting(self):
        """AC: every payload opts into OpenRouter usage accounting."""
        payload = review.build_payload(
            "m", [{"role": "user", "content": "d"}], None, 0.0, None
        )
        self.assertEqual(payload["usage"], {"include": True})

    def test_extract_usage_full_response(self):
        """AC: model, provider, tokens, breakdowns, and cost are extracted."""
        usage = review.extract_usage(self._usage_body())
        self.assertEqual(usage["model"], "test-model")
        self.assertEqual(usage["provider"], "together")
        self.assertEqual(usage["prompt_tokens"], 12)
        self.assertEqual(usage["completion_tokens"], 34)
        self.assertEqual(usage["total_tokens"], 46)
        self.assertEqual(usage["cached_tokens"], 3)
        self.assertEqual(usage["reasoning_tokens"], 5)
        self.assertEqual(usage["cost"], 0.0012)

    def test_extract_usage_without_usage_block(self):
        """AC: missing usage block -> model/provider kept, numeric fields None."""
        body = json.dumps(
            {"model": "m", "provider": "p", "choices": [{"message": {"content": "x"}}]}
        )
        usage = review.extract_usage(body)
        self.assertEqual(usage["model"], "m")
        self.assertEqual(usage["provider"], "p")
        self.assertIsNone(usage["prompt_tokens"])
        self.assertIsNone(usage["cost"])

    def test_extract_usage_invalid_json_returns_all_none(self):
        """AC: unparseable body -> all-None dict, never raises."""
        usage = review.extract_usage("not json at all")
        for field in review.USAGE_FIELDS:
            self.assertIsNone(usage[field])

    def test_extract_usage_non_dict_json_returns_all_none(self):
        """AC: valid JSON that is not an object (list/str/number) -> all Nones."""
        for body in ('["not", "an", "object"]', '"a string"', "42"):
            usage = review.extract_usage(body)
            for field in review.USAGE_FIELDS:
                self.assertIsNone(usage[field])

    def test_extract_usage_non_dict_details_is_defensive(self):
        """AC: malformed detail blocks -> Nones instead of a crash."""
        body = json.dumps(
            {
                "model": "m",
                "usage": {
                    "prompt_tokens": 1,
                    "prompt_tokens_details": "oops",
                    "completion_tokens_details": 42,
                },
            }
        )
        usage = review.extract_usage(body)
        self.assertEqual(usage["prompt_tokens"], 1)
        self.assertIsNone(usage["cached_tokens"])
        self.assertIsNone(usage["reasoning_tokens"])

    def test_merge_usages_sums_and_joins_distinct_endpoints(self):
        """AC: tokens/cost summed; distinct models/providers joined in order."""
        first = review.extract_usage(self._usage_body(model="m1", provider="p1"))
        second = review.extract_usage(
            self._usage_body(model="m2", provider="p2", prompt=8, completion=16)
        )
        merged = review.merge_usages([first, second])
        self.assertEqual(merged["prompt_tokens"], 20)
        self.assertEqual(merged["completion_tokens"], 50)
        self.assertEqual(merged["total_tokens"], 70)
        self.assertEqual(merged["cost"], 0.0024)
        self.assertEqual(merged["model"], "m1, m2")
        self.assertEqual(merged["provider"], "p1, p2")
        self.assertEqual(merged["llm_calls"], 2)

    def test_merge_usages_sums_explicit_call_counts(self):
        """AC: pre-merged per-judge dicts carry llm_calls explicitly."""
        pre_merged = {"prompt_tokens": 5, "llm_calls": 3}
        other = {"prompt_tokens": 7, "llm_calls": 2}
        merged = review.merge_usages([pre_merged, other])
        self.assertEqual(merged["prompt_tokens"], 12)
        self.assertEqual(merged["llm_calls"], 5)

    def test_merge_usages_empty_or_none_records(self):
        """AC: no records -> all-None usage with zero calls; Nones skipped."""
        for records in ([], [None]):
            merged = review.merge_usages(records)
            self.assertEqual(merged["llm_calls"], 0)
            self.assertIsNone(merged["prompt_tokens"])
            self.assertIsNone(merged["cost"])

    def test_merge_usages_ignores_non_numeric_and_boolean_values(self):
        """AC: strings and bools are not summed as numbers."""
        merged = review.merge_usages(
            [{"prompt_tokens": True, "cost": "0.5", "model": "m"}]
        )
        self.assertIsNone(merged["prompt_tokens"])
        self.assertIsNone(merged["cost"])
        self.assertEqual(merged["model"], "m")

    def test_render_kpi_table_rows_and_total(self):
        """AC: per-judge rows with formatted values plus a merged Total row."""
        judges_data = {
            key: {"name": key, "status": "PASS"} for key in review.JUDGE_KEYS
        }
        judges_data["syntax_lint"]["usage"] = {
            "model": "z-ai/glm-5.3-flash",
            "provider": "Z.AI",
            "prompt_tokens": 1234,
            "completion_tokens": 432,
            "reasoning_tokens": 77,
            "cost": 0.0045,
            "llm_calls": 1,
            "timeouts": 2,
        }
        judges_data["syntax_lint"]["duration_seconds"] = 12.34
        judges_data["security"]["usage"] = {
            "model": "moonshotai/kimi-k3",
            "provider": "Together",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "reasoning_tokens": 5,
            "cost": 0.002,
            "llm_calls": 2,
        }
        judges_data["security"]["duration_seconds"] = 8.0

        rows = review.render_kpi_table(judges_data)
        table = "\n".join(rows)
        self.assertIn("### 📊 Judge Usage & KPIs", table)
        self.assertIn("| Judge | Model | Provider | Input Tokens |", table)
        self.assertIn("| LLM Calls | Timeouts | Duration |", table)
        self.assertIn("| syntax_lint (`syntax_lint`) | z-ai/glm-5.3-flash |", table)
        self.assertIn("| 1,234 | 432 | 77 | $0.004500 | 1 | 2 | 12.3s |", table)
        self.assertIn("| moonshotai/kimi-k3 | Together |", table)
        self.assertIn("| 100 | 50 | 5 | $0.002000 | 2 | n/a | 8.0s |", table)
        self.assertIn("| **Total** | z-ai/glm-5.3-flash, moonshotai/kimi-k3 |", table)
        self.assertIn("| 1,334 | 482 | 82 | $0.006500 | 3 | 2 | 20.3s |", table)

    def test_render_kpi_table_renders_na_without_usage(self):
        """AC: legacy judges_data shape renders n/a cells instead of crashing."""
        statuses = {k: "PASS" for k in review.JUDGE_KEYS}
        table = "\n".join(review.render_kpi_table(_build_judges_data(statuses)))
        self.assertIn("### 📊 Judge Usage & KPIs", table)
        for key in review.JUDGE_KEYS:
            self.assertIn(
                f"(`{key}`) | n/a | n/a | n/a | n/a | n/a | n/a | n/a |", table
            )

    def test_build_review_body_includes_kpi_table(self):
        """AC: KPI table in the review body; hidden verdict block unaffected."""
        statuses = {k: "PASS" for k in review.JUDGE_KEYS}
        data = _build_judges_data(statuses)
        data["syntax_lint"]["usage"] = {
            "model": "z-ai/glm-5.3-flash",
            "provider": "Z.AI",
            "prompt_tokens": 10,
            "llm_calls": 1,
        }
        body = review.build_review_body(data)
        self.assertIn("### 📊 Judge Usage & KPIs", body)
        self.assertIn("z-ai/glm-5.3-flash", body)
        self.assertIn("<!-- llm-pr-review-verdicts", body)
        self.assertLess(
            body.index("### 📊 Judge Usage & KPIs"),
            body.index("<!-- llm-pr-review-verdicts"),
        )

    def test_run_judge_collects_usage_from_metadata(self):
        """AC: fast path -> the llm_caller's usage record lands in the collector."""

        def caller(judge_key, prompt, diff, api_key):
            return self._usage_body(), {
                "used_fallback": False,
                "final_model": "m",
                "attempt_count": 1,
                "usage": {"provider": "explicit", "prompt_tokens": 9},
            }

        records: list[dict] = []
        review.run_judge(
            "syntax_lint",
            review.SYSTEM_PROMPT_SYNTAX_LINT,
            "diff",
            "key",
            llm_caller=caller,
            usage_records=records,
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["provider"], "explicit")
        self.assertEqual(records[0]["prompt_tokens"], 9)

    def test_run_judge_falls_back_to_body_extraction_without_metadata_usage(self):
        """AC: metadata without usage -> usage extracted from the response body."""
        records: list[dict] = []

        def legacy_caller(judge_key, prompt, diff, api_key):
            return self._usage_body(provider="novita"), {
                "used_fallback": False,
                "final_model": "m",
                "attempt_count": 1,
            }

        review.run_judge(
            "syntax_lint",
            review.SYSTEM_PROMPT_SYNTAX_LINT,
            "diff",
            "key",
            llm_caller=legacy_caller,
            usage_records=records,
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["provider"], "novita")
        self.assertEqual(records[0]["prompt_tokens"], 12)

    def test_run_judge_collects_usage_across_batches(self):
        """AC: multi-batch -> one usage record per evaluated chunk."""
        with patch.dict(os.environ, {"REVIEW_BATCH_BUDGET_CHARS": "50"}):
            diff = (
                "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
                "diff --git a/b.py b/b.py\n@@ -1 +1 @@\n-x\n+y\n"
            )
            providers = ["novita", "together"]
            call_idx = [0]

            def caller(judge_key, prompt, diff, api_key):
                provider = providers[min(call_idx[0], len(providers) - 1)]
                call_idx[0] += 1
                return self._usage_body(provider=provider), {
                    "used_fallback": False,
                    "final_model": "m",
                    "attempt_count": 1,
                    "usage": {"provider": provider, "prompt_tokens": 5},
                }

            records: list[dict] = []
            review.run_judge(
                "syntax_lint",
                review.SYSTEM_PROMPT_SYNTAX_LINT,
                diff,
                "key",
                llm_caller=caller,
                usage_records=records,
            )
            self.assertEqual(len(records), 2)
            self.assertEqual(
                {record["provider"] for record in records}, {"novita", "together"}
            )

    def test_run_judge_appends_no_record_on_exception(self):
        """AC: a failed chunk records no usage, and the judge still completes."""

        def raising_caller(judge_key, prompt, diff, api_key):
            raise RuntimeError("LLM down")

        records: list[dict] = []
        status, _, _, error, _, _ = review.run_judge(
            "syntax_lint",
            review.SYSTEM_PROMPT_SYNTAX_LINT,
            "diff",
            "key",
            llm_caller=raising_caller,
            usage_records=records,
        )
        self.assertEqual(status, "NEEDS REVIEW")
        self.assertEqual(records, [])

    def test_run_judge_without_collector_is_unchanged(self):
        """AC: default usage_records=None -> old callers unaffected."""
        call_count = [0]

        def passing_caller(judge_key, prompt, diff, api_key):
            call_count[0] += 1
            return self._usage_body(), {
                "used_fallback": False,
                "final_model": "m",
                "attempt_count": 1,
            }

        status, _, _, error, _, _ = review.run_judge(
            "syntax_lint",
            review.SYSTEM_PROMPT_SYNTAX_LINT,
            "diff",
            "key",
            llm_caller=passing_caller,
        )
        self.assertEqual(status, "PASS")
        self.assertEqual(call_count[0], 1)

    @patch("review._call_with_api_retry")
    @patch("review.resolve_model_config")
    @patch("review.get_tracer")
    def test_call_llm_for_review_returns_usage_metadata(
        self, mock_tracer, mock_cfg, mock_retry
    ):
        """AC: usage extracted from the final body lands in the metadata."""
        from telemetry import DummyTracer

        mock_tracer.return_value = DummyTracer()
        mock_cfg.return_value = {
            "model": "primary-model",
            "routing": ["Together"],
            "temperature": 0.0,
            "options": None,
            "fallback_model": "fallback-model",
        }
        mock_retry.return_value = self._usage_body()

        _, metadata = review.call_llm_for_review("syntax_lint", "sys", "diff", "key")
        self.assertEqual(metadata["usage"]["provider"], "together")
        self.assertEqual(metadata["usage"]["prompt_tokens"], 12)
        self.assertEqual(metadata["usage"]["completion_tokens"], 34)
        self.assertEqual(metadata["usage"]["cost"], 0.0012)

    @patch("review._call_with_api_retry")
    @patch("review.resolve_model_config")
    def test_call_llm_for_review_sets_usage_span_attributes(self, mock_cfg, mock_retry):
        """AC: provider and usage tokens/cost are emitted on the LLM span."""
        mock_cfg.return_value = {
            "model": "primary-model",
            "routing": None,
            "temperature": 0.0,
            "options": None,
            "fallback_model": None,
        }
        mock_retry.return_value = self._usage_body(provider="novita", cost=0.5)

        mock_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.return_value.start_as_current_span.return_value.__enter__.return_value = mock_span
        with patch("review.get_tracer", mock_tracer):
            review.call_llm_for_review("syntax_lint", "sys", "diff", "key")

        attributes = {
            call.args[0]: call.args[1]
            for call in mock_span.set_attribute.call_args_list
        }
        self.assertEqual(attributes.get("llm.provider"), "novita")
        self.assertEqual(attributes.get("llm.usage.prompt_tokens"), 12)
        self.assertEqual(attributes.get("llm.usage.completion_tokens"), 34)
        self.assertEqual(attributes.get("llm.usage.cost_usd"), 0.5)

    @patch("review.call_openrouter_api")
    def test_transport_success_log_reports_usage(self, mock_call):
        """AC: the per-attempt log line carries provider, tokens, and cost."""
        mock_call.return_value = (200, self._usage_body())
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            review._call_with_api_retry(
                "m", [{"role": "user", "content": "d"}], "key", None, 0.0, None
            )
        ok_line = next(
            line
            for line in captured.getvalue().splitlines()
            if "[OPENROUTER] ok" in line
        )
        self.assertIn("provider=together", ok_line)
        self.assertIn("prompt_tokens=12", ok_line)
        self.assertIn("completion_tokens=34", ok_line)
        self.assertIn("cost=0.0012", ok_line)

    def test_append_kpi_summary_writes_header_once_then_table(self):
        """AC: repeated appends grow exactly one KPI section header."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "step-summary.md")
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": path}):
                statuses = {k: "PASS" for k in review.JUDGE_KEYS}
                review.append_kpi_summary(_build_judges_data(statuses))
                review.append_kpi_summary(_build_judges_data(statuses))

            content = Path(path).read_text()
        self.assertEqual(content.count(review.KPI_SUMMARY_HEADER), 1)
        self.assertIn("### 📊 Judge Usage & KPIs", content)
        self.assertIn("| **Total** |", content)

    def test_append_kpi_summary_is_a_noop_outside_ci(self):
        """AC: without GITHUB_STEP_SUMMARY nothing is written anywhere."""
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                with patch.dict(os.environ, {}, clear=True):
                    review.append_kpi_summary({})
                self.assertEqual(os.listdir(tmp), [])
            finally:
                os.chdir(original_cwd)

    def test_append_kpi_summary_silently_ignores_write_errors(self):
        """AC: an unwritable summary path is a no-op, never a crash."""
        with patch.dict(
            os.environ, {"GITHUB_STEP_SUMMARY": "/nonexistent-dir-xyz/summary.md"}
        ):
            review.append_kpi_summary({})


class SubmitGitHubReviewTests(unittest.TestCase):
    """Contract tests for the GitHub review submission path.

    run_command is patched at the module boundary (public attribute), which
    keeps the tests hermetic while the production code path — author lookup,
    identity guard, action-flag decision, gh pr review invocation — runs for
    real.
    """

    PR_AUTHOR = "pr-author-login"
    JUDGE_USER = "trusted-judge-user"

    @staticmethod
    def _fake_run_command(responses):
        """Builds a run_command stand-in driven by an ordered response list.

        Each entry is (returncode, stdout, stderr); the received command
        vectors are recorded for assertions.
        """
        calls = []

        def _run(cmd, env=None):
            calls.append(list(cmd))
            ret, out, err = responses.pop(0)
            return ret, out, err

        return _run, calls

    def _submit(self, responses, action):
        runner, calls = self._fake_run_command(responses)
        with patch.object(review, "run_command", side_effect=runner):
            review.submit_github_review(1, action, "review body")
        review_cmds = [c for c in calls if c[:3] == ["gh", "pr", "review"]]
        self.assertEqual(len(review_cmds), 1)
        return review_cmds[0], calls

    def test_installation_token_all_pass_posts_comment_review(self):
        """AC (#43): an installation token gets HTTP 403 on /user, and
        GitHub forbids it from approving ANY pull request - so an all-PASS
        verdict must be submitted as a comment review, never `--approve`,
        and the run must not crash."""
        responses = [
            (0, self.PR_AUTHOR + "\n", ""),  # gh pr view -> author
            (1, "", "gh: Resource not accessible by integration (HTTP 403)"),
            (0, "", ""),  # gh pr review
        ]
        review_cmd, _ = self._submit(responses, "approve")
        self.assertIn("--comment", review_cmd)
        self.assertNotIn("--approve", review_cmd)
        self.assertIn("--body-file", review_cmd)

    def test_installation_token_failed_verdict_also_posts_comment_review(self):
        """AC (#43): the identity-less action is verdict-independent - a
        FAIL/NEEDS REVIEW verdict posts the same comment review, so the
        review state never reveals the verdict (the exit code does)."""
        responses = [
            (0, self.PR_AUTHOR + "\n", ""),
            (1, "", "gh: Resource not accessible by integration (HTTP 403)"),
            (0, "", ""),
        ]
        review_cmd, _ = self._submit(responses, "request-changes")
        self.assertIn("--comment", review_cmd)
        self.assertNotIn("--request-changes", review_cmd)
        self.assertIn("--body-file", review_cmd)

    def test_judge_user_equal_to_pr_author_downgrades_to_comment(self):
        """AC: the identity guard downgrades approve to comment when the
        token owner IS the PR author (self-approval protection)."""
        responses = [
            (0, self.PR_AUTHOR + "\n", ""),
            (0, self.PR_AUTHOR + "\n", ""),  # gh api user == author
            (0, "", ""),
        ]
        review_cmd, _ = self._submit(responses, "approve")
        self.assertIn("--comment", review_cmd)
        self.assertNotIn("--approve", review_cmd)

    def test_distinct_user_and_approve_action_posts_approval(self):
        """AC: a normal trusted-identity run posts an approval."""
        responses = [
            (0, self.PR_AUTHOR + "\n", ""),
            (0, self.JUDGE_USER + "\n", ""),
            (0, "", ""),
        ]
        review_cmd, _ = self._submit(responses, "approve")
        self.assertIn("--approve", review_cmd)

    def test_distinct_user_and_failing_verdict_requests_changes(self):
        """AC: a PAT run keeps the verdict-driven action in both
        directions - FAIL/NEEDS REVIEW requests changes."""
        responses = [
            (0, self.PR_AUTHOR + "\n", ""),
            (0, self.JUDGE_USER + "\n", ""),
            (0, "", ""),
        ]
        review_cmd, _ = self._submit(responses, "request-changes")
        self.assertIn("--request-changes", review_cmd)

    def test_refused_submission_raises_with_the_greppable_prefix(self):
        """AC (#47): the failure carries the distinct prefix that main()'s
        undelivered-body guard logs verbatim (D-0024), so a consumer grepping
        "[ERR] Failed to submit GitHub review" keeps working — and the gh
        diagnostic stays attached."""
        responses = [
            (0, self.PR_AUTHOR + "\n", ""),
            (0, self.JUDGE_USER + "\n", ""),
            (1, "", "gh: Reviews are disabled for this repository"),
        ]
        runner, _ = self._fake_run_command(responses)
        with patch.object(review, "run_command", side_effect=runner):
            with self.assertRaises(Exception) as ctx:
                review.submit_github_review(1, "approve", "review body")

        message = str(ctx.exception)
        self.assertIn("Failed to submit GitHub review: gh pr review failed:", message)
        self.assertIn("Reviews are disabled for this repository", message)


class BatchBudgetTests(unittest.TestCase):
    """Contract for the batch-budget knob: env override with safe fallbacks."""

    def test_unset_env_uses_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(review._get_batch_budget(), review.BATCH_BUDGET_CHARS)

    def test_empty_env_value_falls_back_to_default(self):
        # Workflows pass declared-but-empty inputs as empty strings.
        with patch.dict(os.environ, {"REVIEW_BATCH_BUDGET_CHARS": ""}):
            self.assertEqual(review._get_batch_budget(), review.BATCH_BUDGET_CHARS)

    def test_valid_env_value_is_parsed(self):
        with patch.dict(os.environ, {"REVIEW_BATCH_BUDGET_CHARS": "500000"}):
            self.assertEqual(review._get_batch_budget(), 500000)

    def test_garbage_env_value_falls_back_with_warning(self):
        with patch.dict(os.environ, {"REVIEW_BATCH_BUDGET_CHARS": "not-a-number"}):
            captured_out = io.StringIO()
            with contextlib.redirect_stdout(captured_out):
                budget = review._get_batch_budget()
            self.assertEqual(budget, review.BATCH_BUDGET_CHARS)
            self.assertIn("Invalid REVIEW_BATCH_BUDGET_CHARS", captured_out.getvalue())


class WorkspaceResolutionTests(unittest.TestCase):
    """resolve_workspace_dir: checkout layout precedence for review context."""

    def setUp(self):
        self._saved = {
            name: os.environ.get(name)
            for name in ("REVIEW_WORKSPACE_DIR", "GITHUB_WORKSPACE")
        }
        os.environ.pop("REVIEW_WORKSPACE_DIR", None)
        os.environ.pop("GITHUB_WORKSPACE", None)

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_explicit_override_wins(self):
        os.environ["REVIEW_WORKSPACE_DIR"] = "/custom/checkout"
        os.environ["GITHUB_WORKSPACE"] = "/runner/workspace"
        self.assertEqual(review.resolve_workspace_dir(), "/custom/checkout")

    def test_github_workspace_repo_layout_preferred(self):
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root) / "repo"
            repo.mkdir()
            os.environ["GITHUB_WORKSPACE"] = root
            self.assertEqual(review.resolve_workspace_dir(), str(repo))

    def test_root_checkout_falls_back_to_workspace(self):
        with tempfile.TemporaryDirectory() as root:
            os.environ["GITHUB_WORKSPACE"] = root
            self.assertEqual(review.resolve_workspace_dir(), root)

    def test_cwd_fallback_without_github_env(self):
        self.assertEqual(review.resolve_workspace_dir(), os.getcwd())


class EnrichmentDegradeTests(unittest.TestCase):
    """_enrich_chunk must degrade, never crash the judge run."""

    def test_enrichment_failure_passes_raw_chunk_through(self):
        with patch(
            "enrichment.enrich_diff_with_function_context",
            side_effect=RuntimeError("parser exploded"),
        ):
            out = review._enrich_chunk("diff --git a/x.py b/x.py\n", "/tmp")
        self.assertEqual(out, "diff --git a/x.py b/x.py\n")


if __name__ == "__main__":
    unittest.main()
