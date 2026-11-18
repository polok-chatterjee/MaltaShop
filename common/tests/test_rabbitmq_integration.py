"""Integration tests against a REAL RabbitMQ (skipped automatically when none is reachable)."""
import threading
import time

import pika
import pytest

from maltashop_common.correlation import correlation_id_ctx
from maltashop_common.errors import PermanentError, PublishError
from maltashop_common.events import EventType, make_event
from maltashop_common.messaging import RabbitConsumer, RabbitPublisher
from maltashop_common.topology import Q_NOTIFICATION, declare_topology, dlq_name
from tests.conftest import TEST_URL, requires_broker

pytestmark = requires_broker


@pytest.fixture(autouse=True)
def clean_queues():
    conn = pika.BlockingConnection(pika.URLParameters(TEST_URL))
    ch = conn.channel()
    declare_topology(ch)
    from maltashop_common.topology import QUEUE_BINDINGS
    for q in QUEUE_BINDINGS:
        ch.queue_purge(q)
        ch.queue_purge(dlq_name(q))
    conn.close()
    yield


def queue_depth(name):
    conn = pika.BlockingConnection(pika.URLParameters(TEST_URL))
    try:
        return conn.channel().queue_declare(name, passive=True).method.message_count
    finally:
        conn.close()


def wait_for(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_publish_is_confirmed_and_consumer_receives_event_with_correlation_id():
    got = []
    consumer = RabbitConsumer(TEST_URL, Q_NOTIFICATION, lambda e: got.append((e, correlation_id_ctx.get())))
    consumer.start()
    assert consumer.connected.wait(5)
    pub = RabbitPublisher(TEST_URL)
    event = make_event(EventType.ORDER_CREATED, order_id="ORD-1", customer_id="C1", correlation_id="corr-xyz")
    pub.publish(event)                                             # returns only after broker confirm
    assert wait_for(lambda: got)
    consumer.stop(); pub.close()
    received, ctx_corr = got[0]
    assert received.event_id == event.event_id
    assert received.correlation_id == "corr-xyz" and ctx_corr == "corr-xyz"
    assert wait_for(lambda: queue_depth(Q_NOTIFICATION) == 0)       # acknowledged -> removed


def test_unroutable_message_is_reported_not_silently_dropped():
    pub = RabbitPublisher(TEST_URL)
    with pytest.raises(PublishError):
        pub.publish_raw("no.such.key", b"{}", message_id="m", correlation_id="c", event_type="X")
    pub.close()


def test_permanent_failure_ends_in_dead_letter_queue():
    def bad(event):
        raise PermanentError("boom")

    consumer = RabbitConsumer(TEST_URL, Q_NOTIFICATION, bad)
    consumer.start(); assert consumer.connected.wait(5)
    pub = RabbitPublisher(TEST_URL)
    pub.publish(make_event(EventType.ORDER_CREATED, order_id="ORD-2", customer_id="C", correlation_id="c"))
    assert wait_for(lambda: queue_depth(dlq_name(Q_NOTIFICATION)) == 1)
    assert queue_depth(Q_NOTIFICATION) == 0
    consumer.stop(); pub.close()


def test_transient_failure_is_retried_then_acked():
    attempts = []

    def flaky(event):
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("db busy")

    consumer = RabbitConsumer(TEST_URL, Q_NOTIFICATION, flaky, max_attempts=3, backoff_seconds=0.05)
    consumer.start(); assert consumer.connected.wait(5)
    pub = RabbitPublisher(TEST_URL)
    pub.publish(make_event(EventType.ORDER_CREATED, order_id="ORD-3", customer_id="C", correlation_id="c"))
    assert wait_for(lambda: len(attempts) == 3)
    assert wait_for(lambda: queue_depth(Q_NOTIFICATION) == 0)
    assert queue_depth(dlq_name(Q_NOTIFICATION)) == 0
    consumer.stop(); pub.close()


def test_malformed_message_is_dead_lettered_not_requeued_forever():
    consumer = RabbitConsumer(TEST_URL, Q_NOTIFICATION, lambda e: None)
    consumer.start(); assert consumer.connected.wait(5)
    pub = RabbitPublisher(TEST_URL)
    pub.publish_raw("order.created", b"this is not json", message_id="m", correlation_id="c", event_type="OrderCreated")
    assert wait_for(lambda: queue_depth(dlq_name(Q_NOTIFICATION)) == 1)
    consumer.stop(); pub.close()


def test_message_survives_until_a_consumer_exists():
    """Core async property: publishing succeeds with NO consumer running; delivery happens later."""
    pub = RabbitPublisher(TEST_URL)
    pub.publish(make_event(EventType.ORDER_CREATED, order_id="ORD-4", customer_id="C", correlation_id="late"))
    pub.close()
    assert queue_depth(Q_NOTIFICATION) == 1
    got = []
    consumer = RabbitConsumer(TEST_URL, Q_NOTIFICATION, got.append)
    consumer.start()
    assert wait_for(lambda: got)
    consumer.stop()
    assert got[0].correlation_id == "late"
