import time
import functools
from datetime import datetime, timezone
from google.cloud import logging as cloud_logging
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

PROJECT_ID = "kyc-aml-project-488918"

# ── Cloud Logging setup
logging_client = cloud_logging.Client(project=PROJECT_ID)
logger = logging_client.logger("kyc-aml-pipeline")

# ── Cloud Trace setup
tracer_provider = TracerProvider()
cloud_trace_exporter = CloudTraceSpanExporter(project_id=PROJECT_ID)
tracer_provider.add_span_processor(BatchSpanProcessor(cloud_trace_exporter))
trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer("kyc-aml-multiagent")


def log_pipeline_event(event_type: str, customer_id: str, payload: dict):
    """Send a structured log entry to Google Cloud Logging."""
    entry = {
        "event_type": event_type,
        "customer_id": customer_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload
    }
    logger.log_struct(
        entry,
        severity="INFO",
        labels={
            "customer_id": customer_id,
            "event_type": event_type,
            "pipeline": "kyc-aml-multiagent"
        }
    )
    print(f"[CLOUD LOGGING] {event_type} logged for {customer_id}")


def log_pipeline_error(event_type: str, customer_id: str, error: str):
    """Send an error log entry to Google Cloud Logging."""
    entry = {
        "event_type": event_type,
        "customer_id": customer_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "error": error
    }
    logger.log_struct(
        entry,
        severity="ERROR",
        labels={
            "customer_id": customer_id,
            "event_type": event_type,
            "pipeline": "kyc-aml-multiagent"
        }
    )
    print(f"[CLOUD LOGGING] ERROR {event_type} logged for {customer_id}")


def trace_agent_call(agent_name: str):
    """Decorator that wraps a function in a Cloud Trace span."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with tracer.start_as_current_span(agent_name) as span:
                start = time.time()
                try:
                    result = func(*args, **kwargs)
                    duration_ms = int((time.time() - start) * 1000)
                    span.set_attribute("agent.name", agent_name)
                    span.set_attribute("agent.duration_ms", duration_ms)
                    span.set_attribute("agent.status", "success")
                    if isinstance(result, dict):
                        rec = result.get("gemini_recommendation") or result.get("aml_recommendation", "")
                        if rec:
                            span.set_attribute("agent.recommendation", rec)
                        cid = result.get("customer_id", "")
                        if cid:
                            span.set_attribute("agent.customer_id", cid)
                    print(f"[CLOUD TRACE] {agent_name} span completed in {duration_ms}ms")
                    return result
                except Exception as e:
                    span.set_attribute("agent.status", "error")
                    span.set_attribute("agent.error", str(e))
                    raise
        return wrapper
    return decorator


def flush_traces():
    """Force export of any pending trace spans."""
    tracer_provider.force_flush()