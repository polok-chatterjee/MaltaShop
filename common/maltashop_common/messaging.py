"""RabbitMQ publisher, consumer and the retry / dead-letter decision logic."""
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

import pika
import pika.exceptions
from pydantic import ValidationError

from maltashop_common.correlation import correlation_id_ctx
from maltashop_common.errors import PermanentError, PublishError
from maltashop_common.events import Event
from maltashop_common.topology import EXCHANGE, declare_topology

logger = logging.getLogger("maltashop.messaging")

ACK = "ack"
DEAD_LETTER = "dead_letter"

_BROKER_ERRORS = (pika.exceptions.AMQPError, OSError)


# --------------------------------------------------------------------------- publisher
class RabbitPublisher:
    """Publishes persistent messages and waits for the BROKER's confirm.

    The confirm means "RabbitMQ has accepted the message", not "a consumer has
    processed it". Used only from the outbox relay thread (pika connections are
    not thread-safe).
    """

    def __init__(self, url: str):
        self.url = url
        self._conn: pika.BlockingConnection | None = None
        self._ch = None

    def _ensure(self):
        if self._conn is None or self._conn.is_closed or self._ch is None or self._ch.is_closed:
            self._conn = pika.BlockingConnection(pika.URLParameters(self.url))
            self._ch = self._conn.channel()
            declare_topology(self._ch)       # queues must exist or messages would be dropped
            self._ch.confirm_delivery()
        return self._ch

    def publish_raw(self, routing_key: str, body: str | bytes, *, message_id: str,
                    correlation_id: str, event_type: str) -> None:
        if isinstance(body, str):
            body = body.encode()
        props = pika.BasicProperties(
            content_type="application/json", delivery_mode=2,   # 2 = persistent
            message_id=message_id, correlation_id=correlation_id, type=event_type,
            headers={"x-correlation-id": correlation_id},
        )
        try:
            self._ensure().basic_publish(EXCHANGE, routing_key, body, props, mandatory=True)
        except (pika.exceptions.UnroutableError, pika.exceptions.NackError) as exc:
            self.close()
            raise PublishError(f"broker rejected message: {exc!r}") from exc
        except _BROKER_ERRORS as exc:
            self.close()
            raise PublishError(f"broker unavailable: {exc!r}") from exc

    def publish(self, event: Event) -> None:
        self.publish_raw(event.routing_key, event.model_dump_json(), message_id=event.event_id,
                         correlation_id=event.correlation_id, event_type=event.event_type.value)

    def close(self) -> None:
        try:
            if self._conn is not None and self._conn.is_open:
                self._conn.close()
        except Exception:  # noqa: BLE001
            pass
        self._conn, self._ch = None, None


# --------------------------------------------------------------------------- processing logic
@dataclass
class Outcome:
    action: str            # ACK or DEAD_LETTER
    attempts: int
    reason: str | None = None


def process_message(body: bytes, handler: Callable[[Event], None], *, queue: str,
                    max_attempts: int = 3, backoff_seconds: float = 0.5,
                    sleep: Callable[[float], None] = time.sleep) -> Outcome:
    """Decide what happens to one delivered message. Independent of RabbitMQ so it is unit-testable.

    * malformed / schema-invalid          -> DEAD_LETTER immediately (cannot ever succeed)
    * handler raises PermanentError       -> DEAD_LETTER immediately
    * handler raises anything else        -> treated as transient: retry with exponential
                                             backoff up to max_attempts, then DEAD_LETTER
    * handler returns                     -> ACK (this includes "duplicate ignored")
    """
    try:
        event = Event.model_validate_json(body)
    except ValidationError as exc:
        logger.error("message rejected: invalid event schema", extra={
            "queue": queue, "outcome": DEAD_LETTER, "error_type": "ValidationError",
            "error": str(exc).replace("\n", " ")[:300]})
        return Outcome(DEAD_LETTER, 0, "invalid event schema")

    token = correlation_id_ctx.set(event.correlation_id)
    ctx = {"queue": queue, "event_id": event.event_id, "event_type": event.event_type.value,
           "order_id": event.order_id}
    try:
        for attempt in range(1, max_attempts + 1):
            try:
                handler(event)
                return Outcome(ACK, attempt)
            except PermanentError as exc:
                logger.error("permanent failure, dead-lettering message", extra={
                    **ctx, "attempt": attempt, "outcome": DEAD_LETTER,
                    "error_type": type(exc).__name__, "error": str(exc)})
                return Outcome(DEAD_LETTER, attempt, str(exc))
            except Exception as exc:  # noqa: BLE001  - anything unexpected is retried
                if attempt >= max_attempts:
                    logger.error("retries exhausted, dead-lettering message", extra={
                        **ctx, "attempt": attempt, "max_attempts": max_attempts,
                        "outcome": DEAD_LETTER, "error_type": type(exc).__name__, "error": str(exc)})
                    return Outcome(DEAD_LETTER, attempt, f"retries exhausted: {exc}")
                delay = backoff_seconds * (2 ** (attempt - 1))
                logger.warning("transient failure, will retry", extra={
                    **ctx, "attempt": attempt, "max_attempts": max_attempts,
                    "retry_in_seconds": delay, "error_type": type(exc).__name__, "error": str(exc)})
                sleep(delay)
    finally:
        correlation_id_ctx.reset(token)
    raise AssertionError("unreachable")


# --------------------------------------------------------------------------- consumer
class RabbitConsumer:
    """Background thread: consume one queue with manual acknowledgement and auto-reconnect."""

    def __init__(self, url: str, queue: str, handler: Callable[[Event], None], *,
                 max_attempts: int = 3, backoff_seconds: float = 0.5, reconnect_seconds: float = 2.0):
        self.url, self.queue, self.handler = url, queue, handler
        self.max_attempts, self.backoff_seconds = max_attempts, backoff_seconds
        self.reconnect_seconds = reconnect_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"consumer-{queue}", daemon=True)
        self._conn: pika.BlockingConnection | None = None
        self._ch = None
        self.connected = threading.Event()

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        try:
            if self._conn is not None and self._conn.is_open:
                self._conn.add_callback_threadsafe(lambda: self._ch.stop_consuming())
        except Exception:  # noqa: BLE001
            pass
        self._thread.join(timeout)

    def _on_message(self, ch, method, props, body):
        outcome = process_message(body, self.handler, queue=self.queue,
                                  max_attempts=self.max_attempts, backoff_seconds=self.backoff_seconds)
        if outcome.action == ACK:
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            # requeue=False -> broker routes the message to the queue's dead-letter exchange
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._conn = pika.BlockingConnection(pika.URLParameters(self.url))
                self._ch = self._conn.channel()
                declare_topology(self._ch)
                self._ch.basic_qos(prefetch_count=1)
                self._ch.basic_consume(self.queue, on_message_callback=self._on_message, auto_ack=False)
                logger.info("consumer started", extra={"queue": self.queue})
                self.connected.set()
                self._ch.start_consuming()
            except _BROKER_ERRORS as exc:
                self.connected.clear()
                if self._stop.is_set():
                    break
                logger.error("consumer lost broker connection, reconnecting", extra={
                    "queue": self.queue, "error_type": type(exc).__name__, "error": str(exc)[:200]})
                self._stop.wait(self.reconnect_seconds)
            except Exception:  # noqa: BLE001
                logger.exception("consumer crashed unexpectedly", extra={"queue": self.queue})
                self._stop.wait(self.reconnect_seconds)
            finally:
                try:
                    if self._conn is not None and self._conn.is_open:
                        self._conn.close()
                except Exception:  # noqa: BLE001
                    pass
        logger.info("consumer stopped", extra={"queue": self.queue})
