"""Correlation ID middleware: accept or generate, bind to logs, echo in response."""
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.requests import Request

from app.logging_config import correlation_id_ctx

logger = logging.getLogger("app.request")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, header_name: str = "X-Correlation-ID"):
        super().__init__(app)
        self.header_name = header_name

    async def dispatch(self, request: Request, call_next):
        # Propagation: reuse the caller's ID if supplied, otherwise generate one.
        correlation_id = request.headers.get(self.header_name) or str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        token = correlation_id_ctx.set(correlation_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled error")
            response = JSONResponse(
                status_code=500,
                content={
                    "error": "internal_error",
                    "detail": "An unexpected error occurred",
                    "correlation_id": correlation_id,
                },
            )
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.info(
            "request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        response.headers[self.header_name] = correlation_id
        correlation_id_ctx.reset(token)
        return response
