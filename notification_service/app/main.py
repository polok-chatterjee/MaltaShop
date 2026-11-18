"""Application factory / entry point."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.database import init_db
from app.exceptions import register_exception_handlers
from app.logging_config import configure_logging
from app.middleware import CorrelationIdMiddleware
from app.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logging.getLogger("app").info("service started")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.service_name, settings.log_level)
    app = FastAPI(title="MaltaShop Notification Service", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CorrelationIdMiddleware, header_name=settings.correlation_id_header)
    register_exception_handlers(app)
    app.include_router(router)
    return app


app = create_app()
