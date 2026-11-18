"""Transactional outbox + idempotency helpers.

Why an outbox?  A service must change its database AND publish a message.
Doing both directly is a "dual write": a crash between them loses the message
(or publishes one for a change that was rolled back). Instead the service
writes the message to an `outbox` table IN THE SAME DATABASE TRANSACTION as
its state change; a relay thread publishes it afterwards. The API therefore
never waits for (or depends on) the broker.
"""
import logging
import threading
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import Column, DateTime, Integer, String, Text, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from maltashop_common.correlation import correlation_id_ctx
from maltashop_common.errors import PublishError
from maltashop_common.events import Event

logger = logging.getLogger("maltashop.outbox")


def _utcnow():
    return datetime.now(timezone.utc)


def make_outbox_model(Base):
    class Outbox(Base):
        __tablename__ = "outbox"
        id = Column(Integer, primary_key=True, autoincrement=True)
        event_id = Column(String(64), unique=True, nullable=False)
        event_type = Column(String(64), nullable=False)
        routing_key = Column(String(64), nullable=False)
        body = Column(Text, nullable=False)
        correlation_id = Column(String(64), nullable=False)
        created_at = Column(DateTime(timezone=True), default=_utcnow)
        published_at = Column(DateTime(timezone=True), nullable=True)
        attempts = Column(Integer, default=0)
        last_error = Column(Text, nullable=True)
    return Outbox


def make_processed_event_model(Base):
    class ProcessedEvent(Base):
        """One row per handled event_id. The primary key makes duplicates impossible."""
        __tablename__ = "processed_events"
        event_id = Column(String(64), primary_key=True)
        event_type = Column(String(64), nullable=False)
        processed_at = Column(DateTime(timezone=True), default=_utcnow)
    return ProcessedEvent


def add_to_outbox(db: Session, Outbox, event: Event) -> None:
    """Stage an event in the caller's transaction (the caller commits)."""
    db.add(Outbox(event_id=event.event_id, event_type=event.event_type.value,
                  routing_key=event.routing_key, body=event.model_dump_json(),
                  correlation_id=event.correlation_id))


def mark_processed(db: Session, ProcessedEvent, event: Event) -> bool:
    """Record event_id. Returns False if it was already processed (duplicate delivery).

    Call this in the SAME transaction as the business effect, so "effect applied"
    and "event remembered" succeed or fail together.
    """
    if db.get(ProcessedEvent, event.event_id) is not None:
        return False
    db.add(ProcessedEvent(event_id=event.event_id, event_type=event.event_type.value))
    try:
        db.flush()
    except IntegrityError:        # a concurrent delivery won the race
        db.rollback()
        return False
    return True


def pending_count(session_factory, Outbox) -> int:
    with session_factory() as db:
        return db.scalar(select(func.count()).select_from(Outbox).where(Outbox.published_at.is_(None))) or 0


class OutboxRelay:
    """Background thread that publishes unpublished outbox rows, oldest first."""

    def __init__(self, session_factory: Callable[[], Session], Outbox, publisher, *,
                 poll_interval: float = 0.5, batch_size: int = 50, failure_backoff: float = 2.0):
        self.session_factory, self.Outbox, self.publisher = session_factory, Outbox, publisher
        self.poll_interval, self.batch_size, self.failure_backoff = poll_interval, batch_size, failure_backoff
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="outbox-relay", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self, timeout: float = 5.0):
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout)
        close = getattr(self.publisher, "close", None)
        if close:
            close()

    def wake(self):
        """Non-blocking nudge so a new row is published promptly. Never waits for the broker."""
        self._wake.set()

    def run_once(self) -> int:
        """Publish pending rows in order; stop at the first failure to preserve ordering."""
        published = 0
        O = self.Outbox
        with self.session_factory() as db:
            rows = db.scalars(select(O).where(O.published_at.is_(None)).order_by(O.id).limit(self.batch_size)).all()
            for row in rows:
                token = correlation_id_ctx.set(row.correlation_id)
                try:
                    self.publisher.publish_raw(row.routing_key, row.body, message_id=row.event_id,
                                               correlation_id=row.correlation_id, event_type=row.event_type)
                except PublishError as exc:
                    row.attempts = (row.attempts or 0) + 1
                    row.last_error = str(exc)[:500]
                    db.commit()
                    logger.error("event publish failed, will retry", extra={
                        "event_id": row.event_id, "event_type": row.event_type,
                        "routing_key": row.routing_key, "attempts": row.attempts, "error": str(exc)[:200]})
                    raise
                else:
                    row.attempts = (row.attempts or 0) + 1
                    row.published_at = _utcnow()
                    db.commit()
                    published += 1
                    logger.info("event published to broker", extra={
                        "event_id": row.event_id, "event_type": row.event_type,
                        "routing_key": row.routing_key})
                finally:
                    correlation_id_ctx.reset(token)
        return published

    def _run(self):
        while not self._stop.is_set():
            delay = self.poll_interval
            try:
                self.run_once()
            except PublishError:
                delay = self.failure_backoff
            except Exception:  # noqa: BLE001
                logger.exception("outbox relay error")
                delay = self.failure_backoff
            self._wake.wait(delay)
            self._wake.clear()
