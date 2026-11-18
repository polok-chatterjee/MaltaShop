from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    service_name: str = "inventory-service"
    database_url: str = "sqlite:///./inventory_service.db"
    log_level: str = "INFO"
    correlation_id_header: str = "X-Correlation-ID"
    messaging_enabled: bool = True
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/maltashop"
    outbox_poll_interval: float = 0.5
    consumer_max_attempts: int = 3
    consumer_retry_backoff_seconds: float = 0.5
    inventory_seed: str = "P001:100,P002:5,P003:0"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
