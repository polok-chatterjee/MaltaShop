"""End-to-end tests: real services + real RabbitMQ. Nothing is mocked."""
import time
import uuid

from maltashop_common.events import EventType, make_event
from maltashop_common.messaging import RabbitPublisher
from maltashop_common.topology import Q_NOTIFICATION, dlq_name

from tests_e2e.conftest import TEST_URL, queue_depth, wait_until


def corr() -> str:
    return f"e2e-{uuid.uuid4().hex[:10]}"


def quiesce(stack, stable_for=1.0, timeout=20):
    """Wait until no OrderCreated/Confirmed/Cancelled events are waiting for the Notification Service
    (stable for a moment), so leftovers from earlier tests cannot interfere with a timing-sensitive test."""
    state = {"since": None}

    def quiet():
        if queue_depth(Q_NOTIFICATION) == 0:
            state["since"] = state["since"] or time.time()
            return time.time() - state["since"] >= stable_for
        state["since"] = None
        return False

    wait_until(quiet, timeout=timeout, message="broker to go quiet")


def settled(stack, order_id, saga_state, timeout=15):
    return wait_until(lambda: (o := stack.order(order_id))["saga_state"] == saga_state and o,
                      timeout=timeout, message=f"order {order_id} saga_state={saga_state}")


# =============================================================== 1. async event flow
def test_order_api_returns_without_waiting_and_event_is_delivered_later(stack):
    """Notification Service is DOWN: the order is still accepted, the event waits in the durable
    queue, and is consumed after the service comes back."""
    stack.stop("notification")
    c = corr()
    t0 = time.perf_counter()
    r = stack.post_order(corr=c)
    elapsed = time.perf_counter() - t0
    assert r.status_code == 201 and r.json()["status"] == "PENDING" and r.json()["correlation_id"] == c
    assert elapsed < 2.0, f"API took {elapsed:.2f}s - it must not wait for notification processing"
    order_id = r.json()["order_id"]

    wait_until(lambda: queue_depth(Q_NOTIFICATION) >= 1, message="OrderCreated waiting in broker queue")
    stack.start("notification")                                   # service returns
    notes = wait_until(lambda: [n for n in stack.notifications(order_id) if "received" in n["message"]],
                       message="notification created from queued OrderCreated event")
    assert notes[0]["correlation_id"] == c and notes[0]["status"] == "SENT"
    ok = stack.log_lines("notification", message="event processed successfully", order_id=order_id,
                         event_type="OrderCreated")
    assert len(ok) == 1 and ok[0]["correlation_id"] == c


# =============================================================== 2. happy path + correlation id
def test_happy_path_confirms_order_and_same_correlation_id_appears_in_every_service(stack):
    before = stack.stock()["P001"]
    c = corr()
    order = stack.post_order(corr=c, product="P001", qty=3).json()
    oid = order["order_id"]
    done = settled(stack, oid, "COMPLETED")
    assert done["status"] == "CONFIRMED"
    assert stack.stock()["P001"] == before - 3
    pay = stack.http.get(f"{stack.url('payment')}/payments/{oid}").json()
    assert pay["status"] == "COMPLETED" and pay["correlation_id"] == c and pay["simulated"] is True

    def two_notifications():
        found = stack.notifications(oid)
        return found if len(found) == 2 else None

    notes = wait_until(two_notifications, message="2 notifications (created + confirmed)")
    assert {n["correlation_id"] for n in notes} == {c}
    assert any("received" in n["message"] for n in notes) and any("confirmed" in n["message"] for n in notes)

    for service in ("order", "notification", "inventory", "payment"):
        assert stack.log_lines(service, correlation_id=c), f"{service} has no log line with correlation id {c}"


# =============================================================== 3. compensation: inventory fails
def test_inventory_failure_cancels_order_and_payment_is_never_attempted(stack):
    c = corr()
    oid = stack.post_order(product="P003", qty=1, corr=c).json()["order_id"]      # P003 has zero stock
    done = settled(stack, oid, "CANCELLED")
    assert done["status"] == "CANCELLED" and "insufficient stock for P003" in done["failure_reason"]
    assert stack.http.get(f"{stack.url('payment')}/payments/{oid}").status_code == 404
    wait_until(lambda: any("cancelled" in n["message"] for n in stack.notifications(oid)), message="cancel notice")


# =============================================================== 4. compensation: payment fails
def test_payment_failure_releases_reserved_inventory(stack):
    before = stack.stock()["P002"]
    c = corr()
    oid = stack.post_order(customer="C-DECLINE", product="P002", qty=2, corr=c).json()["order_id"]
    done = settled(stack, oid, "CANCELLED")                       # CANCELLED only after the release is confirmed
    assert done["status"] == "CANCELLED" and "declined" in done["failure_reason"]
    assert stack.stock()["P002"] == before                        # stock reserved, then released: net zero
    res = stack.http.get(f"{stack.url('inventory')}/reservations/{oid}").json()
    assert [r["status"] for r in res["reservations"]] == ["RELEASED"]
    assert stack.http.get(f"{stack.url('payment')}/payments/{oid}").json()["status"] == "FAILED"
    assert stack.log_lines("inventory", correlation_id=c, message="inventory released (simulated compensation)")


# =============================================================== 5. compensation: cancel after payment
def test_cancel_after_successful_payment_refunds_and_restocks(stack):
    before = stack.stock()["P002"]
    c = corr()
    oid = stack.post_order(product="P002", qty=1, corr=c).json()["order_id"]
    settled(stack, oid, "COMPLETED")
    assert stack.stock()["P002"] == before - 1

    r = stack.http.post(f"{stack.url('order')}/orders/{oid}/cancel", headers={"X-Correlation-ID": c})
    assert r.status_code == 202
    done = settled(stack, oid, "CANCELLED")
    assert done["status"] == "CANCELLED"
    assert stack.http.get(f"{stack.url('payment')}/payments/{oid}").json()["status"] == "REFUNDED"
    assert stack.stock()["P002"] == before
    assert stack.log_lines("payment", correlation_id=c, message="payment refunded (simulated compensation)")
    assert stack.http.post(f"{stack.url('order')}/orders/{oid}/cancel").status_code == 409


# =============================================================== 6. duplicate delivery
def test_duplicate_event_delivery_creates_exactly_one_notification(stack):
    pub = RabbitPublisher(TEST_URL)
    oid, c = f"ORD-DUP-{uuid.uuid4().hex[:6].upper()}", corr()
    ev = make_event(EventType.ORDER_CREATED, order_id=oid, customer_id="C001", correlation_id=c)
    for _ in range(3):
        pub.publish(ev)                                           # SAME event_id, delivered three times
    sentinel_id = f"ORD-SENT-{uuid.uuid4().hex[:6].upper()}"
    pub.publish(make_event(EventType.ORDER_CREATED, order_id=sentinel_id, customer_id="C001", correlation_id=c))
    pub.close()
    wait_until(lambda: stack.notifications(sentinel_id), message="sentinel processed (queue is FIFO)")
    assert len(stack.notifications(oid)) == 1
    dup = stack.log_lines("notification", message="duplicate event ignored", event_id=ev.event_id)
    assert len(dup) == 2 and all(r["correlation_id"] == c for r in dup)


# =============================================================== 7. failure handling: dead letter
def test_malformed_message_is_dead_lettered_and_service_keeps_working(stack):
    before = queue_depth(dlq_name(Q_NOTIFICATION))
    pub = RabbitPublisher(TEST_URL)
    pub.publish_raw("order.created", b'{"this": "is not a valid event"}', message_id="poison-1",
                    correlation_id="poison-corr", event_type="OrderCreated")
    pub.close()
    wait_until(lambda: queue_depth(dlq_name(Q_NOTIFICATION)) == before + 1, message="message in DLQ")
    err = wait_until(lambda: stack.log_lines("notification", message="message rejected: invalid event schema"),
                     message="structured error log")
    assert err[0]["level"] == "ERROR" and err[0]["outcome"] == "dead_letter"
    assert queue_depth(Q_NOTIFICATION) == 0                       # not stuck / not redelivered forever
    oid = stack.post_order().json()["order_id"]
    wait_until(lambda: stack.notifications(oid), message="service still processes events")
    settled(stack, oid, "COMPLETED")                              # let this order's saga finish cleanly


# =============================================================== 8. retry behaviour
def test_transient_failures_are_retried_with_backoff_then_processed_once(stack):
    quiesce(stack)
    stack.stop("notification")
    stack.start("notification", env={"SIMULATE_TRANSIENT_FAILURES": "2"})
    try:
        pub = RabbitPublisher(TEST_URL)
        oid, c = f"ORD-RETRY-{uuid.uuid4().hex[:6].upper()}", corr()
        ev = make_event(EventType.ORDER_CREATED, order_id=oid, customer_id="C001", correlation_id=c)
        pub.publish(ev); pub.close()
        notes = wait_until(lambda: stack.notifications(oid), message="notification after retries")
        assert len(notes) == 1 and notes[0]["correlation_id"] == c
        retries = stack.log_lines("notification", message="transient failure, will retry", event_id=ev.event_id)
        assert [r["attempt"] for r in retries] == [1, 2]
        assert [r["retry_in_seconds"] for r in retries] == [0.2, 0.4]          # exponential backoff
        assert all(r["correlation_id"] == c and r["level"] == "WARNING" for r in retries)
        assert queue_depth(dlq_name(Q_NOTIFICATION)) >= 0
    finally:
        stack.stop("notification")
        stack.start("notification")


# =============================================================== 9. retries exhausted -> DLQ
def test_retries_exhausted_ends_in_dead_letter_queue(stack):
    quiesce(stack)
    stack.stop("notification")
    stack.start("notification", env={"SIMULATE_TRANSIENT_FAILURES": "99"})
    try:
        before = queue_depth(dlq_name(Q_NOTIFICATION))
        pub = RabbitPublisher(TEST_URL)
        oid, c = f"ORD-DEAD-{uuid.uuid4().hex[:6].upper()}", corr()
        ev = make_event(EventType.ORDER_CREATED, order_id=oid, customer_id="C001", correlation_id=c)
        pub.publish(ev); pub.close()
        wait_until(lambda: queue_depth(dlq_name(Q_NOTIFICATION)) == before + 1, message="DLQ message")
        err = stack.log_lines("notification", message="retries exhausted, dead-lettering message", event_id=ev.event_id)
        assert err and err[0]["level"] == "ERROR" and err[0]["attempt"] == 3 and err[0]["correlation_id"] == c
        assert stack.notifications(oid) == []
    finally:
        stack.stop("notification")
        stack.start("notification")
