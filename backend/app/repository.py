"""SQLite への読み書き。SQL はすべてこのモジュールに閉じ込める。"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from . import figma


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """SQLite の datetime('now') と比較できる 'YYYY-MM-DD HH:MM:SS' 形式。"""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --- users ----------------------------------------------------------------

def upsert_user(conn: sqlite3.Connection, profile: dict[str, Any]) -> sqlite3.Row:
    """Figma の /v1/me レスポンスからユーザーを作成 or 更新する。"""
    figma_user_id = str(profile.get("id") or profile.get("handle") or "")
    if not figma_user_id:
        raise ValueError("Figma のユーザー ID が取得できませんでした")

    conn.execute(
        """
        INSERT INTO users (figma_user_id, handle, email, img_url)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(figma_user_id) DO UPDATE SET
            handle     = excluded.handle,
            email      = excluded.email,
            img_url    = excluded.img_url,
            updated_at = datetime('now')
        """,
        (
            figma_user_id,
            profile.get("handle"),
            profile.get("email"),
            profile.get("img_url"),
        ),
    )
    row = conn.execute(
        "SELECT * FROM users WHERE figma_user_id = ?", (figma_user_id,)
    ).fetchone()
    assert row is not None
    return row


def get_user(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


# --- oauth tokens ---------------------------------------------------------

def save_tokens(
    conn: sqlite3.Connection, user_id: int, token_payload: dict[str, Any]
) -> None:
    try:
        expires_in = int(float(token_payload.get("expires_in")))
    except (TypeError, ValueError):
        expires_at = None
    else:
        expires_at = _iso(utcnow() + timedelta(seconds=expires_in))
    conn.execute(
        """
        INSERT INTO oauth_tokens (user_id, access_token, refresh_token, expires_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            access_token  = excluded.access_token,
            refresh_token = COALESCE(excluded.refresh_token, oauth_tokens.refresh_token),
            expires_at    = excluded.expires_at,
            updated_at    = datetime('now')
        """,
        (
            user_id,
            token_payload["access_token"],
            token_payload.get("refresh_token"),
            expires_at,
        ),
    )


def get_tokens(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM oauth_tokens WHERE user_id = ?", (user_id,)
    ).fetchone()


def token_is_expired(row: sqlite3.Row, *, skew_seconds: int = 60) -> bool:
    expires_at = _parse(row["expires_at"])
    if expires_at is None:
        return False
    return utcnow() + timedelta(seconds=skew_seconds) >= expires_at


# --- oauth state ----------------------------------------------------------

def create_state(
    conn: sqlite3.Connection, redirect_to: str | None = None, ttl_minutes: int = 10
) -> str:
    purge_expired(conn)
    state = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO oauth_states (state, redirect_to, expires_at) VALUES (?, ?, ?)",
        (state, redirect_to, _iso(utcnow() + timedelta(minutes=ttl_minutes))),
    )
    return state


def consume_state(conn: sqlite3.Connection, state: str) -> sqlite3.Row | None:
    """state を 1 回だけ引き換える。期限切れ・不正なら None。"""
    row = conn.execute(
        "SELECT * FROM oauth_states WHERE state = ?", (state,)
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
    expires_at = _parse(row["expires_at"])
    if expires_at is not None and expires_at < utcnow():
        return None
    return row


# --- sessions -------------------------------------------------------------

def create_session(
    conn: sqlite3.Connection, user_id: int, ttl_hours: int
) -> tuple[str, datetime]:
    """生トークンを返し、DB にはそのハッシュだけ保存する。"""
    raw = secrets.token_urlsafe(32)
    expires_at = utcnow() + timedelta(hours=ttl_hours)
    conn.execute(
        "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
        (hash_token(raw), user_id, _iso(expires_at)),
    )
    return raw, expires_at


def get_session_user(conn: sqlite3.Connection, raw_token: str) -> sqlite3.Row | None:
    session_id = hash_token(raw_token)
    row = conn.execute(
        """
        SELECT u.*, s.id AS session_id
          FROM sessions s
          JOIN users u ON u.id = s.user_id
         WHERE s.id = ? AND s.expires_at > datetime('now')
        """,
        (session_id,),
    ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE sessions SET last_seen_at = datetime('now') WHERE id = ?",
            (session_id,),
        )
    return row


def delete_session(conn: sqlite3.Connection, raw_token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE id = ?", (hash_token(raw_token),))


def purge_expired(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM sessions WHERE expires_at <= datetime('now')")
    conn.execute("DELETE FROM oauth_states WHERE expires_at <= datetime('now')")


# --- figma files / nodes --------------------------------------------------

def save_structure(
    conn: sqlite3.Connection,
    user_id: int,
    file_key: str,
    structure: dict[str, Any],
) -> sqlite3.Row:
    """変換済み構造体を figma_files に、ノードを figma_nodes に保存する。

    1 ファイル分の書き込みは 1 トランザクションにまとめる。
    """
    rows = figma.flatten(structure.get("document") or {})
    conn.execute("BEGIN")
    try:
        conn.execute(
            """
            INSERT INTO figma_files (
                user_id, file_key, name, version, last_modified,
                thumbnail_url, node_count, structure_json, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(user_id, file_key) DO UPDATE SET
                name           = excluded.name,
                version        = excluded.version,
                last_modified  = excluded.last_modified,
                thumbnail_url  = excluded.thumbnail_url,
                node_count     = excluded.node_count,
                structure_json = excluded.structure_json,
                fetched_at     = datetime('now')
            """,
            (
                user_id,
                file_key,
                structure.get("name"),
                structure.get("version"),
                structure.get("lastModified"),
                structure.get("thumbnailUrl"),
                len(rows),
                json.dumps(structure, ensure_ascii=False),
            ),
        )
        file_row = conn.execute(
            "SELECT * FROM figma_files WHERE user_id = ? AND file_key = ?",
            (user_id, file_key),
        ).fetchone()
        file_id = file_row["id"]

        conn.execute("DELETE FROM figma_nodes WHERE file_id = ?", (file_id,))
        conn.executemany(
            """
            INSERT INTO figma_nodes (
                file_id, node_id, parent_node_id, name, type,
                depth, order_index, characters, attrs_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_id, node_id) DO NOTHING
            """,
            [
                (
                    file_id,
                    r["node_id"],
                    r["parent_node_id"],
                    r["name"],
                    r["type"],
                    r["depth"],
                    r["order_index"],
                    r["characters"],
                    json.dumps(r["attrs"], ensure_ascii=False),
                )
                for r in rows
                if r["node_id"]
            ],
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return file_row


def get_file(
    conn: sqlite3.Connection, user_id: int, file_key: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM figma_files WHERE user_id = ? AND file_key = ?",
        (user_id, file_key),
    ).fetchone()


def file_is_fresh(row: sqlite3.Row, ttl_seconds: int) -> bool:
    if ttl_seconds <= 0:
        return False
    fetched_at = _parse(row["fetched_at"])
    if fetched_at is None:
        return False
    return utcnow() - fetched_at < timedelta(seconds=ttl_seconds)


def list_files(
    conn: sqlite3.Connection, user_id: int, limit: int = 50
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, file_key, name, version, last_modified,
               thumbnail_url, node_count, fetched_at
          FROM figma_files
         WHERE user_id = ?
         ORDER BY fetched_at DESC
         LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()


def delete_file(conn: sqlite3.Connection, user_id: int, file_key: str) -> int:
    cur = conn.execute(
        "DELETE FROM figma_files WHERE user_id = ? AND file_key = ?",
        (user_id, file_key),
    )
    return cur.rowcount


def query_nodes(
    conn: sqlite3.Connection,
    file_id: int,
    *,
    node_type: str | None = None,
    parent_node_id: str | None = None,
    name_like: str | None = None,
    max_depth: int | None = None,
    limit: int = 500,
    offset: int = 0,
) -> list[sqlite3.Row]:
    """保存済みノードを SQL で絞り込む（JSON を舐めずに済む）。"""
    sql = ["SELECT * FROM figma_nodes WHERE file_id = ?"]
    params: list[Any] = [file_id]
    if node_type:
        sql.append("AND type = ?")
        params.append(node_type)
    if parent_node_id is not None:
        sql.append("AND parent_node_id IS ?")
        params.append(parent_node_id)
    if name_like:
        sql.append("AND name LIKE ?")
        params.append(f"%{name_like}%")
    if max_depth is not None:
        sql.append("AND depth <= ?")
        params.append(max_depth)
    sql.append("ORDER BY depth, order_index, id LIMIT ? OFFSET ?")
    params.extend([limit, offset])
    return conn.execute(" ".join(sql), params).fetchall()


def node_type_counts(conn: sqlite3.Connection, file_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT type, COUNT(*) AS count
          FROM figma_nodes
         WHERE file_id = ?
         GROUP BY type
         ORDER BY count DESC
        """,
        (file_id,),
    ).fetchall()
