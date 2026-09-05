"""Unit tests for scripts/judge_config.py — the vendored judge-config port.

Contract: precedence chain (node env var > AGENT_MODEL > factory entry >
DEFAULT_MODEL), graceful degradation to {} on missing/malformed config,
env overrides disable provider routing, and orchestrator-runtime fields
(recursion/loop thresholds) are NOT returned.
"""

import json
import os
from pathlib import Path

import pytest

import judge_config


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
