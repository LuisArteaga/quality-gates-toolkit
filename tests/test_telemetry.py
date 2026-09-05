"""Behavioral tests for scripts/telemetry.py's public surface.

Covers the pure helpers (OTLP endpoint resolution, Langfuse credential
handling), the state lifecycle (loop/phase spans, security blocks, state
file persistence), and the OTel processors (local JSONL writer, baggage
session-id propagation) driven through REAL SDK spans. The retrospective
export path (_export_recorded_spans) is deliberately out of scope — it is
a follow-up coverage item (D-0008).
"""

import base64
import datetime
import json
from pathlib import Path

import pytest

import telemetry

ENV_VARS = (
    "AGENT_LOG_PATH",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "OTEL_SERVICE_NAME",
    "SMITHDB_PROJECT_NAME",
    "SMITHDB_API_KEY",
)


@pytest.fixture(autouse=True)
def isolated_logs(tmp_path, monkeypatch):
    """Point the logs/state dir at a per-test tmp dir and clear env knobs."""
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    log_dir = tmp_path / "agent_logs"
    monkeypatch.setenv("AGENT_LOG_PATH", str(log_dir))
    yield log_dir


def _state_file(log_dir: Path) -> Path:
    return log_dir / "telemetry_state.json"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_configure_otlp_endpoint_env_wins(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318/v1/traces")
    assert telemetry.configure_otlp_endpoint() == "http://collector:4318/v1/traces"


def test_configure_otlp_endpoint_empty_outside_docker():
    assert telemetry.configure_otlp_endpoint() == ""


def test_langfuse_requires_both_keys(monkeypatch):
    assert not telemetry._is_langfuse_configured()
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pub")
    assert not telemetry._is_langfuse_configured()
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sec")
    assert telemetry._is_langfuse_configured()


def test_langfuse_auth_header_is_basic_auth_with_ingestion_version(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pub")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sec")
    headers = telemetry._build_langfuse_auth_header()
    expected = base64.b64encode(b"pub:sec").decode()
    assert headers["Authorization"] == f"Basic {expected}"
    assert headers["x-langfuse-ingestion-version"] == "4"


def test_get_tracer_uses_dummy_without_otel(monkeypatch):
    monkeypatch.setattr(telemetry, "HAS_OTEL", False)
    assert isinstance(telemetry.get_tracer(), telemetry.DummyTracer)


# ---------------------------------------------------------------------------
# get_agent_logs_dir fallbacks
# ---------------------------------------------------------------------------


def test_logs_dir_from_env(monkeypatch, isolated_logs):
    resolved = telemetry.get_agent_logs_dir()
    assert Path(resolved) == isolated_logs.resolve()


def test_logs_dir_falls_back_to_tmp_when_env_unset(monkeypatch):
    monkeypatch.delenv("AGENT_LOG_PATH")
    # Neither /workspace/.agent_logs nor the toolkit-local .agent_logs exists
    # on CI/dev machines without the workspace layout.
    assert telemetry.get_agent_logs_dir() == "/tmp/agent_logs"


# ---------------------------------------------------------------------------
# State lifecycle (public path: start/end loop + phases, security blocks)
# ---------------------------------------------------------------------------


def test_record_security_block_buffers_and_persists(isolated_logs):
    telemetry.record_security_block("plan", "/etc/shadow", issue_number=7)
    entry = telemetry._state["security_blocks"][0]
    assert entry["layer"] == "plan"
    assert entry["blocked_path"] == "/etc/shadow"
    assert entry["issue_number"] == 7
    assert _state_file(isolated_logs).exists()


def test_start_loop_resets_state_sets_session_and_persists(isolated_logs):
    telemetry.record_security_block("runtime", "/etc/passwd")
    telemetry.start_orchestrator_loop(issue_number=7, branch="feat/x")
    assert telemetry._langfuse_session_id == "7_feat/x"
    assert telemetry._state["loop_start_time"] is not None
    assert telemetry._state["loop_issue_number"] == 7
    assert telemetry._state["security_blocks"] == []
    assert _state_file(isolated_logs).exists()


def test_phase_lifecycle_end_captures_exit_and_tokens(isolated_logs):
    telemetry.start_orchestrator_phase("plan")
    assert telemetry._state["phases"]["plan"]["end_time"] is None
    telemetry.end_orchestrator_phase(
        exit_code=1,
        prompt_tokens=10,
        completion_tokens=5,
        model_name="vendor/model-a",
    )
    phase = telemetry._state["phases"]["plan"]
    assert phase["end_time"] is not None
    assert phase["exit_code"] == 1
    assert phase["prompt_tokens"] == 10
    assert phase["completion_tokens"] == 5
    assert phase["model_name"] == "vendor/model-a"


def test_unknown_phase_name_is_ignored():
    telemetry.start_orchestrator_phase("not-a-phase")
    assert "not-a-phase" not in telemetry._state["phases"]


def test_end_phase_without_active_phase_is_silent():
    telemetry.end_orchestrator_phase()  # no phases recorded -> no-op


def test_nested_phase_named_end(isolated_logs):
    telemetry.start_orchestrator_phase("verify")
    telemetry.start_orchestrator_phase("bineval", parent="verify")
    telemetry.end_orchestrator_phase(phase_name="bineval", exit_code=0)
    assert telemetry._state["phases"]["bineval"]["end_time"] is not None
    assert telemetry._state["phases"]["verify"]["end_time"] is None
    assert telemetry._state["phases"]["bineval"]["parent"] == "verify"


def test_end_loop_stamps_exit_code_and_removes_state_file(isolated_logs):
    telemetry.start_orchestrator_loop(issue_number=3, branch="main")
    telemetry.end_orchestrator_loop(exit_code=2)
    assert telemetry._state["loop_end_time"] is not None
    assert telemetry._state["loop_exit_code"] == 2
    assert not _state_file(isolated_logs).exists()


def test_resume_merges_persisted_state(isolated_logs):
    # Simulate a prior crashed cycle: state file on disk with exit code and
    # an open phase; a NEW phase start must merge, not clobber.
    isolated_logs.mkdir(parents=True, exist_ok=True)
    prior = {
        "loop_exit_code": None,
        "loop_start_time": 123.0,
        "loop_issue_number": 9,
        "phases": {"execute": {"start_time": 1.0, "end_time": None, "exit_code": 0}},
    }
    _state_file(isolated_logs).write_text(json.dumps(prior), encoding="utf-8")
    telemetry.start_orchestrator_phase("plan")
    assert telemetry._state["loop_issue_number"] == 9
    assert telemetry._state["phases"]["execute"]["start_time"] == 1.0
    assert "plan" in telemetry._state["phases"]


def test_corrupt_state_file_is_ignored(isolated_logs):
    isolated_logs.mkdir(parents=True, exist_ok=True)
    _state_file(isolated_logs).write_text("{not json", encoding="utf-8")
    telemetry.start_orchestrator_phase("plan")
    assert "plan" in telemetry._state["phases"]


# ---------------------------------------------------------------------------
# init_telemetry
# ---------------------------------------------------------------------------


def test_init_telemetry_noop_without_otel(monkeypatch, capsys):
    monkeypatch.setattr(telemetry, "HAS_OTEL", False)
    telemetry.init_telemetry()
    assert "no-op tracing mode" in capsys.readouterr().err


def test_init_telemetry_provider_lifecycle(isolated_logs, monkeypatch):
    # Call 1 (fresh provider path): Langfuse keys -> Langfuse endpoint and
    # auth headers on the OTLP exporter; provider registered globally.
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pub")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sec")
    telemetry.init_telemetry(issue_number=7, branch="main")
    assert telemetry._langfuse_session_id == "7_main"
    provider = telemetry.trace.get_tracer_provider()
    assert isinstance(provider, telemetry.TracerProvider)

    # Call 2 (already-set provider path): no exporter passed -> only the
    # Langfuse baggage processor attachment path runs.
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY")
    telemetry.init_telemetry(reset_state=False)


def test_init_telemetry_session_id_unset_without_issue(isolated_logs):
    telemetry.init_telemetry()
    assert telemetry._langfuse_session_id is None


# ---------------------------------------------------------------------------
# OTel processors (real SDK spans)
# ---------------------------------------------------------------------------


def test_jsonl_processor_writes_real_span(isolated_logs):
    from opentelemetry.sdk.trace import TracerProvider

    processor = telemetry.LocalJSONLFileSpanProcessor()
    provider = TracerProvider()
    # The processor IS a SpanProcessor (on_start/on_end) — attach directly;
    # SimpleSpanProcessor would expect an exporter with .export().
    provider.add_span_processor(processor)
    tracer = telemetry.trace.get_tracer("test-suite", tracer_provider=provider)

    with tracer.start_as_current_span("review_op") as span:
        span.set_attribute("tool.name", "ls")

    log_file = isolated_logs / (
        f"otel_traces_{datetime.date.today().isoformat()}.jsonl"
    )
    entry = json.loads(log_file.read_text(encoding="utf-8").splitlines()[0])
    assert entry["name"] == "review_op"
    assert entry["attributes"]["tool.name"] == "ls"
    assert len(entry["trace_id"]) == 32
    assert entry["parent_span_id"] == ""


def test_jsonl_processor_swallows_broken_spans(isolated_logs, capsys):
    processor = telemetry.LocalJSONLFileSpanProcessor()
    processor.on_end(object())  # no span API -> warn, never raise
    assert "failed to write span" in capsys.readouterr().err


def test_baggage_processor_copies_session_id(isolated_logs):
    from opentelemetry.baggage import set_baggage

    processor = telemetry.BaggageSpanProcessor()
    tracer = telemetry.trace.get_tracer("test-suite")

    ctx = set_baggage("langfuse.session.id", "7_main")
    span = tracer.start_span("op", context=ctx)
    processor.on_start(span, ctx)
    assert span.attributes["langfuse.session.id"] == "7_main"

    # Without baggage: no attribute is set.
    plain = tracer.start_span("plain")
    processor.on_start(plain, None)
    assert "langfuse.session.id" not in (plain.attributes or {})


def test_baggage_processor_redacts_secret_shaped_values(isolated_logs):
    from opentelemetry.baggage import set_baggage

    processor = telemetry.BaggageSpanProcessor()
    tracer = telemetry.trace.get_tracer("test-suite")
    # Full classic-PAT shape: ghp_ + 36 alphanumerics is what the
    # redaction regex matches (shorter fragments pass through by design).
    secretish = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    assert len(secretish) == 4 + 36
    ctx = set_baggage("langfuse.session.id", secretish)
    span = tracer.start_span("op", context=ctx)
    processor.on_start(span, ctx)
    scrubbed = span.attributes["langfuse.session.id"]
    assert scrubbed != secretish
    assert "ghp_" not in scrubbed
