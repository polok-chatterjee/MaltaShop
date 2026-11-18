"""End-to-end harness: starts the four real services (uvicorn processes) against a REAL RabbitMQ.

Uses an isolated vhost (maltashop_test) and temporary SQLite files, so it never touches demo data.
Skipped automatically when no broker is reachable.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pika
import pytest

ROOT = Path(__file__).resolve().parent.parent
TEST_URL = os.environ.get("TEST_RABBITMQ_URL", "amqp://guest:guest@localhost:5672/maltashop_test")
SERVICES = {            # name -> (directory, port)
    "order": ("order_service", 18001),
    "notification": ("notification_service", 18002),
    "inventory": ("inventory_service", 18003),
    "payment": ("payment_service", 18004),
}
LOG_KEEP_DIR = ROOT / "tests_e2e" / "last_run_logs"


def broker_available() -> bool:
    try:
        pika.BlockingConnection(pika.URLParameters(TEST_URL)).close()
        return True
    except Exception:  # noqa: BLE001
        return False


def wait_until(fn, timeout=15.0, interval=0.1, message="condition"):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = fn()
            if last:
                return last
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout}s waiting for {message} (last={last!r})")


class Stack:
    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.procs: dict[str, subprocess.Popen] = {}
        self.http = httpx.Client(timeout=5.0)

    def url(self, name: str) -> str:
        return f"http://127.0.0.1:{SERVICES[name][1]}"

    def log_path(self, name: str) -> Path:
        return self.workdir / f"{name}.log"

    def start(self, name: str, env: dict | None = None) -> None:
        directory, port = SERVICES[name]
        environment = {**os.environ, "DATABASE_URL": f"sqlite:///{self.workdir}/{name}.db",
                       "RABBITMQ_URL": TEST_URL, "MESSAGING_ENABLED": "true",
                       "OUTBOX_POLL_INTERVAL": "0.2", "CONSUMER_RETRY_BACKOFF_SECONDS": "0.2",
                       **(env or {})}
        log = open(self.log_path(name), "ab")
        self.procs[name] = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
            cwd=ROOT / directory, env=environment, stdout=log, stderr=subprocess.STDOUT)
        wait_until(lambda: self._ready(name), timeout=25, message=f"{name} service ready")

    def _ready(self, name: str) -> bool:
        r = self.http.get(self.url(name) + "/health")
        return r.status_code == 200 and r.json().get("messaging", {}).get("consumer_connected") is True

    def stop(self, name: str) -> None:
        p = self.procs.pop(name, None)
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()

    def logs(self, name: str) -> list[dict]:
        out = []
        for line in self.log_path(name).read_text(errors="replace").splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return out

    def log_lines(self, name: str, **match) -> list[dict]:
        return [r for r in self.logs(name) if all(r.get(k) == v for k, v in match.items())]

    # ---- convenience API calls
    def post_order(self, customer="C001", product="P001", qty=1, corr=None):
        headers = {"X-Correlation-ID": corr} if corr else {}
        return self.http.post(self.url("order") + "/orders", headers=headers,
                              json={"customer_id": customer, "items": [{"product_id": product, "quantity": qty}]})

    def order(self, order_id):
        return self.http.get(f"{self.url('order')}/orders/{order_id}").json()

    def stock(self):
        return self.http.get(self.url("inventory") + "/stock").json()["stock"]

    def notifications(self, order_id):
        return self.http.get(f"{self.url('notification')}/notifications", params={"order_id": order_id}).json()


def _reset_broker():
    from maltashop_common.topology import QUEUE_BINDINGS, declare_topology, dlq_name
    conn = pika.BlockingConnection(pika.URLParameters(TEST_URL))
    ch = conn.channel()
    declare_topology(ch)
    for q in QUEUE_BINDINGS:
        ch.queue_purge(q)
        ch.queue_purge(dlq_name(q))
    conn.close()


def queue_depth(name: str) -> int:
    conn = pika.BlockingConnection(pika.URLParameters(TEST_URL))
    try:
        return conn.channel().queue_declare(name, passive=True).method.message_count
    finally:
        conn.close()


@pytest.fixture(scope="session")
def stack(tmp_path_factory):
    if not broker_available():
        pytest.skip(f"RabbitMQ not reachable at {TEST_URL}")
    _reset_broker()
    workdir = tmp_path_factory.mktemp("e2e")
    s = Stack(workdir)
    try:
        for name in SERVICES:
            s.start(name)
        yield s
    finally:
        for name in list(s.procs):
            s.stop(name)
        shutil.rmtree(LOG_KEEP_DIR, ignore_errors=True)
        LOG_KEEP_DIR.mkdir(parents=True)
        for name in SERVICES:
            if s.log_path(name).exists():
                shutil.copy(s.log_path(name), LOG_KEEP_DIR / f"{name}.log")
