"""Payment Service database (SIMULATED payments; owned exclusively by this service)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from maltashop_common.outbox import make_outbox_model, make_processed_event_model

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: f"PAY-{uuid.uuid4().hex[:12].upper()}")
    order_id: Mapped[str] = mapped_column(String(64), unique=True)       # one payment per order
    customer_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))                       # COMPLETED | FAILED | REFUNDED
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


Outbox = make_outbox_model(Base)
ProcessedEvent = make_processed_event_model(Base)
