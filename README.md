# MaltaShop Microservices Prototype – Stage 1

Two independent microservices for an online shop:

| Service | Port | Owns | Responsibility |
|---|---|---|---|
| **Order Service** | 8001 | `order_service.db` (`orders`, `order_items`) | Accept, validate, store and return orders |
| **Notification Service** | 8002 | `notification_service.db` (`notifications`) | Record notifications and their delivery status |

Neither service imports the other's code or opens the other's database.
Communication between them is **not** implemented yet (RabbitMQ is Stage 2).

## Technology choices

| Choice | Why it fits this prototype |
|---|---|
| **Python 3.11+ / FastAPI** | Small, fast to write, automatic request validation (Pydantic) and OpenAPI docs at `/docs`. |
| **SQLAlchemy 2** | The database is chosen purely by the `DATABASE_URL` variable. |
| **SQLite** (default) | Zero installation; one file per service makes "database per service" visible and testable. Switching to PostgreSQL for the Docker stage means changing `DATABASE_URL` and adding `psycopg2-binary`; no code change. |
| **pydantic-settings** | Environment-based configuration (`.env` or real env vars). |
| **pytest + TestClient** | Fast API tests against an in-memory database. |
| RabbitMQ, Docker | Deliberately **not** included; planned for Stages 2 and 3. |

## Project structure

```
maltashop/
├── README.md
├── run_tests.sh
├── order_service/
│   ├── .env.example
│   ├── requirements.txt / requirements-dev.txt
│   ├── app/
│   │   ├── main.py           # app factory, middleware, startup
│   │   ├── routes.py         # HTTP layer only
│   │   ├── services.py       # business logic
│   │   ├── schemas.py        # request/response contracts + validation
│   │   ├── models.py         # SQLAlchemy models
│   │   ├── database.py       # engine/session
│   │   ├── config.py         # env-based settings
│   │   ├── exceptions.py     # domain errors + handlers
│   │   ├── middleware.py     # correlation ID + request logging
│   │   └── logging_config.py # JSON logging
│   └── tests/
└── notification_service/     # same layout, plus app/notifier.py
```

## Setup and run

Requires Python 3.11+.

```bash
cd maltashop
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r order_service/requirements-dev.txt

# Configuration (defaults also work without these files)
cp order_service/.env.example order_service/.env
cp notification_service/.env.example notification_service/.env
```

Start each service in its own terminal (the working directory matters: the `.env` file and the SQLite file are created there):

```bash
# Terminal 1
cd maltashop/order_service
source ../.venv/bin/activate
uvicorn app.main:app --port 8001

# Terminal 2
cd maltashop/notification_service
source ../.venv/bin/activate
uvicorn app.main:app --port 8002
```

Interactive docs: http://localhost:8001/docs and http://localhost:8002/docs

Run all tests:

```bash
cd maltashop && source .venv/bin/activate && ./run_tests.sh
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `SERVICE_NAME` | `order-service` / `notification-service` | Appears in logs and `/health` |
| `DATABASE_URL` | `sqlite:///./order_service.db` / `sqlite:///./notification_service.db` | SQLAlchemy URL; each service must use its own database |
| `LOG_LEVEL` | `INFO` | Python log level |
| `CORRELATION_ID_HEADER` | `X-Correlation-ID` | Header read from and written to responses |

## Correlation IDs

Every request gets a correlation ID. If the caller sends `X-Correlation-ID` it is reused (propagation); otherwise a UUID4 is generated. The ID is:
- stored with the order / notification,
- returned in the `X-Correlation-ID` response header and in every error body,
- attached to every JSON log line of that request.

Calling both services with the same header value lets you trace one customer action across both services' logs.

## API: Order Service (port 8001)

### `POST /orders` → 201 Created
Validation: `customer_id` and `product_id` are 1–64 chars of `A-Z a-z 0-9 _ -`; `items` has 1–100 entries; `quantity` is an integer 1–1000; duplicate `product_id`s are rejected. Returns `Location: /orders/{order_id}`.

| Status | Meaning |
|---|---|
| 201 | Order created (initial status `PENDING`) |
| 422 | Validation error |
| 500 | Unexpected error (no internals leaked) |

### `GET /orders/{order_id}` → 200 / 404
### `GET /health` → 200 `{"status":"ok",...}` / 503 if the database is unreachable

## API: Notification Service (port 8002)

### `POST /notifications` → 201 Created
Body: `order_id`, `customer_id` (required); `channel` (`EMAIL` default, or `SMS`); `message` (optional, a default is generated).
The record is saved as `PENDING`, delivery is attempted, then it becomes `SENT` or `FAILED` (with `failure_reason`). A failed delivery still returns 201, because the notification record was created; the failure is visible in `status`.
(The prototype uses a simulated notifier that logs and succeeds; real email/SMS is out of scope.)

### `GET /notifications/{notification_id}` → 200 / 404
### `GET /notifications?order_id=...` → 200 list, newest first
### `GET /health` → 200 / 503

## Example requests and responses

**Create an order**
```bash
curl -i -X POST http://localhost:8001/orders \
  -H 'Content-Type: application/json' \
  -d '{"customer_id":"C001","items":[{"product_id":"P001","quantity":2}]}'
```
```
HTTP/1.1 201 Created
location: /orders/ORD-5AEF96E7DB7E
x-correlation-id: b2099e66-62fe-4504-8e0d-3ea9daf9d534

{"order_id":"ORD-5AEF96E7DB7E","customer_id":"C001","status":"PENDING",
 "correlation_id":"b2099e66-62fe-4504-8e0d-3ea9daf9d534",
 "created_at":"2026-11-10T17:23:45.805676+00:00",
 "items":[{"product_id":"P001","quantity":2}]}
```

**Get an order** (supplying your own correlation ID)
```bash
curl -H 'X-Correlation-ID: demo-123' http://localhost:8001/orders/ORD-5AEF96E7DB7E
```

**Unknown order → 404**
```json
{"error":"not_found","detail":"Order 'ORD-NOPE' not found","correlation_id":"fd2aec4b-7d91-48c5-9ce1-033ea151d749"}
```

**Invalid order (quantity 0) → 422**
```json
{"error":"validation_error","detail":[{"field":"items.0.quantity","message":"Input should be greater than or equal to 1"}],"correlation_id":"780f2acb-890d-4194-b9e2-9e05f26170d5"}
```

**Create a notification**
```bash
curl -X POST http://localhost:8002/notifications \
  -H 'Content-Type: application/json' -H 'X-Correlation-ID: demo-123' \
  -d '{"order_id":"ORD-5AEF96E7DB7E","customer_id":"C001"}'
```
```json
{"notification_id":"NTF-5F52B2C1A28D","order_id":"ORD-5AEF96E7DB7E","customer_id":"C001",
 "channel":"EMAIL","message":"Your order ORD-5AEF96E7DB7E has been received.",
 "status":"SENT","failure_reason":null,"attempts":1,"correlation_id":"demo-123",
 "created_at":"2026-11-10T17:23:45.887981+00:00","updated_at":"2026-11-10T17:23:45.891607+00:00"}
```

**Health**
```json
{"status":"ok","service":"order-service","database":"ok"}
```

**Sample structured log line**
```json
{"timestamp":"2026-11-10T17:23:45.808787+00:00","level":"INFO","service":"order-service","logger":"app.orders","correlation_id":"b2099e66-62fe-4504-8e0d-3ea9daf9d534","message":"order created","order_id":"ORD-5AEF96E7DB7E","customer_id":"C001","item_count":1}
```

## Not yet implemented (later stages)
- Order Service does not yet trigger notifications; RabbitMQ events come in Stage 2.
- Docker / docker-compose and PostgreSQL; hosting.
- No authentication, product catalogue lookup, stock checks, or schema migrations (tables are created on startup).
