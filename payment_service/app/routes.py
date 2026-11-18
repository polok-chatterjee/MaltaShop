import logging

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.exceptions import ResourceNotFoundError
from app.models import Payment

logger = logging.getLogger("app.routes")
router = APIRouter()


@router.get("/payments/{order_id}", tags=["payments"])
def get_payment(order_id: str, db: Session = Depends(get_db)):
    p = db.scalars(select(Payment).where(Payment.order_id == order_id)).first()
    if p is None:
        raise ResourceNotFoundError(f"No payment for order '{order_id}'")
    return {"simulated": True, "payment_id": p.id, "order_id": p.order_id, "status": p.status,
            "reason": p.reason, "correlation_id": p.correlation_id}


@router.get("/health", tags=["health"])
def health(request: Request, response: Response, db: Session = Depends(get_db)):
    settings = get_settings()
    try:
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        logger.exception("health check: database unavailable")
        db_status = "unavailable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    result = {"status": "ok" if db_status == "ok" else "degraded",
              "service": settings.service_name, "database": db_status}
    if settings.messaging_enabled:
        result["messaging"] = {"consumer_connected": request.app.state.consumer.connected.is_set()}
    return result
