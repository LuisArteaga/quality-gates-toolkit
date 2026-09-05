import base64
import datetime
import json
import os
import sys
import threading
from pathlib import Path

from scripts.redaction import redact_secrets

# Try importing opentelemetry, fallback to dummy classes if not installed
try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False

# Literal keys from OpenInference Semantic Conventions
OPENINFERENCE_SPAN_KIND = "openinference.span.kind"
INPUT_VALUE = "input.value"
OUTPUT_VALUE = "output.value"
LLM_MODEL_NAME = "llm.model_name"
TOOL_NAME = "tool.name"
TOOL_PARAMETERS = "tool.parameters"

_DOCKER_SENTINEL = "/.dockerenv"
_DEFAULT_OTLP_ENDPOINT = "http://host.docker.internal:4318/v1/traces"

# Langfuse Cloud OTLP endpoint — full traces path (OTLPSpanExporter uses endpoint as-is,
# no path appending). See Langfuse OTLP docs:
# https://langfuse.com/integrations/native/opentelemetry
_LANGFUSE_DEFAULT_OTLP_ENDPOINT = "https://cloud.langfuse.com/api/public/otel/v1/traces"


def _is_docker() -> bool:
    """Return True when running inside a Docker container."""
    return os.path.exists(_DOCKER_SENTINEL)


def configure_otlp_endpoint() -> str:
    """Return the OTLP endpoint to use, defaulting to host.docker.internal when in Docker.

    If OTEL_EXPORTER_OTLP_ENDPOINT is already set in the environment, that value is
    returned unchanged. Otherwise, if /.dockerenv is present, the default
    http://host.docker.internal:4318/v1/traces endpoint is set and returned.
    Returns an empty string when neither condition applies.

    # AGENT_DECISION: default only when inside Docker (/.dockerenv) and env var is absent.
    # If a non-Docker deployment is needed, the caller should set
    # OTEL_EXPORTER_OTLP_ENDPOINT explicitly; no change to this function required.
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if not endpoint and _is_docker():
        endpoint = _DEFAULT_OTLP_ENDPOINT
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = endpoint
    return endpoint


def _is_langfuse_configured() -> bool:
    """Return True only when both Langfuse API keys are set (non-empty).

    A single key without the other must NOT activate Langfuse export;
    the code falls back to generic OTLP instead.
    """
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY")) and bool(
        os.getenv("LANGFUSE_SECRET_KEY")
    )


def _attach_langfuse_processors(provider) -> None:
    """Attach the Langfuse session-level SpanProcessor(s) to *provider*.

    The BaggageSpanProcessor copies ``langfuse.session.id`` from OTel baggage
    to span attributes, enabling trace grouping in the Langfuse UI. It is
    attached whenever Langfuse is configured, on **both** the fresh-provider
    path and the already-set-provider path of ``init_telemetry``, so the
    processor set is independent of which ``init_telemetry`` call runs first
    (issue #75: the early-return branch previously skipped it, making
    ``test_langfuse_session_id_in_spans`` order-dependent).

    Re-attachment on an already-configured provider is idempotent in effect —
    the processor sets the same attribute to the same value — which is the
    accepted cost of not inspecting a provider's registered processors
    (the OTel SDK exposes no public way to enumerate them).
    """
    if _is_langfuse_configured():
        provider.add_span_processor(BaggageSpanProcessor())


def _build_langfuse_auth_header() -> dict[str, str]:
    """Build the Authorization header for Langfuse Cloud OTLP export.

    Langfuse uses Basic Auth with base64(public_key:secret_key).
    Also includes x-langfuse-ingestion-version: 4 for real-time ingestion
    in Langfuse v4 (without it, data is delayed up to 10 minutes).

    Reference: https://langfuse.com/integrations/native/opentelemetry
    """
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
    credentials = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    return {
        "Authorization": f"Basic {credentials}",
        "x-langfuse-ingestion-version": "4",
    }


# Dummy definitions for graceful failover when OTel is not installed
class DummySpan:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def set_attribute(self, key, value):
        pass

    def record_exception(self, exception):
        pass

    def set_status(self, status):
        pass


class DummyTracer:
    def start_as_current_span(self, name, *args, **kwargs):
        return DummySpan()


class DummyStatusCode:
    OK = 0
    ERROR = 1


class DummyStatus:
    def __init__(self, code, message=""):
        self.code = code
        self.message = message


class DummyTraceModule:
    StatusCode = DummyStatusCode

    def Status(self, code, message=""):
        return DummyStatus(code, message)


if not HAS_OTEL:
    # Export a dummy trace object that matches opentelemetry API used in review.py
    trace = DummyTraceModule()


def get_tracer():
    """Returns the central tracer instance, or a dummy tracer if OTel is missing."""
    if HAS_OTEL:
        return trace.get_tracer("quality-gates-toolkit-review")
    else:
        return DummyTracer()


def get_agent_logs_dir() -> str:
    """Resolve and return the path to the agent logs directory, ensuring it exists and is writable."""
    # First check if AGENT_LOG_PATH is configured in the environment
    agent_log_path = os.getenv("AGENT_LOG_PATH")
    if agent_log_path:
        try:
            os.makedirs(agent_log_path, exist_ok=True)
            if os.access(agent_log_path, os.W_OK):
                return str(Path(agent_log_path).resolve())
        except Exception:
            pass

    # Fallback to checking /workspace/.agent_logs (inside container)
    if os.path.exists("/workspace/.agent_logs") and os.access(
        "/workspace/.agent_logs", os.W_OK
    ):
        return "/workspace/.agent_logs"
    # Otherwise check local .agent_logs relative to current working dir or project root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    local_logs = os.path.join(project_root, ".agent_logs")
    if os.path.exists(local_logs) and os.access(local_logs, os.W_OK):
        return local_logs
    # Fallback to temp logs if nothing else works
    tmp_logs = "/tmp/agent_logs"
    try:
        os.makedirs(tmp_logs, mode=0o700, exist_ok=True)
        os.chmod(tmp_logs, 0o700)  # nosemgrep: insecure-file-permissions
    except Exception:
        pass
    return tmp_logs


if HAS_OTEL:

    class LocalJSONLFileSpanProcessor(SpanProcessor):
        """A custom OpenTelemetry SpanProcessor that serializes finished spans to local JSONL files."""

        def __init__(self):
            self._lock = threading.Lock()

        def on_start(self, span, parent_context=None):
            pass

        def on_end(self, span):
            try:
                status_code = (
                    span.status.status_code.value
                    if hasattr(span.status.status_code, "value")
                    else int(span.status.status_code)
                )
                status_description = span.status.description or ""

                span_dict = {
                    "trace_id": f"{span.context.trace_id:032x}",
                    "span_id": f"{span.context.span_id:016x}",
                    "parent_span_id": f"{span.parent.span_id:016x}"
                    if span.parent
                    else "",
                    "name": span.name,
                    "kind": span.kind.value
                    if hasattr(span.kind, "value")
                    else int(span.kind),
                    "start_time_unix_nano": span.start_time,
                    "end_time_unix_nano": span.end_time,
                    "attributes": dict(span.attributes or {}),
                    "status_code": status_code,
                    "status_message": status_description,
                    "resource_attributes": dict(span.resource.attributes or {}),
                    "scope_name": span.instrumentation_scope.name
                    if span.instrumentation_scope
                    else "unknown",
                }

                # Write to dated file
                today_str = datetime.date.today().isoformat()
                logs_dir = get_agent_logs_dir()
                log_file_path = os.path.join(logs_dir, f"otel_traces_{today_str}.jsonl")

                with self._lock:
                    with open(log_file_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(span_dict) + "\n")
                        f.flush()
            except Exception as e:
                sys.stderr.write(
                    f"[WARN] LocalJSONLFileSpanProcessor failed to write span: {e}\n"
                )

        def shutdown(self):
            pass

        def force_flush(self, timeout_millis=30000):
            return True

    class BaggageSpanProcessor(SpanProcessor):
        """Copies langfuse.session.id from OTel Baggage to span attributes.

        Langfuse v4 recommends OTel Baggage + a BaggageSpanProcessor so that
        trace-level attributes (like session.id) appear on every span, enabling
        reliable filtering and session grouping in the Langfuse UI.

        Reference: https://langfuse.com/integrations/native/opentelemetry#propagating-attributes

        # AGENT_DECISION: implemented as a custom processor (~8 lines) instead of
        # installing the official opentelemetry-processor-baggage package, to
        # honor the Radical Simplicity constraint (ADR-0009: no new heavy deps).
        # Only the langfuse.session.id key is copied — opt-in, no sensitive data leakage.
        """

        def on_start(self, span, parent_context=None):
            from opentelemetry.baggage import get_all

            # OTel Python 1.37: baggage.get_value re-exports context.get_value,
            # which looks up the key directly in the context — NOT through the
            # _BAGGAGE_KEY dict. Use get_all to properly read baggage entries.
            all_baggage = get_all(context=parent_context)
            value = all_baggage.get("langfuse.session.id")
            if value is not None:
                # Defense-in-depth (ADR-0037 layer 4): scrub any secret shape
                # from string span values before they reach the OTLP exporter.
                span.set_attribute("langfuse.session.id", redact_secrets(str(value)))

        def on_end(self, span):
            pass

        def shutdown(self):
            pass

        def force_flush(self, timeout_millis=30000):
            return True


def init_telemetry(in_memory_exporter=None):
    """
    Initializes OpenTelemetry and OpenInference tracer provider if installed.
    Runs silently as a no-op otherwise.
    """
    if not HAS_OTEL:
        sys.stderr.write(
            "[INFO] OpenTelemetry is not installed. Running in no-op tracing mode.\n"
        )
        return

    # If a real TracerProvider is already set (e.g., in repeated test setUps),
    # attach the new in-memory exporter to it instead of trying to replace it.
    # The global provider can only be set once per process (OTel's Once() guard),
    # so re-initialization augments the existing provider rather than replacing it.
    current_provider = trace.get_tracer_provider()
    if isinstance(current_provider, TracerProvider):
        # Attach session-level processors identically to the fresh-provider
        # path so outcomes are independent of call order (issue #75).
        _attach_langfuse_processors(current_provider)
        if in_memory_exporter is not None:
            current_provider.add_span_processor(SimpleSpanProcessor(in_memory_exporter))
        return

    service_name = os.getenv("OTEL_SERVICE_NAME", "quality-gates-review")
    project_name = os.getenv("REVIEW_OTEL_PROJECT_NAME", "default")

    resource = Resource.create(
        {
            "service.name": service_name,
            "openinference.project.name": project_name,
        }
    )

    provider = TracerProvider(resource=resource)

    # Register our crash-resistant local logging SpanProcessor
    provider.add_span_processor(LocalJSONLFileSpanProcessor())

    # Attach Langfuse session-level processors identically to the already-set
    # provider path so outcomes are independent of call order (issue #75).
    _attach_langfuse_processors(provider)

    if in_memory_exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(in_memory_exporter))
    else:
        if _is_langfuse_configured():
            # Langfuse Cloud export: explicit endpoint takes precedence over the
            # Langfuse default; auth headers always use the Langfuse Basic credentials.
            # AGENT_DECISION: do NOT call configure_otlp_endpoint() here — it would
            # set the Docker default into the env var, masking the Langfuse default.
            endpoint = (
                os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
                or _LANGFUSE_DEFAULT_OTLP_ENDPOINT
            )
            headers = _build_langfuse_auth_header()
        else:
            # Generic OTLP export (Docker default or explicit endpoint)
            configure_otlp_endpoint()
            endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
            headers = {}
            api_key = os.getenv("REVIEW_OTEL_API_KEY", "")
            if api_key:
                headers["x-api-key"] = api_key

            extra_headers_str = os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")
            if extra_headers_str:
                for item in extra_headers_str.split(","):
                    if "=" in item:
                        k, v = item.split("=", 1)
                        headers[k.strip()] = v.strip()

        if endpoint:
            try:
                exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers)
                # SimpleSpanProcessor exports each ended span synchronously to
                # the OTLP endpoint, instead of BatchSpanProcessor's deferred
                # batch flush: review.py is a short-lived CLI process, so a
                # batched processor could lose spans at exit; the synchronous
                # processor guarantees every span reaches the collector before
                # the process ends.
                provider.add_span_processor(SimpleSpanProcessor(exporter))
            except Exception as e:
                sys.stderr.write(f"[WARN] Failed to initialize OTLP exporter: {e}\n")

    trace.set_tracer_provider(provider)
