"""SIMULATED inventory: reserve and release stock in response to saga commands.

All-or-nothing: either every line of an order is reserved or none is.
Replies go through the outbox in the same transaction as the stock change.
"""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from maltashop_common.errors import PermanentError
from maltashop_common.events import Event, EventType, reply_to
from maltashop_common.outbox import add_to_outbox, mark_processed

from app.models import Outbox, ProcessedEvent, Reservation, StockItem

logger = logging.getLogger("app.inventory")


def seed_stock(session_factory, seed: str) -> None:
    with session_factory() as db:
        for part in filter(None, (p.strip() for p in seed.split(","))):
            product, qty = part.split(":")
            if db.get(StockItem, product) is None:
                db.add(StockItem(product_id=product, available=int(qty)))
        db.commit()


def _reply(db: Session, event: Event, etype: EventType, payload=None) -> None:
    add_to_outbox(db, Outbox, reply_to(event, etype, payload))


def _reserve(db: Session, event: Event) -> None:
    items = event.payload.get("items")
    if not isinstance(items, list) or not items:
        raise PermanentError("ReserveInventory without items")

    existing = db.scalars(select(Reservation).where(Reservation.order_id == event.order_id,
                                                    Reservation.status == "RESERVED")).all()
    if existing:                                          # same order asked twice: do not reserve twice
        logger.info("reservation already exists (simulated)", extra={"order_id": event.order_id})
        return _reply(db, event, EventType.INVENTORY_RESERVED, {"items": items})

    for line in items:
        stock = db.get(StockItem, line["product_id"])
        available = stock.available if stock else 0
        if available < line["quantity"]:
            reason = f"insufficient stock for {line['product_id']} (requested {line['quantity']}, available {available})"
            logger.warning("inventory reservation failed (simulated)", extra={
                "order_id": event.order_id, "product_id": line["product_id"], "reason": reason})
            return _reply(db, event, EventType.INVENTORY_RESERVATION_FAILED, {"reason": reason})

    for line in items:
        db.get(StockItem, line["product_id"]).available -= line["quantity"]
        db.add(Reservation(order_id=event.order_id, product_id=line["product_id"],
                           quantity=line["quantity"], correlation_id=event.correlation_id))
    logger.info("inventory reserved (simulated)", extra={"order_id": event.order_id, "lines": len(items)})
    _reply(db, event, EventType.INVENTORY_RESERVED, {"items": items})


def _release(db: Session, event: Event) -> None:
    """Compensating action: put reserved stock back. Safe to run twice."""
    held = db.scalars(select(Reservation).where(Reservation.order_id == event.order_id,
                                                Reservation.status == "RESERVED")).all()
    for r in held:
        db.get(StockItem, r.product_id).available += r.quantity
        r.status = "RELEASED"
    logger.info("inventory released (simulated compensation)", extra={
        "order_id": event.order_id, "lines": len(held)})
    _reply(db, event, EventType.INVENTORY_RELEASED, {"released_lines": len(held)})


def make_handler(session_factory):
    def handle(event: Event) -> None:
        with session_factory() as db:
            if not mark_processed(db, ProcessedEvent, event):
                logger.info("duplicate event ignored", extra={
                    "event_id": event.event_id, "event_type": event.event_type.value, "order_id": event.order_id})
                return
            if event.event_type == EventType.RESERVE_INVENTORY:
                _reserve(db, event)
            elif event.event_type == EventType.RELEASE_INVENTORY:
                _release(db, event)
            else:
                raise PermanentError(f"Inventory Service does not handle {event.event_type.value}")
            db.commit()
    return handle
