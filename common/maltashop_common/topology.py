"""RabbitMQ topology: single source of truth for exchanges, queues and bindings.

  maltashop.events (topic) --routing key--> work queues
  each work queue has a dead-letter exchange (maltashop.dlx) and a <queue>.dlq
"""
import json
import sys

from maltashop_common.events import ROUTING_KEYS, EventType

EXCHANGE = "maltashop.events"
DLX = "maltashop.dlx"

Q_NOTIFICATION = "notification.order_events"
Q_INVENTORY = "inventory.commands"
Q_PAYMENT = "payment.commands"
Q_ORDER_REPLIES = "order.saga_replies"

K = ROUTING_KEYS
QUEUE_BINDINGS: dict[str, list[str]] = {
    Q_NOTIFICATION: [K[EventType.ORDER_CREATED], K[EventType.ORDER_CONFIRMED], K[EventType.ORDER_CANCELLED]],
    Q_INVENTORY: [K[EventType.RESERVE_INVENTORY], K[EventType.RELEASE_INVENTORY]],
    Q_PAYMENT: [K[EventType.PROCESS_PAYMENT], K[EventType.REFUND_PAYMENT]],
    Q_ORDER_REPLIES: [
        K[EventType.INVENTORY_RESERVED], K[EventType.INVENTORY_RESERVATION_FAILED],
        K[EventType.INVENTORY_RELEASED], K[EventType.PAYMENT_COMPLETED],
        K[EventType.PAYMENT_FAILED], K[EventType.PAYMENT_REFUNDED],
    ],
}


def dlq_name(queue: str) -> str:
    return f"{queue}.dlq"


def _queue_args(queue: str) -> dict:
    return {"x-dead-letter-exchange": DLX, "x-dead-letter-routing-key": queue}


def declare_topology(channel) -> None:
    """Idempotent: every service calls this on connect, so queues exist before any publish."""
    channel.exchange_declare(EXCHANGE, exchange_type="topic", durable=True)
    channel.exchange_declare(DLX, exchange_type="direct", durable=True)
    for queue, keys in QUEUE_BINDINGS.items():
        channel.queue_declare(queue, durable=True, arguments=_queue_args(queue))
        for key in keys:
            channel.queue_bind(queue, EXCHANGE, routing_key=key)
        channel.queue_declare(dlq_name(queue), durable=True)
        channel.queue_bind(dlq_name(queue), DLX, routing_key=queue)


def definitions(vhost: str = "/") -> dict:
    """RabbitMQ definitions.json equivalent of declare_topology()."""
    queues, bindings = [], []
    for queue, keys in QUEUE_BINDINGS.items():
        queues.append({"name": queue, "vhost": vhost, "durable": True, "auto_delete": False,
                       "arguments": _queue_args(queue)})
        queues.append({"name": dlq_name(queue), "vhost": vhost, "durable": True,
                       "auto_delete": False, "arguments": {}})
        for key in keys:
            bindings.append({"source": EXCHANGE, "vhost": vhost, "destination": queue,
                             "destination_type": "queue", "routing_key": key, "arguments": {}})
        bindings.append({"source": DLX, "vhost": vhost, "destination": dlq_name(queue),
                         "destination_type": "queue", "routing_key": queue, "arguments": {}})
    return {
        "vhosts": [{"name": vhost}],
        "exchanges": [
            {"name": EXCHANGE, "vhost": vhost, "type": "topic", "durable": True,
             "auto_delete": False, "internal": False, "arguments": {}},
            {"name": DLX, "vhost": vhost, "type": "direct", "durable": True,
             "auto_delete": False, "internal": False, "arguments": {}},
        ],
        "queues": queues,
        "bindings": bindings,
    }


if __name__ == "__main__":
    vh = sys.argv[1] if len(sys.argv) > 1 else "/"
    print(json.dumps(definitions(vh), indent=2))
