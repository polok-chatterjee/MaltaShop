"""Structured (JSON) logging with correlation ID injection."""
import contextvars
import json
import logging
from datetime import datetime, timezone

# Holds the correlation ID of the request/message currently being processed.
# Shared with maltashop_common so consumers log the same field.
from maltashop_common.correlation import correlation_id_ctx  # noqa: E402,F401

# Attributes that belong to LogRecord itself; anything else is treated as "extra".
_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "correlation_id": correlation_id_ctx.get(),
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(service_name: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service_name))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # Our middleware already logs requests in a structured way.
    logging.getLogger("uvicorn.access").disabled = True
    # pika logs every connection step at INFO; keep only warnings/errors from the client library.
    logging.getLogger("pika").setLevel(logging.WARNING)
