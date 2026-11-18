import json
import pytest
from pydantic import ValidationError

from maltashop_common.events import ROUTING_KEYS, Event, EventType, make_event, reply_to
from maltashop_common.topology import QUEUE_BINDINGS, definitions


def test_order_created_contains_required_fields():
    e = make_event(EventType.ORDER_CREATED, order_id="ORD-1", customer_id="C001", correlation_id="corr-1")
    data = json.loads(e.model_dump_json())
    for field in ("event_id", "event_type", "order_id", "customer_id", "correlation_id", "timestamp"):
        assert data[field]
    assert data["event_type"] == "OrderCreated"
    assert data["timestamp"].endswith(("Z", "+00:00"))   # ISO 8601, UTC


def test_event_ids_are_unique():
    a = make_event(EventType.ORDER_CREATED, order_id="O", customer_id="C", correlation_id="x")
    b = make_event(EventType.ORDER_CREATED, order_id="O", customer_id="C", correlation_id="x")
    assert a.event_id != b.event_id


def test_roundtrip_json():
    e = make_event(EventType.RESERVE_INVENTORY, order_id="O", customer_id="C", correlation_id="x",
                   payload={"items": [{"product_id": "P1", "quantity": 2}]})
    assert Event.model_validate_json(e.model_dump_json()) == e


def test_unknown_field_and_unknown_type_rejected():
    base = json.loads(make_event(EventType.ORDER_CREATED, order_id="O", customer_id="C", correlation_id="x").model_dump_json())
    with pytest.raises(ValidationError):
        Event.model_validate({**base, "surprise": 1})
    with pytest.raises(ValidationError):
        Event.model_validate({**base, "event_type": "Nope"})
    with pytest.raises(ValidationError):
        Event.model_validate({k: v for k, v in base.items() if k != "correlation_id"})


def test_reply_keeps_correlation_id():
    cmd = make_event(EventType.RESERVE_INVENTORY, order_id="O", customer_id="C", correlation_id="trace-9")
    rep = reply_to(cmd, EventType.INVENTORY_RESERVED)
    assert rep.correlation_id == "trace-9" and rep.event_id != cmd.event_id and rep.order_id == "O"


def test_every_event_type_has_a_routing_key_and_a_queue():
    assert set(ROUTING_KEYS) == set(EventType)
    bound = {k for keys in QUEUE_BINDINGS.values() for k in keys}
    assert bound == set(ROUTING_KEYS.values())          # nothing unrouted, nothing bound twice


def test_definitions_json_matches_topology():
    d = definitions("/")
    names = {q["name"] for q in d["queues"]}
    assert names == {q for q in QUEUE_BINDINGS} | {f"{q}.dlq" for q in QUEUE_BINDINGS}
    for q in d["queues"]:
        if not q["name"].endswith(".dlq"):
            assert q["arguments"]["x-dead-letter-exchange"] == "maltashop.dlx"
