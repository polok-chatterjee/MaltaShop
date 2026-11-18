"""Business logic. No HTTP concerns live here."""
import logging

from sqlalchemy.orm import Session

from app.exceptions import OrderNotFoundError
from app.models import Order, OrderItem, OrderStatus
from app.schemas import OrderCreate

logger = logging.getLogger("app.orders")


class OrderService:
    def __init__(self, db: Session):
        self.db = db

    def create_order(self, data: OrderCreate, correlation_id: str) -> Order:
        order = Order(
            customer_id=data.customer_id,
            status=OrderStatus.PENDING,
            correlation_id=correlation_id,
            items=[OrderItem(product_id=i.product_id, quantity=i.quantity) for i in data.items],
        )
        self.db.add(order)
        self.db.commit()
        logger.info(
            "order created",
            extra={"order_id": order.id, "customer_id": order.customer_id, "item_count": len(order.items)},
        )
        return order

    def get_order(self, order_id: str) -> Order:
        order = self.db.get(Order, order_id)
        if order is None:
            raise OrderNotFoundError(order_id)
        return order
