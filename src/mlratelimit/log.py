"""JSON log formatting for applications that use this library.

The library itself only creates loggers; call :func:`configure_logging` from your app.
"""

import json
import logging
from typing import Any

# Attributes every LogRecord has; anything else came from ``extra=`` and is emitted as a field.
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: time, level, logger, message, plus any ``extra`` fields."""

    def format(self, record: logging.LogRecord) -> str:
        """Render ``record`` as a JSON line."""
        payload: dict[str, Any] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update({k: v for k, v in vars(record).items() if k not in _STANDARD})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Send all logs to stderr as JSON lines."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
