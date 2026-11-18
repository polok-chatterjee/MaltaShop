from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.models import Channel

Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


class NotificationCreate(BaseModel):
    order_id: Identifier
    customer_id: Identifier
    channel: Channel = Channel.EMAIL
    message: str | None = Field(default=None, min_length=1, max_length=1000)


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    notification_id: str = Field(validation_alias="id")
    order_id: str
    customer_id: str
    channel: str
    message: str
    status: str
    failure_reason: str | None
    attempts: int
    correlation_id: str
    created_at: datetime
    updated_at: datetime

    @field_validator("channel", "status", mode="before")
    @classmethod
    def _enum_value(cls, v):
        return getattr(v, "value", v)

    @field_serializer("created_at", "updated_at")
    def _ser_dt(self, v: datetime) -> str:
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.isoformat()
