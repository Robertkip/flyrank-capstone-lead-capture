"""Application settings, loaded from the environment (never hard-coded)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Core
    base_url: str = "http://localhost:8000"
    log_level: str = "INFO"

    # Database
    database_url: str = "postgresql+psycopg://leadcapture:leadcapture@db:5432/leadcapture"
    test_database_url: str = (
        "postgresql+psycopg://leadcapture:leadcapture@db:5432/leadcapture_test"
    )

    # Auth
    jwt_secret: str = "insecure-dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_ip_per_minute: int = 30
    rate_limit_widget_per_minute: int = 120

    # Payload limits
    max_body_bytes: int = 8192
    max_field_length: int = 2000

    # Spam
    honeypot_field: str = "website_url"
    min_fill_seconds: float = 1.5

    # Geo enrichment
    geo_mode: str = "mock"  # "mock" | "live"
    geo_timeout_seconds: float = 2.0
    geo_mock_a_up: bool = True
    geo_mock_b_up: bool = True
    geo_dev_fallback_ip: str = "8.8.8.8"

    # Outbox / side effects
    outbox_worker_enabled: bool = True
    outbox_poll_seconds: float = 1.0
    outbox_max_attempts: int = 5
    outbox_backoff_base_seconds: int = 2
    email_mode: str = "console"  # "console" | "smtp"
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    email_from: str = "no-reply@leadcapture.local"
    side_effect_force_fail: bool = False

    # Widget asset delivery
    widget_bundle_version: str = "v1"
    config_cache_max_age: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
