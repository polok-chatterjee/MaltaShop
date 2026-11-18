"""Correlation ID context shared by HTTP middleware, consumers and the relay."""
import contextvars
import uuid

correlation_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default="-"
)


def new_correlation_id() -> str:
    return str(uuid.uuid4())
