import pytest

from app.notifier import DeliveryError
from app.routes import get_notifier
from app.main import app

BODY = {"order_id": "ORD-ABC123", "customer_id": "C001"}


class FailingNotifier:
    def send(self, channel, customer_id, message):
        raise DeliveryError("SMTP unreachable")


def test_create_notification_marks_sent(client):
    r = client.post("/notifications", json=BODY, headers={"X-Correlation-ID": "corr-9"})
    assert r.status_code == 201
    b = r.json()
    assert b["notification_id"].startswith("NTF-")
    assert b["status"] == "SENT"
    assert b["attempts"] == 1
    assert b["channel"] == "EMAIL"
    assert b["failure_reason"] is None
    assert b["correlation_id"] == "corr-9"
    assert "ORD-ABC123" in b["message"]
    assert r.headers["X-Correlation-ID"] == "corr-9"


def test_failed_delivery_is_recorded(client):
    app.dependency_overrides[get_notifier] = lambda: FailingNotifier()
    r = client.post("/notifications", json=BODY)
    assert r.status_code == 201  # the notification record was created
    b = r.json()
    assert b["status"] == "FAILED"
    assert b["failure_reason"] == "SMTP unreachable"
    assert b["attempts"] == 1
    # persisted status matches
    assert client.get(f"/notifications/{b['notification_id']}").json()["status"] == "FAILED"


def test_get_notification(client):
    created = client.post("/notifications", json=BODY).json()
    r = client.get(f"/notifications/{created['notification_id']}")
    assert r.status_code == 200
    assert r.json() == created


def test_get_unknown_notification_404(client):
    r = client.get("/notifications/NTF-NOPE")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_list_filtered_by_order(client):
    client.post("/notifications", json=BODY)
    client.post("/notifications", json=BODY)
    client.post("/notifications", json={**BODY, "order_id": "ORD-OTHER"})
    assert len(client.get("/notifications").json()) == 3
    assert len(client.get("/notifications?order_id=ORD-ABC123").json()) == 2
    assert client.get("/notifications?order_id=none").json() == []


def test_custom_channel_and_message(client):
    r = client.post("/notifications", json={**BODY, "channel": "SMS", "message": "Hi"})
    assert r.json()["channel"] == "SMS"
    assert r.json()["message"] == "Hi"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"order_id": "ORD-1"},
        {"customer_id": "C001"},
        {"order_id": "", "customer_id": "C001"},
        {"order_id": "ORD-1", "customer_id": "C001", "channel": "PIGEON"},
        {"order_id": "ORD-1", "customer_id": "C001", "message": ""},
    ],
)
def test_invalid_payload_422(client, payload):
    r = client.post("/notifications", json=payload)
    assert r.status_code == 422
    assert r.json()["error"] == "validation_error"


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "notification-service", "database": "ok"}


def test_unexpected_error_returns_500(client, monkeypatch):
    from app.services import NotificationService

    monkeypatch.setattr(NotificationService, "get", lambda self, i: (_ for _ in ()).throw(RuntimeError("x")))
    r = client.get("/notifications/NTF-1", headers={"X-Correlation-ID": "t5"})
    assert r.status_code == 500
    assert r.json()["correlation_id"] == "t5"
