from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "E-Commerce API"
    app_version: str = "1.0.0"
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False

    database_url: PostgresDsn = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/ecommerce",
    )
    sql_echo: bool = False

    redis_url: RedisDsn = Field(default="redis://localhost:6379/0")

    secret_key: str = Field(
        default="dev-secret-key-change-in-production-32c",
        min_length=32,
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    cors_origins: list[str] = Field(default=["http://localhost:3000"])
    cors_allow_credentials: bool = True
    cors_allow_methods: list[str] = Field(default=["*"])
    cors_allow_headers: list[str] = Field(default=["*"])

    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60

    cart_session_header: str = "X-Cart-Session"
    cart_ttl_seconds: int = 60 * 60 * 24 * 30

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    chat_confidence_threshold: float = 0.55
    chat_history_max_messages: int = 20
    chat_session_ttl_seconds: int = 60 * 60 * 24 * 7

    log_level: str = "INFO"
    seed_policies_on_startup: bool = True

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate_production(self) -> None:
        if not self.is_production:
            return
        if self.secret_key.startswith("dev-"):
            raise RuntimeError("SECRET_KEY must be set to a secure value in production")
        if not self.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required in production")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_production()
    return settings
