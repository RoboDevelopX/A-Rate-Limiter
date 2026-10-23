import json
import logging

from prometheus_client import generate_latest

from mlratelimit import FixedWindow, Limiter, ManualClock, MemoryBackend, Quota
from mlratelimit.log import JsonFormatter, configure_logging
from mlratelimit.metrics import InstrumentedLimiter, Metrics


async def test_instrumented_limiter_counts_decisions_and_latency(clock: ManualClock) -> None:
    metrics = Metrics()
    limiter = InstrumentedLimiter(
        Limiter(FixedWindow(), Quota(1, 60), MemoryBackend(clock)), metrics, "free:default"
    )
    await limiter.check("u")
    await limiter.check("u")
    metrics.record_backend_error("timeout")
    text = generate_latest(metrics.registry).decode()
    assert 'ratelimit_decisions_total{decision="allowed",limiter="free:default"} 1.0' in text
    assert 'ratelimit_decisions_total{decision="blocked",limiter="free:default"} 1.0' in text
    assert 'ratelimit_backend_errors_total{reason="timeout"} 1.0' in text
    assert 'ratelimit_check_duration_seconds_count{limiter="free:default"} 2.0' in text


def test_json_formatter_includes_extra_fields_and_exceptions() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        "mlratelimit.x", logging.WARNING, __file__, 1, "hi %s", ("there",), None
    )
    record.reason = "timeout"
    line = json.loads(formatter.format(record))
    assert line["message"] == "hi there"
    assert line["level"] == "WARNING"
    assert line["reason"] == "timeout"
    error = RuntimeError("boom")
    record.exc_info = (RuntimeError, error, None)
    assert "boom" in json.loads(formatter.format(record))["exception"]


def test_configure_logging_installs_json_handler() -> None:
    root = logging.getLogger()
    previous = (root.handlers[:], root.level)
    try:
        configure_logging("debug")
        assert root.level == logging.DEBUG
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
    finally:
        root.handlers[:], _ = previous
        root.setLevel(previous[1])
