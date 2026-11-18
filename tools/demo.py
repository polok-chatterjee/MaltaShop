#!/usr/bin/env python3
"""Helpers for demonstrating reliability behaviour against the running system.

    python tools/demo.py duplicate   # publish the SAME OrderCreated event 3 times
    python tools/demo.py poison      # publish a malformed message (ends in the dead-letter queue)
    python tools/demo.py dlq         # show queue depths (including dead-letter queues)
    python tools/demo.py purge       # empty all queues (used by reset_demo_data.sh)
Uses RABBITMQ_URL (default: amqp://guest:guest@localhost:5672/maltashop).
"""
import os
import sys
import uuid

import pika

from maltashop_common.events import EventType, make_event
from maltashop_common.messaging import RabbitPublisher
from maltashop_common.topology import QUEUE_BINDINGS, declare_topology, dlq_name

URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/maltashop")


def depths():
    conn = pika.BlockingConnection(pika.URLParameters(URL))
    ch = conn.channel()
    declare_topology(ch)
    out = {}
    for q in QUEUE_BINDINGS:
        for name in (q, dlq_name(q)):
            out[name] = ch.queue_declare(name, passive=True).method.message_count
    conn.close()
    return out


def main(cmd):
    if cmd == "duplicate":
        order_id, corr = f"ORD-DUPDEMO-{uuid.uuid4().hex[:5].upper()}", f"dup-{uuid.uuid4().hex[:8]}"
        ev = make_event(EventType.ORDER_CREATED, order_id=order_id, customer_id="C001", correlation_id=corr)
        pub = RabbitPublisher(URL)
        for i in range(3):
            pub.publish(ev)
            print(f"published copy {i + 1}/3 of event {ev.event_id}")
        pub.close()
        print(f"\norder_id={order_id}  correlation_id={corr}")
        print(f"Check: curl 'localhost:8002/notifications?order_id={order_id}'   (expect exactly 1)")
        print(f"       python tools/trace.py {corr}")
    elif cmd == "poison":
        corr = f"poison-{uuid.uuid4().hex[:8]}"
        pub = RabbitPublisher(URL)
        pub.publish_raw("order.created", b'{"this": "is not a valid OrderCreated event"}',
                        message_id=str(uuid.uuid4()), correlation_id=corr, event_type="OrderCreated")
        pub.close()
        print("published a malformed message to routing key order.created")
        print("Check: python tools/demo.py dlq   (notification.order_events.dlq should have 1 message)")
    elif cmd == "dlq":
        for name, n in depths().items():
            print(f"{name:<36} {n}")
    elif cmd == "purge":
        conn = pika.BlockingConnection(pika.URLParameters(URL))
        ch = conn.channel()
        declare_topology(ch)
        for q in QUEUE_BINDINGS:
            ch.queue_purge(q); ch.queue_purge(dlq_name(q))
        conn.close()
        print("queues purged")
    else:
        print(__doc__); sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
