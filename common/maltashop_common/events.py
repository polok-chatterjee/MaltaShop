"""The single event/command envelope used by every service.

Every message on the broker, whether a domain event (OrderCreated), a saga
command (ReserveInventory) or a saga reply (InventoryReserved), uses this one
envelope. Type-specific data lives in `payload`.
"""
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
    # Domain events (published by Order Service, consumed by Notification Service)
    ORDER_CREATED = "OrderCreated"
    ORDER_CONFIRMED = "OrderConfirmed"
    ORDER_CANCELLED = "OrderCancelled"
    # Saga commands (Order Service -> Inventory / Payment)
    RESERVE_INVENTORY = "ReserveInventory"
    RELEASE_INVENTORY = "ReleaseInventory"
    PROCESS_PAYMENT = "ProcessPayment"
    REFUND_PAYMENT = "RefundPayment"
    # Saga replies (Inventory / Payment -> Order Service)
    INVENTORY_RESERVED = "InventoryReserved"
    INVENTORY_RESERVATION_FAILED = "InventoryReservationFailed"
    INVENTORY_RELEASED = "InventoryReleased"
    PAYMENT_COMPLETED = "PaymentCompleted"
    PAYMENT_FAILED = "PaymentFailed"
    PAYMENT_REFUNDED = "PaymentRefunded"


ROUTING_KEYS: dict[EventType, str] = {
    EventType.ORDER_CREATED: "order.created",
    EventType.ORDER_CONFIRMED: "order.confirmed",
    EventType.ORDER_CANCELLED: "order.cancelled",
    EventType.RESERVE_INVENTORY: "inventory.reserve",
    EventType.RELEASE_INVENTORY: "inventory.release",
    EventType.PROCESS_PAYMENT: "payment.process",
    EventType.REFUND_PAYMENT: "payment.refund",
    EventType.INVENTORY_RESERVED: "inventory.reserved",
    EventType.INVENTORY_RESERVATION_FAILED: "inventory.reservation_failed",
    EventType.INVENTORY_RELEASED: "inventory.released",
    EventType.PAYMENT_COMPLETED: "payment.completed",
    EventType.PAYMENT_FAILED: "payment.failed",
    EventType.PAYMENT_REFUNDED: "payment.refunded",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Event(BaseModel):
    """Envelope (schema version 1). Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()),
                          description="Globally unique ID; the idempotency key for consumers")
    event_type: EventType
    schema_version: int = 1
    order_id: str = Field(min_length=1, max_length=64)
    customer_id: str = Field(min_length=1, max_length=64)
    correlation_id: str = Field(min_length=1, max_length=64,
                                description="Copied unchanged from the originating HTTP request")
    timestamp: datetime = Field(default_factory=_utcnow, description="UTC creation time")
    payload: dict[str, Any] = Field(default_factory=dict,
                                    description="Type-specific data (items, reason, payment_id ...)")

    @property
    def routing_key(self) -> str:
        return ROUTING_KEYS[self.event_type]


def make_event(event_type: EventType, *, order_id: str, customer_id: str,
               correlation_id: str, payload: dict | None = None) -> Event:
    return Event(event_type=event_type, order_id=order_id, customer_id=customer_id,
                 correlation_id=correlation_id, payload=payload or {})


def reply_to(event: Event, event_type: EventType, payload: dict | None = None) -> Event:
    """Build a follow-up event that keeps the SAME correlation ID (propagation)."""
    return make_event(event_type, order_id=event.order_id, customer_id=event.customer_id,
                      correlation_id=event.correlation_id, payload=payload)
