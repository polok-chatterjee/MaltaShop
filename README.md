# MaltaShop Microservices Prototype: Stage 2 (Asynchronous Events and Saga)

Stage 1 (two REST services with separate databases) is **preserved**. Stage 2 adds RabbitMQ messaging,
reliability mechanisms and an orchestrated saga. **Docker and hosting are not part of this stage.**

| Service | Port | Database (own) | Role |
|---|---|---|---|
| Order Service | 8001 | `order_service.db` | REST API, outbox, **saga orchestrator** |
| Notification Service | 8002 | `notification_service.db` | Consumes order events, stores notifications |
| Inventory Service | 8003 | `inventory_service.db` | **Simulated** stock: reserve / release |
| Payment Service | 8004 | `payment_service.db` | **Simulated** payments: process / refund |
| `common/` (library) | n/a | none | Event schema, topology, publisher/consumer, outbox helpers |

> **Simulation notice.** Inventory and Payment are lightweight simulations to exercise the saga. **No real
> payment is processed, no card data exists, and no real stock is managed.** Notification delivery (email/SMS) is also simulated.

![Event flow](docs/sequence_event_flow.png)
![Saga](docs/sequence_saga.png)

(Mermaid sources: `docs/sequence_diagrams.md`.)

---------------------------------------------------------------------------------------------------

## 1. Setup (Ubuntu)

```bash
# RabbitMQ (once)
sudo apt install rabbitmq-server
sudo systemctl enable --now rabbitmq-server
cd maltashop
./scripts/setup_rabbitmq.sh            # creates vhosts 'maltashop' and 'maltashop_test' (needs sudo)

# Python (once)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt     # also installs the shared ./common library
```
Optional management UI: `sudo rabbitmq-plugins enable rabbitmq_management`, then http://localhost:15672 (guest/guest, vhost `maltashop`).

If you still have Stage 1 `.db` files they keep working: new tables are added automatically (older orders simply have no saga).

## 2. Run

```bash
./scripts/start_all.sh        # starts all four services; logs in logs/<service>.log
./scripts/stop_all.sh
./scripts/reset_demo_data.sh  # stop, delete demo DBs/logs, empty the queues
```
Or manually, one terminal each: `cd <service_dir> && source ../.venv/bin/activate && uvicorn app.main:app --port 800N`.

Exchanges and queues are declared automatically by every service on start-up (`common/maltashop_common/topology.py`).

## 3. Configuration (environment variables; examples in each `<service>/.env.example`)

| Variable | Default | Services | Meaning |
|---|---|---|---|
| `MESSAGING_ENABLED` | `true` | all | `false` disables all RabbitMQ threads (used by unit tests) |
| `RABBITMQ_URL` | `amqp://guest:guest@localhost:5672/maltashop` | all | Broker and vhost |
| `OUTBOX_POLL_INTERVAL` | `0.5` | order, inventory, payment | Seconds between outbox polls (relay is also woken immediately by the API) |
| `CONSUMER_MAX_ATTEMPTS` | `3` | all | Handling attempts before dead-lettering |
| `CONSUMER_RETRY_BACKOFF_SECONDS` | `0.5` | all | First retry delay; doubles each attempt (0.5 s, 1 s, ...) |
| `INVENTORY_SEED` | `P001:100,P002:5,P003:0` | inventory | Initial simulated stock |
| `PAYMENT_DECLINE_CUSTOMERS` | `C-DECLINE` | payment | Customers whose simulated payment is declined |
| `SIMULATE_TRANSIENT_FAILURES` | `0` | notification | **Demo knob**: first N handling attempts fail transiently |

## 4. API changes (additive; Stage 1 behaviour unchanged)

* `POST /orders`, `GET /orders/{id}`: response gains `saga_state` and `failure_reason`.
* **New** `POST /orders/{order_id}/cancel` -> `202 Accepted` (only for `CONFIRMED` orders, else `409`); compensation completes asynchronously.
* `GET /health` on every service additionally reports `messaging.consumer_connected` (Order also `outbox_pending`).
* Inventory: `GET /stock`, `GET /reservations/{order_id}`. Payment: `GET /payments/{order_id}`. (Read-only, for demonstration.)

## 5. Event schema

One envelope for every message (events, commands, replies). Full JSON Schema: `docs/event_schema.json`; real captured examples: `docs/event_examples.json`.

```json
{
  "event_id": "699a4757-dafc-406e-915c-bdafe018c3e3",
  "event_type": "OrderCreated",
  "schema_version": 1,
  "order_id": "ORD-32E1E9723F1B",
  "customer_id": "C001",
  "correlation_id": "demo-outage-27840",
  "timestamp": "2026-11-17T19:48:18.234444Z",
  "payload": { "items": [ { "product_id": "P001", "quantity": 1 } ] }
}
```
`event_id` (unique, idempotency key), `event_type`, `order_id`, `customer_id`, `correlation_id`, `timestamp` (UTC) are always present. `payload` carries type-specific data. Unknown fields are rejected (`extra="forbid"`), so malformed messages are dead-lettered rather than half-processed.

| Kind | Types (routing key) |
|---|---|
| Domain events | OrderCreated `order.created`, OrderConfirmed `order.confirmed`, OrderCancelled `order.cancelled` |
| Saga commands | ReserveInventory `inventory.reserve`, ReleaseInventory `inventory.release`, ProcessPayment `payment.process`, RefundPayment `payment.refund` |
| Saga replies | InventoryReserved, InventoryReservationFailed, InventoryReleased, PaymentCompleted, PaymentFailed, PaymentRefunded (`inventory.*` / `payment.*`) |

## 6. RabbitMQ configuration

Exchange `maltashop.events` (topic, durable); dead-letter exchange `maltashop.dlx` (direct). Every work queue is durable, has `x-dead-letter-exchange=maltashop.dlx` and a companion `<queue>.dlq`.

| Queue | Bound routing keys | Consumer |
|---|---|---|
| `notification.order_events` | `order.created`, `order.confirmed`, `order.cancelled` | Notification |
| `inventory.commands` | `inventory.reserve`, `inventory.release` | Inventory |
| `payment.commands` | `payment.process`, `payment.refund` | Payment |
| `order.saga_replies` | `inventory.reserved/reservation_failed/released`, `payment.completed/failed/refunded` | Order (orchestrator) |

Messages are persistent (`delivery_mode=2`), published with publisher confirms and `mandatory=True`, and consumed with manual acknowledgement and `prefetch=1`.
`rabbitmq/definitions.json` is the same topology as an importable definitions file (generated from code; verified by importing it into a scratch vhost: 8 queues, 17 bindings): `rabbitmqctl import_definitions rabbitmq/definitions.json`.

## 7. Reliability mechanisms

| Mechanism | How it is implemented |
|---|---|
| **No waiting on the consumer** | `POST /orders` writes the order, saga row and outbox rows in **one DB transaction**, then returns. A relay thread publishes outbox rows afterwards, so the API depends on neither the broker nor any consumer (proved by test and demo with the Notification Service stopped). |
| **Message acknowledgement** | Consumers use manual ack: `basic_ack` only after the handler succeeded; success includes "duplicate ignored". A crash before ack means RabbitMQ redelivers (at-least-once). |
| **Idempotent consumption** | See below. |
| **Retry (transient failures)** | Any non-permanent exception is retried in-process up to `CONSUMER_MAX_ATTEMPTS` with exponential backoff (0.5 s, 1 s ...); each attempt logs a WARNING with `attempt` and `retry_in_seconds`. |
| **Dead-lettering** | Malformed/schema-invalid messages and `PermanentError`s are rejected at once; exhausted retries are rejected too. `basic_nack(requeue=False)` makes the broker route the message to `<queue>.dlq` for inspection. |
| **Structured error logging** | JSON logs with `event_id`, `event_type`, `order_id`, `queue`, `attempt`, `error_type`, `error`, `outcome`, and the `correlation_id`. |
| **Correlation ID propagation** | HTTP header `X-Correlation-ID` (or generated) -> stored on order -> copied into every event/command/reply -> set as the logging context in each consumer and in the relay -> stored on notifications/payments/reservations. Also sent as the AMQP `correlation_id` property. |
| **Broker outage** | Outbox rows stay unpublished (`attempts`, `last_error` recorded) and are published in order when the broker returns. |

### How duplicate event delivery is handled

Duplicates are *expected* with at-least-once delivery: a consumer can crash after processing but before the ack; a
network fault can hide an ack; the relay can crash after publishing but before marking the row published.
Each consumer therefore keeps a `processed_events` table (primary key `event_id`) **in its own database** and, inside the
**same transaction** as the business effect, inserts the `event_id`. If the id already exists the event is logged
("duplicate event ignored"), acknowledged, and has no further effect. If processing fails and rolls back, the id is rolled back too, so the retry is *not* mistaken for a duplicate.
Additional safety nets: one payment per order, one active reservation per order, release/refund are safe to repeat, and the orchestrator ignores replies that do not fit the saga's current state.

## 8. The saga

**Orchestration.** The Order Service is the single coordinator. It stores the saga state (`order_sagas`) and sends *commands*; Inventory and Payment only reply with *events* and never call each other. All saga messages go through the outbox, so a state change and its next command cannot be separated by a crash.

| Saga state | Meaning |
|---|---|
| `STARTED` | ReserveInventory sent |
| `INVENTORY_RESERVED` | ProcessPayment sent |
| `COMPLETED` | Order `CONFIRMED` |
| `COMPENSATING` | Order `CANCELLED`; waiting for undo steps to be confirmed |
| `CANCELLED` | Final; all required undo steps confirmed |

**Compensating transactions.** A step that already committed in another service cannot be rolled back, so it is undone by a *new* business action:

| Failure | Compensation |
|---|---|
| Inventory reservation fails | Order cancelled (nothing was reserved, so nothing to undo; payment never attempted) |
| Payment fails | `ReleaseInventory`; order cancelled; saga ends `CANCELLED` after `InventoryReleased` |
| Order cancelled after successful payment | `RefundPayment` **and** `ReleaseInventory`; saga ends `CANCELLED` after both replies |

**Eventual consistency.** Between the API response and the final reply the system is deliberately inconsistent: the order is `PENDING` while stock may already be reserved. Clients observe progress through `saga_state`. In the captured run the order went `PENDING` -> `CONFIRMED` in about one second (dominated by the 0.5 s outbox polling in each service).

**Why not a distributed database transaction?** A two-phase commit across the four databases and the broker would need XA-capable resources (SQLite and RabbitMQ are not), would hold locks in every service until the slowest participant answers, makes the coordinator a single point of failure, and would stop all services from working whenever one is down. That removes the autonomy that motivated microservices. The saga gives up atomic isolation in exchange for availability, independent deployment and failure isolation, with compensations restoring business consistency.

## 9. Tests

| Suite | Command | Tests | What it proves |
|---|---|---|---|
| `common` | `cd common && pytest` | 27 | Event schema, `process_message` retry/DLQ logic, outbox, idempotency table; **6 integration tests on a real RabbitMQ** (publish confirm, consume + ack, unroutable, DLQ, retry, message waits for a late consumer) |
| `order_service` | `cd order_service && pytest` | 36 | Stage 1 tests (21, unchanged) + outbox publication, correlation propagation, saga happy path, all compensations, cancel rules, duplicate/out-of-order replies, rollback of idempotency marker |
| `notification_service` | `cd notification_service && pytest` | 26 | Stage 1 (14) + consumption, correlation in JSON logs, duplicates, retry/backoff, exhaustion, delivery failure, unsupported type |
| `inventory_service` | `cd inventory_service && pytest` | 10 | All-or-nothing reserve, release, idempotency |
| `payment_service` | `cd payment_service && pytest` | 8 | Complete/decline, refund, never charging twice |
| `tests_e2e` | `pytest tests_e2e` | 9 | **Real processes + real RabbitMQ**: async delivery while Notification is down, happy path, same correlation ID in all four service logs, the three compensations, duplicates, DLQ, retry with backoff, retries exhausted |

Run everything: `./run_tests.sh` (add `-v` for names). Last run: **116 passed, 0 skipped, 0 failed** (broker running); full verbose log in `docs/example_logs/test_run_output.txt`.
Integration/e2e suites **skip with a message** if RabbitMQ is unreachable, so a green run without a broker is *not* full verification.

## 10. How to demonstrate it (about 10 minutes)

```bash
./scripts/reset_demo_data.sh && ./scripts/start_all.sh
```

**a) Fast path: everything scripted** (happy path, 3 compensations, duplicates, dead-letter):
`python tools/demo_session.py` (captured output: `docs/example_logs/demo_session_output.txt`)

**b) Manual walk-through**

1. *Happy path + correlation ID*
   ```bash
   curl -s -X POST localhost:8001/orders -H 'Content-Type: application/json' -H 'X-Correlation-ID: demo-1' \
        -d '{"customer_id":"C001","items":[{"product_id":"P001","quantity":2}]}'
   # returns immediately: status PENDING, saga_state STARTED
   sleep 2; curl -s localhost:8001/orders/<ORDER_ID>          # CONFIRMED / COMPLETED
   python tools/trace.py demo-1                               # one correlation ID across all 4 services
   curl -s "localhost:8002/notifications?order_id=<ORDER_ID>" # 2 notifications (received, confirmed)
   ```
2. *Asynchrony (order accepted while Notification Service is down)*: `./scripts/demo_notification_outage.sh`
3. *Compensation 1, inventory fails*: `curl -s -X POST localhost:8001/orders -H 'Content-Type: application/json' -d '{"customer_id":"C001","items":[{"product_id":"P003","quantity":1}]}'` -> `CANCELLED`, reason "insufficient stock"; `curl localhost:8004/payments/<ID>` -> 404 (payment never attempted).
4. *Compensation 2, payment fails*: same with `"customer_id":"C-DECLINE"`, product `P002`: order `CANCELLED`; `curl localhost:8003/stock` shows P002 unchanged (reserved then released); `curl localhost:8003/reservations/<ID>` -> `RELEASED`.
5. *Compensation 3, refund*: place a normal order, wait for `CONFIRMED`, then `curl -X POST localhost:8001/orders/<ID>/cancel` (202) -> saga `CANCELLED`; `curl localhost:8004/payments/<ID>` -> `REFUNDED`; stock restored.
6. *Duplicates*: `python tools/demo.py duplicate` then run the `curl` it prints: exactly 1 notification, 2 "duplicate event ignored" log lines.
7. *Dead letter*: `python tools/demo.py poison; sleep 2; python tools/demo.py dlq` -> `notification.order_events.dlq 1`; Notification Service still healthy.
8. *Retry*: `./scripts/demo_retry.sh` (restarts Notification Service so its first 2 attempts fail; log shows attempt 1, 2 then success).

## 11. Example logs (real captures, in `docs/example_logs/`)

| File | Shows |
|---|---|
| `correlation_id_trace_happy_path.txt` | One correlation ID across order, inventory, payment and notification services, in time order |
| `raw_json_logs_same_correlation_id.jsonl` | The raw structured JSON log lines from Order **and** Notification service for one order |
| `demo_session_output.txt` | Scripted run: happy path, three compensations, duplicates, dead-letter |
| `demo_notification_outage_output.txt` | Order accepted in ~19 ms while Notification Service was stopped; event waited in the queue; consumed after restart |
| `demo_retry_output.txt` | Two transient failures (0.5 s, 1.0 s backoff), then success |
| `test_run_output.txt` | Verbose output of all 116 tests |

Note: "event published to broker" is logged *after* the broker confirms, so in a merged trace the consumer's line can appear a few milliseconds before the publisher's.

## 12. Limitations (not done in this stage)

* Inventory, Payment and email/SMS are **simulations**; no real provider, authentication or stock rules.
* No saga timeouts or stuck-saga recovery: if a participant never replies the order stays `PENDING`. Cancelling an order that is still `PENDING` is not supported (`409`).
* A `PermanentError` that is dead-lettered (e.g. refund of an unknown payment) leaves its saga unfinished; there is no DLQ replay tool (use the RabbitMQ management UI).
* Retries run in the consumer thread with `sleep`, so a failing message briefly blocks that queue (acceptable at this scale). Consumers are threads inside each service process.
* Single RabbitMQ node, default `guest` credentials, no TLS or clustering. SQLite, no migrations (new tables are created on start-up; changed columns are not).
* `extra="forbid"` makes schema evolution a coordinated change (bump `schema_version`).
* A notification's *delivery* failure is recorded as `FAILED` and acknowledged (not retried); only infrastructure failures are retried.
* The code uses pika's `BlockingConnection`, which pika 1.4 flags as deprecated for removal in 2.0; `pika` is therefore pinned to `<2` and migrating to its newer connection API is future work.
* Docker, hosting and CI/CD are future stages.
