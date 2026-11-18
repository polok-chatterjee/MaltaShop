"""Inventory Service database (SIMULATED stock; owned exclusively by this service)."""
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from maltashop_common.outbox import make_outbox_model, make_processed_event_model

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class StockItem(Base):
    __tablename__ = "stock"
    product_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    available: Mapped[int] = mapped_column(Integer)


class Reservation(Base):
    __tablename__ = "reservations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="RESERVED")    # RESERVED | RELEASED
    correlation_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


Outbox = make_outbox_model(Base)
ProcessedEvent = make_processed_event_model(Base)
