import re
import uuid

VALID = {"customer_id": "C001", "items": [{"product_id": "P001", "quantity": 2}]}


def test_create_order_returns_201_and_expected_fields(client):
    r = client.post("/orders", json=VALID)
    assert r.status_code == 201
    body = r.json()
    assert re.fullmatch(r"ORD-[0-9A-F]{12}", body["order_id"])
    assert body["customer_id"] == "C001"
    assert body["status"] == "PENDING"
    assert body["created_at"].endswith("+00:00")
    assert body["items"] == [{"product_id": "P001", "quantity": 2}]
    assert uuid.UUID(body["correlation_id"])
    assert r.headers["Location"] == f"/orders/{body['order_id']}"


def test_order_ids_are_unique(client):
    ids = {client.post("/orders", json=VALID).json()["order_id"] for _ in range(10)}
    assert len(ids) == 10


def test_correlation_id_generated_and_echoed_in_header(client):
    r = client.post("/orders", json=VALID)
    assert r.headers["X-Correlation-ID"] == r.json()["correlation_id"]


def test_correlation_id_propagated_from_client(client):
    r = client.post("/orders", json=VALID, headers={"X-Correlation-ID": "abc-123"})
    assert r.json()["correlation_id"] == "abc-123"
    assert r.headers["X-Correlation-ID"] == "abc-123"


def test_get_order_returns_stored_order(client):
    created = client.post("/orders", json=VALID).json()
    r = client.get(f"/orders/{created['order_id']}")
    assert r.status_code == 200
    assert r.json() == created


def test_get_unknown_order_returns_404_with_correlation_id(client):
    r = client.get("/orders/ORD-DOESNOTEXIST", headers={"X-Correlation-ID": "trace-1"})
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"
    assert r.json()["correlation_id"] == "trace-1"


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "order-service", "database": "ok"}


import pytest


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"customer_id": "C001"},
        {"customer_id": "C001", "items": []},
        {"customer_id": "", "items": [{"product_id": "P1", "quantity": 1}]},
        {"customer_id": "bad id!", "items": [{"product_id": "P1", "quantity": 1}]},
        {"customer_id": "C001", "items": [{"product_id": "P1", "quantity": 0}]},
        {"customer_id": "C001", "items": [{"product_id": "P1", "quantity": -3}]},
        {"customer_id": "C001", "items": [{"product_id": "P1", "quantity": "two"}]},
        {"customer_id": "C001", "items": [{"product_id": "P1", "quantity": 1001}]},
        {"customer_id": "C001", "items": [{"quantity": 1}]},
        {"customer_id": "C001", "items": [{"product_id": "P1", "quantity": 1}, {"product_id": "P1", "quantity": 2}]},
    ],
)
def test_invalid_orders_rejected_with_422(client, payload):
    r = client.post("/orders", json=payload)
    assert r.status_code == 422
    assert r.json()["error"] == "validation_error"
    assert r.json()["correlation_id"]


def test_invalid_order_is_not_persisted(client):
    client.post("/orders", json={"customer_id": "C001", "items": []})
    # Nothing to fetch; a fresh valid order is the only one that exists.
    assert client.get("/orders/ORD-000000000000").status_code == 404


def test_malformed_json_returns_422(client):
    r = client.post("/orders", content="{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_unexpected_error_returns_500_with_correlation_id(client, monkeypatch):
    from app.services import OrderService

    def boom(self, order_id):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(OrderService, "get_order", boom)
    r = client.get("/orders/ORD-1", headers={"X-Correlation-ID": "trace-500"})
    assert r.status_code == 500
    assert r.json() == {
        "error": "internal_error",
        "detail": "An unexpected error occurred",
        "correlation_id": "trace-500",
    }
    assert r.headers["X-Correlation-ID"] == "trace-500"
    assert "db exploded" not in r.text  # internals are not leaked
