import pytest
from sqlalchemy import select

from app.handler import make_handler, seed_stock
from app.models import Outbox, Reservation, StockItem
from maltashop_common.errors import PermanentError
from maltashop_common.events import Event, EventType, make_event


@pytest.fixture()
def handler(session_factory):
    seed_stock(session_factory, "P001:10,P002:3,P003:0")
    return make_handler(session_factory)


def cmd(etype, items=None, order="ORD-1", corr="trace-inv"):
    return make_event(etype, order_id=order, customer_id="C001", correlation_id=corr,
                      payload={"items": items} if items is not None else {})


def stock(sf):
    with sf() as db:
        return {s.product_id: s.available for s in db.scalars(select(StockItem))}


def replies(sf):
    with sf() as db:
        return [Event.model_validate_json(r.body) for r in db.scalars(select(Outbox).order_by(Outbox.id))]


def test_reserve_decrements_stock_and_replies_with_same_correlation_id(session_factory, handler):
    handler(cmd(EventType.RESERVE_INVENTORY, [{"product_id": "P001", "quantity": 4}]))
    assert stock(session_factory)["P001"] == 6
    (r,) = replies(session_factory)
    assert r.event_type == EventType.INVENTORY_RESERVED and r.correlation_id == "trace-inv"


def test_insufficient_stock_replies_failure_and_changes_nothing(session_factory, handler):
    handler(cmd(EventType.RESERVE_INVENTORY, [{"product_id": "P003", "quantity": 1}]))
    (r,) = replies(session_factory)
    assert r.event_type == EventType.INVENTORY_RESERVATION_FAILED
    assert "insufficient stock for P003" in r.payload["reason"]
    assert stock(session_factory)["P003"] == 0


def test_reservation_is_all_or_nothing(session_factory, handler):
    handler(cmd(EventType.RESERVE_INVENTORY, [{"product_id": "P001", "quantity": 2}, {"product_id": "P002", "quantity": 99}]))
    assert stock(session_factory) == {"P001": 10, "P002": 3, "P003": 0}      # P001 NOT taken
    assert replies(session_factory)[0].event_type == EventType.INVENTORY_RESERVATION_FAILED


def test_unknown_product_is_a_failed_reservation(session_factory, handler):
    handler(cmd(EventType.RESERVE_INVENTORY, [{"product_id": "NOPE", "quantity": 1}]))
    assert replies(session_factory)[0].event_type == EventType.INVENTORY_RESERVATION_FAILED


def test_release_restores_stock_compensation(session_factory, handler):
    handler(cmd(EventType.RESERVE_INVENTORY, [{"product_id": "P001", "quantity": 4}]))
    handler(cmd(EventType.RELEASE_INVENTORY))
    assert stock(session_factory)["P001"] == 10
    assert replies(session_factory)[-1].event_type == EventType.INVENTORY_RELEASED
    with session_factory() as db:
        assert db.scalars(select(Reservation)).one().status == "RELEASED"


def test_release_twice_does_not_double_restock(session_factory, handler):
    handler(cmd(EventType.RESERVE_INVENTORY, [{"product_id": "P001", "quantity": 4}]))
    handler(cmd(EventType.RELEASE_INVENTORY))
    handler(cmd(EventType.RELEASE_INVENTORY))                 # a different event_id, same intent
    assert stock(session_factory)["P001"] == 10


def test_duplicate_command_is_applied_once(session_factory, handler):
    c = cmd(EventType.RESERVE_INVENTORY, [{"product_id": "P001", "quantity": 4}])
    handler(c); handler(c); handler(c)                        # same event_id redelivered
    assert stock(session_factory)["P001"] == 6                # decremented once
    assert len(replies(session_factory)) == 1                 # one reply only


def test_same_order_reserved_twice_does_not_double_take(session_factory, handler):
    handler(cmd(EventType.RESERVE_INVENTORY, [{"product_id": "P001", "quantity": 4}]))
    handler(cmd(EventType.RESERVE_INVENTORY, [{"product_id": "P001", "quantity": 4}]))   # new event_id
    assert stock(session_factory)["P001"] == 6


def test_malformed_command_is_a_permanent_error_and_leaves_no_trace(session_factory, handler):
    with pytest.raises(PermanentError):
        handler(cmd(EventType.RESERVE_INVENTORY, []))
    with pytest.raises(PermanentError):
        handler(make_event(EventType.PROCESS_PAYMENT, order_id="O", customer_id="C", correlation_id="c"))
    assert replies(session_factory) == []


def test_stock_and_health_endpoints(client, session_factory):
    seed_stock(session_factory, "P001:10")
    assert client.get("/stock").json() == {"simulated": True, "stock": {"P001": 10}}
    assert client.get("/health").json()["service"] == "inventory-service"
    assert client.get("/reservations/ORD-NONE").status_code == 404
