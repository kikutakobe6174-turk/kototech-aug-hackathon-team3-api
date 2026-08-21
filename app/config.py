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


@dataclass(frozen=True)
class Settings:
    database_path: Path
    allowed_origins: tuple[str, ...]
    save_history: bool
    history_limit: int


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
    )
