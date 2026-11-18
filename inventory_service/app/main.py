import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.exceptions import register_exception_handlers
from app.handler import seed_stock
from app.logging_config import configure_logging
from app.middleware import CorrelationIdMiddleware
from app.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings = get_settings()
    seed_stock(SessionLocal, settings.inventory_seed)
    if settings.messaging_enabled:
        from app.messaging import start_messaging
        start_messaging(app, settings)
    logging.getLogger("app").info("service started (SIMULATED inventory)",
                                  extra={"messaging_enabled": settings.messaging_enabled})
    yield
    if settings.messaging_enabled:
        from app.messaging import stop_messaging
        stop_messaging(app)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.service_name, settings.log_level)
    app = FastAPI(title="MaltaShop Inventory Service (simulated)", version="0.2.0", lifespan=lifespan)
    app.add_middleware(CorrelationIdMiddleware, header_name=settings.correlation_id_header)
    register_exception_handlers(app)
    app.include_router(router)
    return app


app = create_app()
