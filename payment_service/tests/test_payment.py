import pytest
from sqlalchemy import select

from app.handler import make_handler
from app.models import Outbox, Payment
from maltashop_common.errors import PermanentError
from maltashop_common.events import Event, EventType, make_event


@pytest.fixture()
def handler(session_factory):
    return make_handler(session_factory, {"C-DECLINE"})


def cmd(etype=EventType.PROCESS_PAYMENT, customer="C001", order="ORD-1", corr="trace-pay"):
    return make_event(etype, order_id=order, customer_id=customer, correlation_id=corr)


def replies(sf):
    with sf() as db:
        return [Event.model_validate_json(r.body) for r in db.scalars(select(Outbox).order_by(Outbox.id))]


def payments(sf):
    with sf() as db:
        return list(db.scalars(select(Payment)))


def test_payment_completes_and_replies_with_same_correlation_id(session_factory, handler):
    handler(cmd())
    (r,) = replies(session_factory)
    assert r.event_type == EventType.PAYMENT_COMPLETED and r.correlation_id == "trace-pay"
    assert r.payload["payment_id"].startswith("PAY-")
    assert payments(session_factory)[0].status == "COMPLETED"


def test_declined_customer_gets_payment_failed(session_factory, handler):
    handler(cmd(customer="C-DECLINE"))
    (r,) = replies(session_factory)
    assert r.event_type == EventType.PAYMENT_FAILED and "declined" in r.payload["reason"]
    assert payments(session_factory)[0].status == "FAILED"


def test_duplicate_command_creates_one_payment_and_one_reply(session_factory, handler):
    c = cmd()
    handler(c); handler(c); handler(c)
    assert len(payments(session_factory)) == 1 and len(replies(session_factory)) == 1


def test_asking_again_for_the_same_order_never_charges_twice(session_factory, handler):
    handler(cmd()); handler(cmd())                            # different event_ids, same order
    assert len(payments(session_factory)) == 1
    assert [r.event_type for r in replies(session_factory)] == [EventType.PAYMENT_COMPLETED] * 2


def test_refund_marks_payment_refunded_compensation(session_factory, handler):
    handler(cmd()); handler(cmd(EventType.REFUND_PAYMENT, corr="trace-refund"))
    assert payments(session_factory)[0].status == "REFUNDED"
    last = replies(session_factory)[-1]
    assert last.event_type == EventType.PAYMENT_REFUNDED and last.correlation_id == "trace-refund"


def test_refund_is_idempotent(session_factory, handler):
    handler(cmd()); handler(cmd(EventType.REFUND_PAYMENT)); handler(cmd(EventType.REFUND_PAYMENT))
    assert payments(session_factory)[0].status == "REFUNDED"
    assert [r.event_type for r in replies(session_factory)].count(EventType.PAYMENT_REFUNDED) == 2


def test_refund_without_a_completed_payment_is_a_permanent_error(session_factory, handler):
    with pytest.raises(PermanentError):
        handler(cmd(EventType.REFUND_PAYMENT))
    handler(cmd(customer="C-DECLINE", order="ORD-2"))
    with pytest.raises(PermanentError):
        handler(cmd(EventType.REFUND_PAYMENT, customer="C-DECLINE", order="ORD-2"))


def test_endpoint_and_health(client, session_factory, handler):
    handler(cmd())
    body = client.get("/payments/ORD-1").json()
    assert body["simulated"] is True and body["status"] == "COMPLETED"
    assert client.get("/payments/ORD-X").status_code == 404
    assert client.get("/health").json()["service"] == "payment-service"
