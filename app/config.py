"""
Ralph Service Configuration

Environment Variables:
- DATABASE_URL: Ralph's own PostgreSQL database
- REDIS_URL: Redis for rate limiting
- ADMIN_API_KEY: API key for admin operations
- ENV: environment (development/production)
"""

import os
from typing import List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Ralph service settings."""

    # Core
    ENV: str = os.getenv("ENV", "development")
    DEBUG: bool = ENV == "development"

    # Database (Ralph's own DB, not project DBs)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    # Redis (for rate limiting)
    REDIS_URL: str = os.getenv("REDIS_URL", "")

    # Authentication
    ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", "")

    # CORS
    ALLOWED_ORIGINS: List[str] = [
        "https://chat.openai.com",
        "https://chatgpt.com",
        "http://localhost:3000",  # Local dev
    ]

    # Rate Limiting
    REVIEW_RATE_LIMIT: int = 4  # reviews per hour
    REVIEW_RATE_WINDOW: int = 3600  # 1 hour in seconds

    # Dispatcher
    DISPATCHER_INTERVAL_MINUTES: int = 5
    DISPATCHER_BATCH_SIZE: int = 10

    # Guardrails
    MAX_ITERATIONS: int = 3
    FORBIDDEN_PATHS: List[str] = [
        "backend/app/core/security",
        "backend/app/services/billing",
        "backend/app/core/config.py",
    ]
    DEPENDENCY_FILES: List[str] = [
        "requirements.txt",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
    ]

    class Config:
        env_file = ".env"
        case_sensitive = True


_settings = None


def get_settings() -> Settings:
    """Get settings instance (singleton)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
