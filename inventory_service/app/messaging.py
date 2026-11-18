import logging

from maltashop_common.messaging import RabbitConsumer, RabbitPublisher
from maltashop_common.outbox import OutboxRelay
from maltashop_common.topology import Q_INVENTORY

from app.database import SessionLocal
from app.handler import make_handler
from app.models import Outbox

logger = logging.getLogger("app.messaging")


def start_messaging(app, settings) -> None:
    relay = OutboxRelay(SessionLocal, Outbox, RabbitPublisher(settings.rabbitmq_url),
                        poll_interval=settings.outbox_poll_interval)
    consumer = RabbitConsumer(settings.rabbitmq_url, Q_INVENTORY, make_handler(SessionLocal),
                              max_attempts=settings.consumer_max_attempts,
                              backoff_seconds=settings.consumer_retry_backoff_seconds)
    relay.start(); consumer.start()
    app.state.relay, app.state.consumer = relay, consumer
    logger.info("messaging started", extra={"queue": Q_INVENTORY})


def stop_messaging(app) -> None:
    for name in ("consumer", "relay"):
        c = getattr(app.state, name, None)
        if c is not None:
            c.stop()
