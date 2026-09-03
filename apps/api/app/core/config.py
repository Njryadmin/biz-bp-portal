"""
apps/api/app/core/config.py

Centralized settings. Loads from environment / .env.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="BIZ_BP_", extra="ignore")

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

    # Authentication / RBAC (see core/auth.py)
    # NOTE: we expose only non-secret defaults here. ``JWT_SECRET`` is
    # read from the bare ``JWT_SECRET`` env var (no BIZ_BP_ prefix) to
    # match industry convention.
    jwt_secret: str = "change-me-in-production-32-chars-min-please"
    cookie_name: str = "finbp_token"
    cookie_secure: bool = False

    # AI-model registry: symmetric key used to encrypt stored api_key
    # values. If unset, the registry stores api_key as plaintext (or
    # treats ``env:VAR`` references as pass-through). See
    # ``core/secret.py`` for the Fernet-based encryption helper.
    ai_secret_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
