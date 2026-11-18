"""HTTP routes. Thin layer: parse -> call service -> shape response."""
import logging

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.schemas import OrderCreate, OrderOut
from app.services import OrderService

logger = logging.getLogger("app.routes")
router = APIRouter()


def get_order_service(db: Session = Depends(get_db)) -> OrderService:
    return OrderService(db)


@router.post("/orders", response_model=OrderOut, status_code=status.HTTP_201_CREATED, tags=["orders"])
def create_order(
    payload: OrderCreate,
    request: Request,
    response: Response,
    service: OrderService = Depends(get_order_service),
):
    order = service.create_order(payload, correlation_id=request.state.correlation_id)
    response.headers["Location"] = f"/orders/{order.id}"
    return order


@router.get("/orders/{order_id}", response_model=OrderOut, tags=["orders"])
def get_order(order_id: str, service: OrderService = Depends(get_order_service)):
    return service.get_order(order_id)


@router.get("/health", tags=["health"])
def health(response: Response, db: Session = Depends(get_db)):
    settings = get_settings()
    try:
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        logger.exception("health check: database unavailable")
        db_status = "unavailable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "service": settings.service_name,
        "database": db_status,
    }
