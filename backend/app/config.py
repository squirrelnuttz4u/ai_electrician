"""Application configuration, loaded from environment variables."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    postgres_user: str = "aielec"
    postgres_password: str = "change-me-in-production"
    postgres_db: str = "aielectrician"
    postgres_host: str = "db"
    postgres_port: int = 5432

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Storage
    data_dir: str = "/data"

    # Ollama
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_chat_model: str = "gpt-oss:120b"
    ollama_vision_model: str = "qwen2.5-vl:72b"
    ollama_embed_model: str = "nomic-embed-text"
    embed_dim: int = 768
    ollama_timeout: int = 600

    # PDF processing
    render_dpi: int = 200
    text_layer_min_chars: int = 40

    # Auth
    auth_enabled: bool = False
    auth_shared_password: str = "change-me"
    auth_secret: str = "please-generate-a-long-random-string"

    # App
    cors_origins: str = "*"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
