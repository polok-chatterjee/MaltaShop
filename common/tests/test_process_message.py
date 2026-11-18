import pytest

from maltashop_common.correlation import correlation_id_ctx
from maltashop_common.errors import PermanentError
from maltashop_common.events import EventType, make_event
from maltashop_common.messaging import ACK, DEAD_LETTER, process_message


def body(**kw):
    return make_event(EventType.ORDER_CREATED, order_id="ORD-1", customer_id="C1",
                      correlation_id=kw.get("corr", "corr-1")).model_dump_json().encode()


def run(handler, payload=None, max_attempts=3):
    sleeps = []
    out = process_message(payload or body(), handler, queue="q", max_attempts=max_attempts,
                          backoff_seconds=0.5, sleep=sleeps.append)
    return out, sleeps


def test_success_is_acked_once():
    calls = []
    out, sleeps = run(calls.append)
    assert out.action == ACK and out.attempts == 1 and len(calls) == 1 and sleeps == []


def test_transient_failures_are_retried_with_exponential_backoff_then_succeed():
    state = {"n": 0}

    def flaky(event):
        state["n"] += 1
        if state["n"] < 3:
            raise ConnectionError("db busy")

    out, sleeps = run(flaky)
    assert out.action == ACK and out.attempts == 3
    assert sleeps == [0.5, 1.0]                                # backoff doubles


def test_retries_exhausted_goes_to_dead_letter():
    def always(event):
        raise ConnectionError("still down")

    out, sleeps = run(always, max_attempts=3)
    assert out.action == DEAD_LETTER and out.attempts == 3 and sleeps == [0.5, 1.0]
    assert "retries exhausted" in out.reason


def test_permanent_error_is_not_retried():
    def bad(event):
        raise PermanentError("cannot refund unknown payment")

    out, sleeps = run(bad)
    assert out.action == DEAD_LETTER and out.attempts == 1 and sleeps == []


@pytest.mark.parametrize("junk", [b"not json at all", b"{}", b'{"event_type": "OrderCreated"}'])
def test_malformed_or_schema_invalid_goes_straight_to_dead_letter(junk):
    called = []
    out, _ = run(called.append, payload=junk)
    assert out.action == DEAD_LETTER and out.attempts == 0 and called == []


def test_correlation_id_is_set_while_handling_and_reset_afterwards(caplog):
    seen = []
    assert correlation_id_ctx.get() == "-"
    run(lambda e: seen.append(correlation_id_ctx.get()), payload=body(corr="trace-42"))
    assert seen == ["trace-42"]
    assert correlation_id_ctx.get() == "-"


def test_retry_and_dead_letter_are_logged_with_structure(caplog):
    caplog.set_level("INFO", logger="maltashop.messaging")

    def always(event):
        raise ConnectionError("down")

    run(always, max_attempts=2)
    warn = [r for r in caplog.records if r.levelname == "WARNING"][0]
    err = [r for r in caplog.records if r.levelname == "ERROR"][0]
    assert warn.attempt == 1 and warn.order_id == "ORD-1" and warn.error_type == "ConnectionError"
    assert err.outcome == "dead_letter" and err.max_attempts == 2
