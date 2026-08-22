"""SQLite 接続とスキーマ定義。

ORM は使わず標準ライブラリの sqlite3 だけで完結させている。
テーブル定義（= このアプリのデータ構造）はすべて SCHEMA にまとまっている。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from .config import get_settings

logger = logging.getLogger(__name__)

SCHEMA = """
-- 地域ごとの相場・需要データ。
-- city = '' の行がその都道府県のデフォルト（市区町村が未登録のときに使う）。
CREATE TABLE IF NOT EXISTS regions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    prefecture           TEXT NOT NULL,
    city                 TEXT NOT NULL DEFAULT '',
    land_price_per_tsubo INTEGER NOT NULL,  -- 坪単価（万円）
    rent_per_tsubo       INTEGER NOT NULL,  -- 坪あたり月額賃料（円）
    rent_demand          REAL    NOT NULL,  -- 賃貸需要 0.0-1.0
    population_trend     REAL    NOT NULL,  -- 人口動態 -1.0-1.0
    vacancy_rate         REAL    NOT NULL,  -- 空き家率 0.0-1.0
    UNIQUE (prefecture, city)
);
CREATE INDEX IF NOT EXISTS idx_regions_pref ON regions(prefecture);

-- 造りごとの係数。法定耐用年数から建物の残存価値を出す。
CREATE TABLE IF NOT EXISTS structure_factors (
    structure                 TEXT PRIMARY KEY,
    legal_life_years          INTEGER NOT NULL,  -- 法定耐用年数
    build_cost_per_tsubo      INTEGER NOT NULL,  -- 再建築費の目安（万円/坪）
    renovation_cost_per_tsubo INTEGER NOT NULL   -- 賃貸化リフォーム費（万円/坪）
);

-- 種別ごとの重み。rentable = 0 なら賃貸を候補から外す。
CREATE TABLE IF NOT EXISTS property_type_factors (
    property_type TEXT PRIMARY KEY,
    sell_weight   REAL NOT NULL DEFAULT 1.0,
    rent_weight   REAL NOT NULL DEFAULT 1.0,
    hold_weight   REAL NOT NULL DEFAULT 1.0,
    rentable      INTEGER NOT NULL DEFAULT 1
);

-- 診断レポートの本文テンプレート。
-- recommendation = 'any' の行は、判定結果によらない共通文。
-- （NULL にすると SQLite の UNIQUE が NULL 同士を別物として扱い、再投入で重複するため）
CREATE TABLE IF NOT EXISTS advice_templates (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id     TEXT NOT NULL,
    recommendation TEXT NOT NULL DEFAULT 'any',
    body           TEXT NOT NULL,
    UNIQUE (section_id, recommendation)
);
CREATE INDEX IF NOT EXISTS idx_templates_section ON advice_templates(section_id);

-- 「活用例」セクションの生成結果のキャッシュ。
-- 同じ条件で何度も LLM を呼ばないようにする。
CREATE TABLE IF NOT EXISTS usecase_cache (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key  TEXT NOT NULL UNIQUE,
    prefecture TEXT NOT NULL,
    city       TEXT NOT NULL,
    model      TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 診断の履歴。入力と結果をそのまま残す。
CREATE TABLE IF NOT EXISTS diagnoses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    prefecture     TEXT NOT NULL,
    city           TEXT NOT NULL,
    detail_json    TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    scores_json    TEXT NOT NULL,
    sections_json  TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_diagnoses_created ON diagnoses(created_at DESC);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """設定済みの sqlite3 コネクションを開く。"""
    settings = get_settings()
    db_path = Path(path) if path is not None else settings.database_path
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)

    # check_same_thread=False: FastAPI は sync な依存関係をスレッドプールで動かすため。
    # コネクションはリクエストごとに 1 本なので、同時に複数スレッドから触られることはない。
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> None:
    """スキーマ作成と初期データ投入。何度呼んでも安全。"""
    from .seed import seed_all

    own = conn or connect()
    try:
        own.executescript(SCHEMA)
        seed_all(own)
    finally:
        if conn is None:
            own.close()


def schema_is_ready(conn: sqlite3.Connection) -> bool:
    """スキーマが入っているかを 1 クエリで確かめる。"""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'regions'"
    ).fetchone()
    return row is not None


def ensure_schema(conn: sqlite3.Connection) -> None:
    """DB ファイルが消えた・作り直された場合でも、その場で復旧する。

    sqlite3 は存在しないパスを開くと空の DB を黙って作るため、
    これが無いと「no such table: regions」で 500 になる。
    起動後に data/ を消す、DATABASE_PATH を変える、といった操作で実際に起きる。
    """
    if not schema_is_ready(conn):
        logger.warning("スキーマが見つからないため再作成します: %s", get_settings().database_path)
        init_db(conn)


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI の依存関係。リクエストごとに 1 コネクション。"""
    conn = connect()
    try:
        ensure_schema(conn)
        yield conn
    finally:
        conn.close()
