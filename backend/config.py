from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str = "AI Code Review Agent"
    app_version: str = "1.0.0"
    groq_api_key: str | None = os.getenv("GROQ_API_KEY")
    groq_model: str = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
    groq_base_url: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    github_token: str | None = os.getenv("GITHUB_TOKEN")
    github_webhook_secret: str | None = os.getenv("GITHUB_WEBHOOK_SECRET")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./ai_code_review.db")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    max_code_bytes: int = int(os.getenv("MAX_CODE_BYTES", "100000"))
    cors_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
        if origin.strip()
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
