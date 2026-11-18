"""Environment-based configuration (12-factor style)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    service_name: str = "order-service"
    database_url: str = "sqlite:///./order_service.db"
    log_level: str = "INFO"
    correlation_id_header: str = "X-Correlation-ID"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
