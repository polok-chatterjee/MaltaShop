import logging

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.notifier import Notifier, SimulatedNotifier
from app.schemas import NotificationCreate, NotificationOut
from app.services import NotificationService

logger = logging.getLogger("app.routes")
router = APIRouter()


def get_notifier() -> Notifier:
    return SimulatedNotifier()


def get_service(db: Session = Depends(get_db), notifier: Notifier = Depends(get_notifier)) -> NotificationService:
    return NotificationService(db, notifier)


@router.post("/notifications", response_model=NotificationOut, status_code=status.HTTP_201_CREATED, tags=["notifications"])
def create_notification(
    payload: NotificationCreate,
    request: Request,
    response: Response,
    service: NotificationService = Depends(get_service),
):
    n = service.create_and_send(payload, correlation_id=request.state.correlation_id)
    response.headers["Location"] = f"/notifications/{n.id}"
    return n


@router.get("/notifications", response_model=list[NotificationOut], tags=["notifications"])
def list_notifications(order_id: str | None = None, service: NotificationService = Depends(get_service)):
    return service.list(order_id)


@router.get("/notifications/{notification_id}", response_model=NotificationOut, tags=["notifications"])
def get_notification(notification_id: str, service: NotificationService = Depends(get_service)):
    return service.get(notification_id)


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
