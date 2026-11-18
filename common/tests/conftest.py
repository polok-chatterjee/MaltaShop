import os
import pika
import pytest

TEST_URL = os.environ.get("TEST_RABBITMQ_URL", "amqp://guest:guest@localhost:5672/maltashop_test")


def broker_available() -> bool:
    try:
        pika.BlockingConnection(pika.URLParameters(TEST_URL)).close()
        return True
    except Exception:  # noqa: BLE001
        return False


requires_broker = pytest.mark.skipif(not broker_available(), reason=f"RabbitMQ not reachable at {TEST_URL}")
