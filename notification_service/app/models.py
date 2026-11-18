"""Database models for the Notification Service (separate DB from Order Service)."""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return f"NTF-{uuid.uuid4().hex[:12].upper()}"


class DeliveryStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class Channel(str, enum.Enum):
    EMAIL = "EMAIL"
    SMS = "SMS"


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    # order_id / customer_id are plain reference values, NOT foreign keys:
    # the order data lives in another service's database.
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[Channel] = mapped_column(Enum(Channel), default=Channel.EMAIL)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus), default=DeliveryStatus.PENDING
    )
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
