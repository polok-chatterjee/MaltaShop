#!/usr/bin/env python3
"""Scripted demonstration of the async flow, the saga and its compensations.

Requires the four services running (scripts/start_all.sh) and RabbitMQ. Safe to rerun.
    python tools/demo_session.py
"""
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
ORDER, NOTIF, INV, PAY = ("http://localhost:800%d" % i for i in (1, 2, 3, 4))
http = httpx.Client(timeout=5)
RUN = uuid.uuid4().hex[:4]


def title(t):
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


def wait(fn, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    raise SystemExit("timed out waiting for the system to settle")


def place(customer, product, qty, corr):
    t0 = time.perf_counter()
    r = http.post(f"{ORDER}/orders", headers={"X-Correlation-ID": corr},
                  json={"customer_id": customer, "items": [{"product_id": product, "quantity": qty}]})
    ms = (time.perf_counter() - t0) * 1000
    b = r.json()
    print(f"POST /orders -> {r.status_code} in {ms:.0f} ms | order={b['order_id']} status={b['status']} "
          f"saga_state={b['saga_state']} correlation_id={b['correlation_id']}")
    return b["order_id"]


def settle(oid, saga_state):
    o = wait(lambda: (x := http.get(f"{ORDER}/orders/{oid}").json())["saga_state"] == saga_state and x)
    print(f"GET /orders/{oid} -> status={o['status']} saga_state={o['saga_state']} failure_reason={o['failure_reason']}")
    return o


def trace(corr):
    time.sleep(0.8)
    print(f"\n--- log lines for correlation ID {corr} (all services, time-ordered) ---")
    subprocess.run([sys.executable, str(ROOT / "tools" / "trace.py"), corr])


def stock():
    return http.get(f"{INV}/stock").json()["stock"]


# ---------------------------------------------------------------------------------------------
title("A. HAPPY PATH: order -> inventory reserved -> payment -> order confirmed (+ notifications)")
c = f"demo-happy-{RUN}"
print("stock before:", stock())
oid = place("C001", "P001", 2, c)
settle(oid, "COMPLETED")
print("stock after: ", stock(), " (P001 reduced by 2)")
print("payment:     ", http.get(f"{PAY}/payments/{oid}").json())
wait(lambda: len(http.get(f"{NOTIF}/notifications", params={"order_id": oid}).json()) == 2)
for n in http.get(f"{NOTIF}/notifications", params={"order_id": oid}).json():
    print(f"notification: status={n['status']} correlation_id={n['correlation_id']} message={n['message']!r}")
trace(c)

title("B. COMPENSATION 1: inventory reservation fails -> order cancelled (payment never attempted)")
c = f"demo-nostock-{RUN}"
oid = place("C001", "P003", 1, c)                  # P003 has zero stock
settle(oid, "CANCELLED")
print("payment lookup:", http.get(f"{PAY}/payments/{oid}").status_code, "(404 = payment was never attempted)")
trace(c)

title("C. COMPENSATION 2: payment fails -> reserved inventory is released -> order cancelled")
c = f"demo-declined-{RUN}"
before = stock()["P002"]
oid = place("C-DECLINE", "P002", 2, c)
settle(oid, "CANCELLED")
print(f"P002 stock before={before} after={stock()['P002']} (reserved then released: unchanged)")
print("reservations:", http.get(f"{INV}/reservations/{oid}").json()["reservations"])
print("payment:     ", http.get(f"{PAY}/payments/{oid}").json()["status"])
trace(c)

title("D. COMPENSATION 3: order cancelled AFTER successful payment -> refund + stock released")
c = f"demo-cancel-{RUN}"
before = stock()["P002"]
oid = place("C002", "P002", 1, c)
settle(oid, "COMPLETED")
print(f"P002 stock after purchase: {stock()['P002']} (was {before})")
r = http.post(f"{ORDER}/orders/{oid}/cancel", headers={"X-Correlation-ID": c})
print(f"POST /orders/{oid}/cancel -> {r.status_code} (accepted; compensation completes asynchronously)")
settle(oid, "CANCELLED")
print("payment:", http.get(f"{PAY}/payments/{oid}").json()["status"], "| P002 stock:", stock()["P002"], f"(restored to {before})")
trace(c)

title("E. DUPLICATE DELIVERY: the same OrderCreated event published 3 times")
out = subprocess.run([sys.executable, str(ROOT / "tools" / "demo.py"), "duplicate"], capture_output=True, text=True).stdout
print(out)
oid = [l for l in out.splitlines() if l.startswith("order_id=")][0].split()[0].split("=")[1]
corr = out.split("correlation_id=")[1].split()[0]
time.sleep(1.5)
notes = http.get(f"{NOTIF}/notifications", params={"order_id": oid}).json()
print(f"notifications stored for {oid}: {len(notes)}  (3 deliveries, 1 effect)")
trace(corr)

title("F. DEAD-LETTERING: a malformed message is rejected and parked in the dead-letter queue")
print(subprocess.run([sys.executable, str(ROOT / "tools" / "demo.py"), "poison"], capture_output=True, text=True).stdout)
time.sleep(1.5)
print(subprocess.run([sys.executable, str(ROOT / "tools" / "demo.py"), "dlq"], capture_output=True, text=True).stdout)
print("service still healthy:", http.get(f"{NOTIF}/health").json())
