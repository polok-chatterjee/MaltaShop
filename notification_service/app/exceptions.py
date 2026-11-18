import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("app.errors")


class NotificationNotFoundError(Exception):
    def __init__(self, notification_id: str):
        self.notification_id = notification_id
        super().__init__(f"Notification '{notification_id}' not found")


def _body(request: Request, error: str, detail):
    return {
        "error": error,
        "detail": detail,
        "correlation_id": getattr(request.state, "correlation_id", None),
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotificationNotFoundError)
    async def not_found(request: Request, exc: NotificationNotFoundError):
        logger.warning("notification not found", extra={"notification_id": exc.notification_id})
        return JSONResponse(status_code=404, content=_body(request, "not_found", str(exc)))

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError):
        errors = [
            {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
            for e in exc.errors()
        ]
        logger.warning("validation failed", extra={"errors": errors})
        return JSONResponse(status_code=422, content=_body(request, "validation_error", errors))
