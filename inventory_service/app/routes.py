import logging

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.exceptions import ResourceNotFoundError
from app.models import Reservation, StockItem

logger = logging.getLogger("app.routes")
router = APIRouter()


@router.get("/stock", tags=["inventory"])
def stock(db: Session = Depends(get_db)):
    """Current SIMULATED stock levels (for demonstration)."""
    return {"simulated": True, "stock": {s.product_id: s.available for s in db.scalars(select(StockItem))}}


@router.get("/reservations/{order_id}", tags=["inventory"])
def reservations(order_id: str, db: Session = Depends(get_db)):
    rows = db.scalars(select(Reservation).where(Reservation.order_id == order_id)).all()
    if not rows:
        raise ResourceNotFoundError(f"No reservation for order '{order_id}'")
    return {"simulated": True, "order_id": order_id, "reservations": [
        {"product_id": r.product_id, "quantity": r.quantity, "status": r.status,
         "correlation_id": r.correlation_id} for r in rows]}


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
