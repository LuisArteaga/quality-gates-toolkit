"""Behavioral tests for scripts/telemetry.py's retained surface.

The toolkit ships only the PR-review tracing runtime: OTLP/Langfuse export
configuration, the local JSONL span processor, the baggage session-id
processor, and the no-op fallbacks. The orchestrator loop/phase machinery
was removed as out-of-scope (see DECISIONS.md D-0009) and has no tests here.
"""

import base64
import datetime
import json

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
    """Point the logs dir at a per-test tmp dir and clear env knobs."""
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    log_dir = tmp_path / "agent_logs"
    monkeypatch.setenv("AGENT_LOG_PATH", str(log_dir))
    yield log_dir


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_configure_otlp_endpoint_env_wins(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318/v1/traces")
    assert telemetry.configure_otlp_endpoint() == "http://collector:4318/v1/traces"


def test_configure_otlp_endpoint_docker_default(monkeypatch):
    monkeypatch.setattr(telemetry, "_is_docker", lambda: True)
    endpoint = telemetry.configure_otlp_endpoint()
    assert endpoint == "http://host.docker.internal:4318/v1/traces"
    # The default is also SET in the env so the exporter picks it up.
    import os

    assert os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == endpoint


def test_configure_otlp_endpoint_empty_outside_docker():
    assert telemetry.configure_otlp_endpoint() == ""


def test_langfuse_requires_both_keys(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pub")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sec")
    assert telemetry._is_langfuse_configured() is True
    monkeypatch.delenv("LANGFUSE_SECRET_KEY")
    assert telemetry._is_langfuse_configured() is False
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY")
    assert telemetry._is_langfuse_configured() is False


def test_langfuse_auth_header_shape(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pub")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sec")
    headers = telemetry._build_langfuse_auth_header()
    expected = base64.b64encode(b"pub:sec").decode()
    assert headers["Authorization"] == f"Basic {expected}"
    assert headers["x-langfuse-ingestion-version"] == "4"


# ---------------------------------------------------------------------------
# Log directory resolution
# ---------------------------------------------------------------------------


def test_agent_logs_dir_env_path_wins_and_is_created(isolated_logs):
    resolved = telemetry.get_agent_logs_dir()
    assert resolved == str(isolated_logs.resolve())
    assert isolated_logs.exists()


def test_agent_logs_dir_falls_back_to_tmp(monkeypatch):
    monkeypatch.delenv("AGENT_LOG_PATH", raising=False)
    resolved = telemetry.get_agent_logs_dir()
    assert resolved == "/tmp/agent_logs"


# ---------------------------------------------------------------------------
# Tracer fallbacks
# ---------------------------------------------------------------------------


def test_get_tracer_returns_real_tracer_with_otel():
    if not telemetry.HAS_OTEL:
        pytest.skip("opentelemetry not installed")
    tracer = telemetry.get_tracer()
    assert tracer is not None


def test_get_tracer_returns_dummy_without_otel(monkeypatch):
    monkeypatch.setattr(telemetry, "HAS_OTEL", False)
    tracer = telemetry.get_tracer()
    assert isinstance(tracer, telemetry.DummyTracer)


def test_dummy_span_is_a_silent_context_manager():
    span = telemetry.DummySpan()
    with span as s:
        s.set_attribute("k", "v")
        s.record_exception(RuntimeError("x"))
        s.set_status(telemetry.DummyStatus(telemetry.DummyStatusCode.OK))
    assert s is span


# ---------------------------------------------------------------------------
# init_telemetry
# ---------------------------------------------------------------------------


def test_init_telemetry_noop_without_otel(monkeypatch, capsys):
    monkeypatch.setattr(telemetry, "HAS_OTEL", False)
    telemetry.init_telemetry()
    assert "[INFO] OpenTelemetry is not installed" in capsys.readouterr().err


def test_init_telemetry_is_idempotent(isolated_logs):
    from opentelemetry.sdk.trace import TracerProvider

    telemetry.init_telemetry()
    telemetry.init_telemetry()
    provider = telemetry.trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)


def test_init_telemetry_attaches_in_memory_exporter(isolated_logs):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    telemetry.init_telemetry(in_memory_exporter=exporter)
    provider = telemetry.trace.get_tracer_provider()
    assert isinstance(provider, telemetry.TracerProvider)


# ---------------------------------------------------------------------------
# LocalJSONLFileSpanProcessor
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


# ---------------------------------------------------------------------------
# BaggageSpanProcessor
# ---------------------------------------------------------------------------


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
