"""Request/response schemas (API contract and input validation)."""
from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


class OrderItemIn(BaseModel):
    product_id: Identifier
    quantity: int = Field(ge=1, le=1000, description="Units ordered (1-1000)")


class OrderCreate(BaseModel):
    customer_id: Identifier
    items: list[OrderItemIn] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def no_duplicate_products(self):
        ids = [i.product_id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate product_id in items; combine quantities instead")
        return self


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    product_id: str
    quantity: int


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: str = Field(validation_alias="id")
    customer_id: str
    status: str
    correlation_id: str
    created_at: datetime
    items: list[OrderItemOut]

    @field_validator("status", mode="before")
    @classmethod
    def _status_value(cls, v):
        return getattr(v, "value", v)

    @field_serializer("created_at")
    def _ser_created(self, v: datetime) -> str:
        # SQLite drops tzinfo; values are always stored as UTC.
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.isoformat()
