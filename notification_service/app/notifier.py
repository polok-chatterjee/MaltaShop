"""Delivery channel abstraction.

The prototype does not talk to a real email/SMS provider. `SimulatedNotifier`
logs the message and reports success, which is enough to exercise the
status-tracking logic. A real provider (SMTP, SMS gateway) would implement
the same `send` interface.
"""
import logging
from typing import Protocol

logger = logging.getLogger("app.notifier")


class DeliveryError(Exception):
    pass


class Notifier(Protocol):
    def send(self, channel: str, customer_id: str, message: str) -> None: ...


class SimulatedNotifier:
    def send(self, channel: str, customer_id: str, message: str) -> None:
        logger.info(
            "simulated delivery",
            extra={"channel": channel, "customer_id": customer_id},
        )
