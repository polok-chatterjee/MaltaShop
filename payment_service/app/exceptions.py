import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("app.errors")


class ResourceNotFoundError(Exception):
    pass


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ResourceNotFoundError)
    async def not_found(request: Request, exc: ResourceNotFoundError):
        return JSONResponse(status_code=404, content={
            "error": "not_found", "detail": str(exc),
            "correlation_id": getattr(request.state, "correlation_id", None)})
