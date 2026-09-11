"""Unit tests for scripts/judge_config.py — the vendored judge-config port.

Contract: precedence chain (node env var > AGENT_MODEL > factory entry >
DEFAULT_MODEL), graceful degradation to {} on missing/malformed config,
env overrides disable provider routing, and orchestrator-runtime fields
(recursion/loop thresholds) are NOT returned. Nested judge sections
(``ci_cd_pr_judges`` etc., plus the optional ``judges-section`` declaration)
resolve additively; flat top-level configs resolve byte-identically to the
v1.3.0 behavior (golden contract).
"""

import json
import os
from pathlib import Path

import pytest

import judge_config
import review


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """Point every test at a per-test config path and clear override vars."""
    monkeypatch.setenv("REVIEW_CONFIG_PATH", str(tmp_path / "factory.json"))
    for var in (
        "AGENT_MODEL",
        "SYNTAX_LINT_MODEL",
        "TEST_COVERAGE_MODEL",
        "ARCHITECTURE_MODEL",
        "SECURITY_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


def _write_factory(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "factory.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestFactoryResolution:
    def test_factory_entry_resolved(self, tmp_path):
        _write_factory(
            tmp_path,
            {
                "security": {
                    "model": "vendor/model-a",
                    "routing": ["Provider1", "Provider2"],
                    "temperature": 0.1,
                    "max_tokens": 1024,
                    "fallback_model": "vendor/model-b",
                }
            },
        )
        cfg = judge_config.resolve_model_config("security")
        assert cfg["model"] == "vendor/model-a"
        assert cfg["routing"] == ["Provider1", "Provider2"]
        assert cfg["temperature"] == 0.1
        assert cfg["max_tokens"] == 1024
        assert cfg["fallback_model"] == "vendor/model-b"

    def test_missing_config_degrades_to_default_with_auto_routing(self, tmp_path):
        # REVIEW_CONFIG_PATH points at a nonexistent file (fixture default).
        cfg = judge_config.resolve_model_config("syntax_lint")
        assert cfg["model"] == judge_config.DEFAULT_MODEL
        assert cfg["routing"] is None
        assert cfg["fallback_model"] is None

    def test_malformed_json_degrades_gracefully(self, tmp_path):
        path = tmp_path / "factory.json"
        path.write_text("{not json", encoding="utf-8")
        cfg = judge_config.resolve_model_config("syntax_lint")
        assert cfg["model"] == judge_config.DEFAULT_MODEL

    def test_non_object_node_entry_is_ignored(self, tmp_path):
        _write_factory(tmp_path, {"syntax_lint": "just a string"})
        cfg = judge_config.resolve_model_config("syntax_lint")
        assert cfg["model"] == judge_config.DEFAULT_MODEL

    def test_routing_null_means_auto_route(self, tmp_path):
        _write_factory(
            tmp_path, {"syntax_lint": {"model": "vendor/model-a", "routing": None}}
        )
        cfg = judge_config.resolve_model_config("syntax_lint")
        assert cfg["routing"] is None

    def test_routing_list_is_pinned_order(self, tmp_path):
        _write_factory(
            tmp_path,
            {"syntax_lint": {"model": "vendor/model-a", "routing": ["A", "B"]}},
        )
        cfg = judge_config.resolve_model_config("syntax_lint")
        assert cfg["routing"] == ["A", "B"]

    def test_orchestrator_runtime_fields_are_not_returned(self, tmp_path):
        _write_factory(
            tmp_path,
            {
                "security": {
                    "model": "vendor/model-a",
                    "recursion_limit": 100,
                    "loop_warn_threshold": 20,
                    "loop_hard_limit": 40,
                }
            },
        )
        cfg = judge_config.resolve_model_config("security")
        assert "recursion_limit" not in cfg
        assert "loop_warn_threshold" not in cfg
        assert "loop_hard_limit" not in cfg


class TestEnvOverridePrecedence:
    def test_node_env_var_beats_factory_and_disables_routing(self, tmp_path):
        _write_factory(
            tmp_path,
            {
                "security": {
                    "model": "vendor/model-a",
                    "routing": ["Provider1"],
                    "temperature": 0.7,
                }
            },
        )
        os.environ["SECURITY_MODEL"] = "vendor/override"
        try:
            cfg = judge_config.resolve_model_config("security")
        finally:
            del os.environ["SECURITY_MODEL"]
        assert cfg["model"] == "vendor/override"
        assert cfg["routing"] is None
        assert cfg["temperature"] == 0.0  # factory model differs -> safe defaults

    def test_agent_model_used_when_node_var_absent(self):
        os.environ["AGENT_MODEL"] = "vendor/general"
        try:
            cfg = judge_config.resolve_model_config("architecture")
        finally:
            del os.environ["AGENT_MODEL"]
        assert cfg["model"] == "vendor/general"
        assert cfg["routing"] is None

    def test_env_override_with_same_model_inherits_factory_tuning(self, tmp_path):
        _write_factory(
            tmp_path,
            {
                "security": {
                    "model": "vendor/model-a",
                    "routing": ["Provider1"],
                    "temperature": 0.7,
                    "options": {"reasoning": {"effort": "high"}},
                    "max_tokens": 2048,
                }
            },
        )
        os.environ["SECURITY_MODEL"] = "vendor/model-a"
        try:
            cfg = judge_config.resolve_model_config("security")
        finally:
            del os.environ["SECURITY_MODEL"]
        assert cfg["model"] == "vendor/model-a"
        assert cfg["routing"] is None  # still disabled for override models
        assert cfg["temperature"] == 0.7
        assert cfg["options"] == {"reasoning": {"effort": "high"}}
        assert cfg["max_tokens"] == 2048


class TestConfigPathResolution:
    def test_review_config_path_env_selects_the_file(self, monkeypatch, tmp_path):
        custom = tmp_path / "nested" / "my-judges.json"
        custom.parent.mkdir()
        custom.write_text(json.dumps({"syntax_lint": {"model": "vendor/custom"}}))
        monkeypatch.setenv("REVIEW_CONFIG_PATH", str(custom))
        cfg = judge_config.resolve_model_config("syntax_lint")
        assert cfg["model"] == "vendor/custom"

    def test_default_path_is_caller_relative(self, monkeypatch, tmp_path):
        # No REVIEW_CONFIG_PATH: config/factory.json relative to cwd — inside
        # a called workflow that is the caller checkout root.
        monkeypatch.delenv("REVIEW_CONFIG_PATH", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "factory.json").write_text(
            json.dumps({"security": {"model": "vendor/cwd-relative"}})
        )
        cfg = judge_config.resolve_model_config("security")
        assert cfg["model"] == "vendor/cwd-relative"


# Real-world nested consumer: agentic-planner-core's config/factory.json
# (first consumer of the nested-section fallback). Verbatim structure.
PLANNER_FACTORY: dict = {
    "factory_version": "2026.2.0",
    "cli_orchestration": {
        "grill": {
            "model": "z-ai/glm-5.2",
            "routing": ["Together", "DeepInfra", "Fireworks", "Parasail", "Inceptron"],
            "temperature": 0.0,
        },
        "verify": {
            "model": "z-ai/glm-5.2",
            "routing": ["Together", "DeepInfra", "Fireworks", "Parasail", "Inceptron"],
            "temperature": 0.0,
        },
        "draft": {
            "model": "deepseek/deepseek-v4-pro",
            "routing": ["DeepInfra", "SiliconFlow", "Novita", "Parasail", "DeepSeek"],
            "options": {"thinking": "max"},
            "temperature": 0.0,
        },
    },
    "refine_graph_nodes": {
        "analyze_sources": {
            "model": "deepseek/deepseek-v4-flash",
            "routing": ["DeepInfra", "SiliconFlow", "Novita", "Parasail", "DeepSeek"],
            "options": {"thinking": "high"},
        },
        "web_search": {
            "model": "deepseek/deepseek-v4-flash",
            "routing": ["DeepInfra", "SiliconFlow", "Novita", "Parasail", "DeepSeek"],
            "options": {"thinking": "none"},
        },
        "propose_options": {
            "model": "deepseek/deepseek-v4-pro",
            "routing": ["DeepInfra", "SiliconFlow", "Novita", "Parasail", "DeepSeek"],
            "options": {"thinking": "max"},
        },
        "evaluate_grade": {
            "model": "z-ai/glm-5.2",
            "routing": ["Together", "DeepInfra", "Fireworks", "Parasail", "Inceptron"],
            "temperature": 0.0,
            "max_tokens": 8192,
        },
        "apply_decision": {
            "model": "moonshotai/kimi-k2.7-code",
            "routing": ["Together", "SiliconFlow", "MoonshotAI", "Inceptron"],
            "temperature": 0.0,
            "max_tokens": 16384,
        },
        "publish_issue": {
            "model": "moonshotai/kimi-k2.7-code",
            "routing": ["Together", "SiliconFlow", "MoonshotAI", "Inceptron"],
            "temperature": 0.0,
        },
        "security_audit": {
            "model": "z-ai/glm-5.2",
            "routing": ["Together", "DeepInfra", "Fireworks", "Parasail", "Inceptron"],
            "temperature": 0.0,
            "max_tokens": 4096,
        },
        "intent_gate": {
            "model": "z-ai/glm-5.2",
            "routing": ["Together", "DeepInfra", "Fireworks", "Parasail", "Inceptron"],
            "temperature": 0.0,
            "max_tokens": 8192,
        },
        "planning_judge": {
            "model": "z-ai/glm-5.2",
            "routing": ["Together", "DeepInfra", "Fireworks", "Parasail", "Inceptron"],
            "temperature": 0.0,
            "max_tokens": 8192,
        },
    },
    "ci_cd_pr_judges": {
        "syntax_lint": {
            "model": "moonshotai/kimi-k2.7-code",
            "routing": ["Together", "SiliconFlow", "MoonshotAI", "Inceptron"],
        },
        "test_coverage": {
            "model": "moonshotai/kimi-k2.7-code",
            "routing": ["Together", "SiliconFlow", "MoonshotAI", "Inceptron"],
        },
        "architecture": {
            "model": "z-ai/glm-5.2",
            "routing": ["Together", "DeepInfra", "Fireworks", "Parasail", "Inceptron"],
        },
        "security": {
            "model": "deepseek/deepseek-v4-pro",
            "routing": ["DeepInfra", "SiliconFlow", "Novita", "Parasail", "DeepSeek"],
            "options": {"thinking": "max"},
        },
    },
}


def _load_example_factory() -> dict:
    """The shipped flat v1.3.0-style example config (config/factory.example.json)."""
    path = Path(__file__).resolve().parents[1] / "config" / "factory.example.json"
    return json.loads(path.read_text(encoding="utf-8"))


class TestNestedSectionResolution:
    def test_planner_factory_resolves_all_four_judges_from_nested_section(
        self, tmp_path
    ):
        _write_factory(tmp_path, PLANNER_FACTORY)
        cfg = judge_config.resolve_model_config("syntax_lint")
        assert cfg["model"] == "moonshotai/kimi-k2.7-code"
        assert cfg["routing"] == ["Together", "SiliconFlow", "MoonshotAI", "Inceptron"]
        assert cfg["temperature"] == 0.0
        assert cfg["fallback_model"] is None
        cfg = judge_config.resolve_model_config("test_coverage")
        assert cfg["model"] == "moonshotai/kimi-k2.7-code"
        cfg = judge_config.resolve_model_config("architecture")
        assert cfg["model"] == "z-ai/glm-5.2"
        assert cfg["routing"] == [
            "Together",
            "DeepInfra",
            "Fireworks",
            "Parasail",
            "Inceptron",
        ]
        cfg = judge_config.resolve_model_config("security")
        assert cfg["model"] == "deepseek/deepseek-v4-pro"
        assert cfg["routing"] == [
            "DeepInfra",
            "SiliconFlow",
            "Novita",
            "Parasail",
            "DeepSeek",
        ]
        assert cfg["options"] == {"thinking": "max"}

    def test_non_judge_nodes_from_other_sections_do_not_resolve(self, tmp_path):
        # Bounded scan: cli_orchestration/refine_graph_nodes are NOT judge
        # sections — their node names must never resolve for the judges.
        _write_factory(tmp_path, PLANNER_FACTORY)
        for stranger in ("grill", "verify", "draft", "planning_judge"):
            cfg = judge_config.resolve_model_config(stranger)
            assert cfg["model"] == judge_config.DEFAULT_MODEL

    def test_nested_resolution_logs_info_with_section_name(self, tmp_path, capsys):
        _write_factory(tmp_path, PLANNER_FACTORY)
        judge_config.resolve_model_config("security")
        err = capsys.readouterr().err
        assert (
            "[INFO] Judge config for 'security' resolved from nested section 'ci_cd_pr_judges'."
            in err
        )

    def test_declared_section_scanned_for_custom_names(self, tmp_path):
        _write_factory(
            tmp_path,
            {
                "judges-section": ["pr_reviewers"],
                "pr_reviewers": {"security": {"model": "vendor/declared"}},
            },
        )
        cfg = judge_config.resolve_model_config("security")
        assert cfg["model"] == "vendor/declared"

    def test_declared_sections_scan_before_defaults(self, tmp_path):
        _write_factory(
            tmp_path,
            {
                "judges-section": ["pr_reviewers"],
                "pr_reviewers": {"security": {"model": "vendor/declared"}},
                "ci_cd_pr_judges": {"security": {"model": "vendor/default-section"}},
            },
        )
        cfg = judge_config.resolve_model_config("security")
        assert cfg["model"] == "vendor/declared"

    def test_malformed_judges_section_key_is_ignored(self, tmp_path, capsys):
        _write_factory(
            tmp_path,
            {"judges-section": "pr_reviewers", "security": {"model": "vendor/flat"}},
        )
        cfg = judge_config.resolve_model_config("security")
        assert cfg["model"] == "vendor/flat"  # top-level still wins
        err = capsys.readouterr().err
        assert (
            "[WARN] Config key 'judges-section' must be a list of nested section names; ignoring."
            in err
        )

    def test_top_level_takes_precedence_over_nested_section(self, tmp_path):
        _write_factory(
            tmp_path,
            {
                "security": {"model": "vendor/top-level"},
                "ci_cd_pr_judges": {"security": {"model": "vendor/nested"}},
            },
        )
        cfg = judge_config.resolve_model_config("security")
        assert cfg["model"] == "vendor/top-level"

    def test_non_object_entry_in_nested_section_is_skipped(self, tmp_path, capsys):
        _write_factory(
            tmp_path,
            {
                "judges-section": ["custom_first"],
                "custom_first": {"security": "just a string"},
                "ci_cd_pr_judges": {"security": {"model": "vendor/next"}},
            },
        )
        cfg = judge_config.resolve_model_config("security")
        assert cfg["model"] == "vendor/next"
        err = capsys.readouterr().err
        assert (
            "[WARN] Factory entry for node 'security' in section 'custom_first' is not an object; ignoring."
            in err
        )

    def test_unknown_node_in_nested_config_still_degrades_with_warn(
        self, tmp_path, capsys
    ):
        _write_factory(tmp_path, PLANNER_FACTORY)
        cfg = judge_config.resolve_model_config("nonexistent_node")
        assert cfg["model"] == judge_config.DEFAULT_MODEL
        assert cfg["routing"] is None
        err = capsys.readouterr().err
        assert (
            "[WARN] Node 'nonexistent_node' not found in judge configuration; falling back to"
            in err
        )

    def test_env_override_inherits_tuning_from_nested_entry(self, tmp_path):
        _write_factory(tmp_path, PLANNER_FACTORY)
        os.environ["SECURITY_MODEL"] = "deepseek/deepseek-v4-pro"
        try:
            cfg = judge_config.resolve_model_config("security")
        finally:
            del os.environ["SECURITY_MODEL"]
        assert cfg["model"] == "deepseek/deepseek-v4-pro"
        assert cfg["routing"] is None  # override models never keep routing
        assert cfg["options"] == {"thinking": "max"}


class TestFlatGoldenContract:
    """Old-style flat consumer configs must resolve byte-identically to
    v1.3.0 — the nested-section fallback is strictly additive."""

    GOLDEN_FLAT_RESOLUTIONS = {
        "syntax_lint": {
            "model": "z-ai/glm-5.3-flash",
            "routing": None,
            "temperature": 0.0,
            "options": None,
            "max_tokens": None,
            "fallback_model": "z-ai/glm-5.2",
        },
        "test_coverage": {
            "model": "z-ai/glm-5.3-flash",
            "routing": [
                "Z.AI",
                "Novita",
                "DeepInfra",
                "Modal",
                "Fireworks",
                "Friendli",
                "Parasail",
                "Phala",
            ],
            "temperature": 0.0,
            "options": None,
            "max_tokens": None,
            "fallback_model": "z-ai/glm-5.2",
        },
        "architecture": {
            "model": "z-ai/glm-5.3-flash",
            "routing": [
                "Z.AI",
                "Novita",
                "DeepInfra",
                "Modal",
                "Fireworks",
                "Friendli",
                "Parasail",
                "Phala",
            ],
            "temperature": 0.0,
            "options": None,
            "max_tokens": None,
            "fallback_model": "moonshotai/kimi-k3",
        },
        "security": {
            "model": "moonshotai/kimi-k3",
            "routing": [
                "Together",
                "Fireworks",
                "Parasail",
                "Moonshot AI",
                "DeepInfra",
            ],
            "temperature": 0.0,
            "options": {"reasoning": {"effort": "high"}},
            "max_tokens": None,
            "fallback_model": "z-ai/glm-5.2",
        },
    }

    def test_flat_v130_example_config_resolves_golden_identical(self, tmp_path):
        _write_factory(tmp_path, _load_example_factory())
        for node, golden in self.GOLDEN_FLAT_RESOLUTIONS.items():
            cfg = judge_config.resolve_model_config(node)
            assert cfg == golden

    def test_flat_example_config_never_triggers_nested_fallback(self, tmp_path, capsys):
        _write_factory(tmp_path, _load_example_factory())
        judge_config.resolve_model_config("security")
        err = capsys.readouterr().err
        assert "resolved from nested section" not in err

    def test_flat_vs_nested_resolution_parity(self, tmp_path):
        # The same judge entries, flat vs nested under ci_cd_pr_judges,
        # resolve to identical config dicts.
        flat = {
            "syntax_lint": {"model": "vendor/m", "routing": ["P1"], "temperature": 0.2}
        }
        nested = {"ci_cd_pr_judges": flat}
        _write_factory(tmp_path, flat)
        flat_cfgs = {n: judge_config.resolve_model_config(n) for n in flat}
        _write_factory(tmp_path, nested)
        nested_cfgs = {n: judge_config.resolve_model_config(n) for n in flat}
        assert flat_cfgs == nested_cfgs

    def test_review_body_and_verdict_block_parity_flat_vs_nested(self, tmp_path):
        # The posted review body (incl. the hidden verdict block) must not
        # depend on where the judge config was sourced from.
        statuses = {
            "syntax_lint": "PASS",
            "test_coverage": "FAIL",
            "architecture": "PASS",
            "security": "NEEDS REVIEW",
        }
        bodies = {}
        for label, data in (
            ("flat", {"syntax_lint": {"model": "vendor/m"}}),
            ("nested", {"ci_cd_pr_judges": {"syntax_lint": {"model": "vendor/m"}}}),
        ):
            _write_factory(tmp_path, data)
            cfg = judge_config.resolve_model_config("syntax_lint")
            judges_data = {
                key: {
                    "name": key,
                    "status": status,
                    "findings": ["bug|test finding"] if status == "FAIL" else [],
                    "error": "check crashed" if status == "NEEDS REVIEW" else None,
                    "reasoning": "r",
                    "used_fallback": status == "FAIL",
                    "final_model": cfg["model"],
                    "usage": {"model": cfg["model"]},
                }
                for key, status in statuses.items()
            }
            bodies[label] = review.build_review_body(judges_data)
        assert bodies["flat"] == bodies["nested"]
        assert "<!-- llm-pr-review-verdicts" in bodies["flat"]
        assert "syntax_lint: PASS" in bodies["flat"]
        assert "test_coverage: FAIL" in bodies["flat"]
