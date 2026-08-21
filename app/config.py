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
    if raw is None or raw.strip() == "":
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


def _float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    database_path: Path
    allowed_origins: tuple[str, ...]
    save_history: bool
    history_limit: int
    log_level: str

    # Gemini（活用方法の生成）
    gemini_api_key: str | None
    gemini_model: str
    gemini_api_revision: str
    gemini_timeout_seconds: float

    @property
    def gemini_enabled(self) -> bool:
        """API キーがあり、明示的に無効化されていないときだけ使う。"""
        return bool(self.gemini_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    origins = tuple(
        o.strip()
        for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
        if o.strip()
    )
    return Settings(
        database_path=Path(os.getenv("DATABASE_PATH", "./data/app.db")),
        allowed_origins=origins,
        save_history=_bool("SAVE_HISTORY", True),
        history_limit=_int("HISTORY_LIMIT", 100),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        gemini_api_key=(
            os.getenv("GEMINI_API_KEY") or None
            if _bool("GEMINI_ENABLED", True)
            else None
        ),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
        gemini_api_revision=os.getenv("GEMINI_API_REVISION", "2026-05-20"),
        gemini_timeout_seconds=_float("GEMINI_TIMEOUT_SECONDS", 30.0),
    )
