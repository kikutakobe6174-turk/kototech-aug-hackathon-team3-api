"""環境変数から読み込むアプリ設定。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Figma OAuth
    figma_client_id: str
    figma_client_secret: str
    figma_redirect_uri: str
    figma_scope: str

    # Frontend / CORS
    frontend_url: str
    allowed_origins: tuple[str, ...]

    # Session cookie
    cookie_name: str
    cookie_secure: bool
    cookie_samesite: str
    cookie_domain: str | None
    session_ttl_hours: int

    # Storage
    database_path: Path
    file_cache_ttl_seconds: int
    max_tree_depth: int

    # Dev
    dev_mode: bool
    figma_pat: str | None

    @property
    def oauth_configured(self) -> bool:
        return bool(self.figma_client_id and self.figma_client_secret)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    origins = tuple(
        o.strip()
        for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
        if o.strip()
    )
    return Settings(
        figma_client_id=os.getenv("FIGMA_CLIENT_ID", ""),
        figma_client_secret=os.getenv("FIGMA_CLIENT_SECRET", ""),
        figma_redirect_uri=os.getenv(
            "FIGMA_REDIRECT_URI", "http://localhost:8000/callback"
        ),
        figma_scope=os.getenv("FIGMA_SCOPE", "files:read"),
        frontend_url=os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/"),
        allowed_origins=origins,
        cookie_name=os.getenv("SESSION_COOKIE_NAME", "session"),
        cookie_secure=_bool("COOKIE_SECURE", False),
        cookie_samesite=os.getenv("COOKIE_SAMESITE", "lax").lower(),
        cookie_domain=os.getenv("COOKIE_DOMAIN") or None,
        session_ttl_hours=_int("SESSION_TTL_HOURS", 24 * 7),
        database_path=Path(os.getenv("DATABASE_PATH", "./data/app.db")),
        file_cache_ttl_seconds=_int("FILE_CACHE_TTL_SECONDS", 300),
        max_tree_depth=_int("MAX_TREE_DEPTH", 100),
        dev_mode=_bool("DEV_MODE", False),
        figma_pat=os.getenv("FIGMA_PERSONAL_ACCESS_TOKEN") or None,
    )
