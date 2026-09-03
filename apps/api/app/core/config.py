"""
apps/api/app/core/config.py

Centralized settings. Loads from environment / .env.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="FIN_BP_", extra="ignore")

    # Paths
    project_root: str = ""  # auto-filled in main
    registry_path: str = "business_lines/registry.yaml"
    business_lines_root: str = "business_lines"

    # Database
    database_url: str = "postgresql+asyncpg://finbp:finbp@localhost:5432/finbp"
    redis_url: str = "redis://localhost:6379/0"
    clickhouse_url: str = "clickhouse://localhost:9000/default"

    # CORS
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
