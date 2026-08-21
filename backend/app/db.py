"""SQLite 接続とスキーマ定義。

ORM は使わず標準ライブラリの sqlite3 だけで完結させている。
テーブル定義（= このアプリのデータ構造）はすべて SCHEMA にまとまっている。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .config import get_settings

SCHEMA = """
-- Figma でログインしたユーザー
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    figma_user_id  TEXT NOT NULL UNIQUE,
    handle         TEXT,
    email          TEXT,
    img_url        TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Figma OAuth のアクセストークン（ユーザー 1 人につき 1 行）
CREATE TABLE IF NOT EXISTS oauth_tokens (
    user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    access_token  TEXT NOT NULL,
    refresh_token TEXT,
    expires_at    TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- OAuth の state（CSRF 対策）。使い捨て。
CREATE TABLE IF NOT EXISTS oauth_states (
    state       TEXT PRIMARY KEY,
    redirect_to TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT NOT NULL
);

-- ログインセッション。id は cookie に入れるトークンの SHA-256。
CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- 取得した Figma ファイル 1 件（= 変換済み JSON 構造のキャッシュ / 履歴）
CREATE TABLE IF NOT EXISTS figma_files (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    file_key       TEXT NOT NULL,
    name           TEXT,
    version        TEXT,
    last_modified  TEXT,
    thumbnail_url  TEXT,
    node_count     INTEGER NOT NULL DEFAULT 0,
    structure_json TEXT NOT NULL,
    fetched_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, file_key)
);
CREATE INDEX IF NOT EXISTS idx_files_user ON figma_files(user_id, fetched_at DESC);

-- ファイルのノードツリーをフラットに展開したもの。
-- parent_node_id による自己参照で階層を表現する（JSON を舐めずに SQL で検索できる）。
CREATE TABLE IF NOT EXISTS figma_nodes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id        INTEGER NOT NULL REFERENCES figma_files(id) ON DELETE CASCADE,
    node_id        TEXT NOT NULL,
    parent_node_id TEXT,
    name           TEXT,
    type           TEXT,
    depth          INTEGER NOT NULL,
    order_index    INTEGER NOT NULL,
    characters     TEXT,
    attrs_json     TEXT NOT NULL DEFAULT '{}',
    UNIQUE (file_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON figma_nodes(file_id, parent_node_id);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON figma_nodes(file_id, type);
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
    """スキーマを作成する（何度呼んでも安全）。"""
    if conn is not None:
        conn.executescript(SCHEMA)
        return
    own = connect()
    try:
        own.executescript(SCHEMA)
    finally:
        own.close()


@contextmanager
def session_scope() -> Iterator[sqlite3.Connection]:
    """`BEGIN` 〜 `COMMIT` / `ROLLBACK` をまとめて面倒みる。"""
    conn = connect()
    try:
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI の依存関係。リクエストごとに 1 コネクション。"""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
