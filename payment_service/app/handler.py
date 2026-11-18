"""SIMULATED payments: no payment provider is contacted and no card data exists.

A payment is declined if the customer is on the configured decline list. This
exists only to exercise the saga's compensation path.
"""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from maltashop_common.errors import PermanentError
from maltashop_common.events import Event, EventType, reply_to
from maltashop_common.outbox import add_to_outbox, mark_processed

from app.models import Outbox, Payment, ProcessedEvent

logger = logging.getLogger("app.payment")


def _reply(db: Session, event: Event, etype: EventType, payload=None) -> None:
    add_to_outbox(db, Outbox, reply_to(event, etype, payload))


def _process(db: Session, event: Event, decline: set[str]) -> None:
    payment = db.scalars(select(Payment).where(Payment.order_id == event.order_id)).first()
    if payment is None:
        if event.customer_id in decline:
            payment = Payment(order_id=event.order_id, customer_id=event.customer_id, status="FAILED",
                              reason="card declined (simulated)", correlation_id=event.correlation_id)
        else:
            payment = Payment(order_id=event.order_id, customer_id=event.customer_id, status="COMPLETED",
                              correlation_id=event.correlation_id)
        db.add(payment)
        db.flush()
    # One payment per order: asking again simply repeats the original outcome.
    if payment.status == "FAILED":
        logger.warning("payment declined (simulated)", extra={"order_id": event.order_id, "payment_id": payment.id})
        _reply(db, event, EventType.PAYMENT_FAILED, {"reason": payment.reason})
    else:
        logger.info("payment completed (simulated)", extra={"order_id": event.order_id, "payment_id": payment.id})
        _reply(db, event, EventType.PAYMENT_COMPLETED, {"payment_id": payment.id})


def _refund(db: Session, event: Event) -> None:
    """Compensating action for a completed payment. Safe to run twice."""
    payment = db.scalars(select(Payment).where(Payment.order_id == event.order_id)).first()
    if payment is None or payment.status == "FAILED":
        raise PermanentError(f"no completed payment to refund for order '{event.order_id}'")
    payment.status = "REFUNDED"
    logger.info("payment refunded (simulated compensation)", extra={
        "order_id": event.order_id, "payment_id": payment.id})
    _reply(db, event, EventType.PAYMENT_REFUNDED, {"payment_id": payment.id})


def make_handler(session_factory, decline_customers: set[str]):
    def handle(event: Event) -> None:
        with session_factory() as db:
            if not mark_processed(db, ProcessedEvent, event):
                logger.info("duplicate event ignored", extra={
                    "event_id": event.event_id, "event_type": event.event_type.value, "order_id": event.order_id})
                return
            if event.event_type == EventType.PROCESS_PAYMENT:
                _process(db, event, decline_customers)
            elif event.event_type == EventType.REFUND_PAYMENT:
                _refund(db, event)
            else:
                raise PermanentError(f"Payment Service does not handle {event.event_type.value}")
            db.commit()
    return handle
