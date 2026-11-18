"""Business logic for notifications."""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exceptions import NotificationNotFoundError
from app.models import DeliveryStatus, Notification
from app.notifier import DeliveryError, Notifier
from app.schemas import NotificationCreate

logger = logging.getLogger("app.notifications")


class NotificationService:
    def __init__(self, db: Session, notifier: Notifier):
        self.db = db
        self.notifier = notifier

    def create_and_send(self, data: NotificationCreate, correlation_id: str) -> Notification:
        message = data.message or f"Your order {data.order_id} has been received."
        notification = Notification(
            order_id=data.order_id,
            customer_id=data.customer_id,
            channel=data.channel,
            message=message,
            status=DeliveryStatus.PENDING,
            correlation_id=correlation_id,
        )
        self.db.add(notification)
        self.db.commit()  # persist PENDING before attempting delivery

        notification.attempts += 1
        try:
            self.notifier.send(data.channel.value, data.customer_id, message)
            notification.status = DeliveryStatus.SENT
            logger.info("notification sent", extra={"notification_id": notification.id, "order_id": data.order_id})
        except DeliveryError as exc:
            notification.status = DeliveryStatus.FAILED
            notification.failure_reason = str(exc)[:255]
            logger.warning(
                "notification delivery failed",
                extra={"notification_id": notification.id, "order_id": data.order_id, "reason": str(exc)},
            )
        self.db.commit()
        return notification

    def get(self, notification_id: str) -> Notification:
        notification = self.db.get(Notification, notification_id)
        if notification is None:
            raise NotificationNotFoundError(notification_id)
        return notification

    def list(self, order_id: str | None = None) -> list[Notification]:
        stmt = select(Notification).order_by(Notification.created_at.desc())
        if order_id:
            stmt = stmt.where(Notification.order_id == order_id)
        return list(self.db.scalars(stmt))
