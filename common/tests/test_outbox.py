import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from maltashop_common.errors import PublishError
from maltashop_common.events import EventType, make_event
from maltashop_common.outbox import (OutboxRelay, add_to_outbox, make_outbox_model,
                                     make_processed_event_model, mark_processed, pending_count)


class Base(DeclarativeBase):
    pass


Outbox = make_outbox_model(Base)
Processed = make_processed_event_model(Base)


class FakePublisher:
    def __init__(self, fail_times=0):
        self.sent, self.fail_times = [], fail_times

    def publish_raw(self, routing_key, body, *, message_id, correlation_id, event_type):
        if self.fail_times:
            self.fail_times -= 1
            raise PublishError("broker down")
        self.sent.append((routing_key, message_id, correlation_id, event_type))


@pytest.fixture()
def Session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def ev(n, corr="c-1"):
    return make_event(EventType.ORDER_CREATED, order_id=f"ORD-{n}", customer_id="C", correlation_id=corr)


def stage(Session, *events):
    with Session() as db:
        for e in events:
            add_to_outbox(db, Outbox, e)
        db.commit()


def test_relay_publishes_pending_rows_in_order_and_marks_them(Session):
    a, b = ev(1, "corr-a"), ev(2, "corr-b")
    stage(Session, a, b)
    pub = FakePublisher()
    n = OutboxRelay(Session, Outbox, pub).run_once()
    assert n == 2 and [s[1] for s in pub.sent] == [a.event_id, b.event_id]
    assert pub.sent[0][2] == "corr-a"                              # correlation id travels with the message
    assert pending_count(Session, Outbox) == 0
    assert OutboxRelay(Session, Outbox, pub).run_once() == 0       # nothing is published twice


def test_broker_failure_keeps_event_pending_then_retry_succeeds(Session):
    stage(Session, ev(1))
    pub = FakePublisher(fail_times=1)
    relay = OutboxRelay(Session, Outbox, pub)
    with pytest.raises(PublishError):
        relay.run_once()
    assert pending_count(Session, Outbox) == 1
    with Session() as db:
        row = db.scalars(select(Outbox)).one()
        assert row.attempts == 1 and "broker down" in row.last_error and row.published_at is None
    assert relay.run_once() == 1 and pending_count(Session, Outbox) == 0


def test_failure_stops_batch_to_preserve_ordering(Session):
    stage(Session, ev(1), ev(2))
    pub = FakePublisher(fail_times=1)
    relay = OutboxRelay(Session, Outbox, pub)
    with pytest.raises(PublishError):
        relay.run_once()
    assert pub.sent == []                                          # event 2 did not jump the queue
    relay.run_once()
    assert [s[1] for s in pub.sent][0] == ev_ids(Session)[0]


def ev_ids(Session):
    with Session() as db:
        return [r.event_id for r in db.scalars(select(Outbox).order_by(Outbox.id))]


def test_outbox_rollback_means_no_event(Session):
    with Session() as db:
        add_to_outbox(db, Outbox, ev(1))
        db.rollback()                                              # business transaction failed
    assert pending_count(Session, Outbox) == 0


def test_mark_processed_detects_duplicates(Session):
    e = ev(1)
    with Session() as db:
        assert mark_processed(db, Processed, e) is True
        db.commit()
    with Session() as db:
        assert mark_processed(db, Processed, e) is False           # same event_id delivered again
        assert mark_processed(db, Processed, ev(2)) is True
