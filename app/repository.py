"""SQLite への読み書き。SQL はすべてこのモジュールに閉じ込める。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

DEFAULT_STRUCTURE = "その他"
DEFAULT_PROPERTY_TYPE = "戸建て"
COMMON_TEMPLATE = "any"


def find_region(
    conn: sqlite3.Connection, prefecture: str, city: str
) -> sqlite3.Row | None:
    """市区町村の行があればそれを、無ければ都道府県のデフォルト行を返す。"""
    row = conn.execute(
        "SELECT * FROM regions WHERE prefecture = ? AND city = ?",
        (prefecture, city),
    ).fetchone()
    if row is not None:
        return row
    return conn.execute(
        "SELECT * FROM regions WHERE prefecture = ? AND city = ''", (prefecture,)
    ).fetchone()


def get_structure_factor(
    conn: sqlite3.Connection, structure: str | None
) -> sqlite3.Row:
    """未入力・未知の造りは「その他」にフォールバックする。"""
    row = None
    if structure:
        row = conn.execute(
            "SELECT * FROM structure_factors WHERE structure = ?", (structure,)
        ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM structure_factors WHERE structure = ?", (DEFAULT_STRUCTURE,)
        ).fetchone()
    if row is None:
        raise LookupError("structure_factors が未投入です。init_db を実行してください。")
    return row


def get_property_type_factor(
    conn: sqlite3.Connection, property_type: str | None
) -> sqlite3.Row:
    """未入力・未知の種別は「戸建て」にフォールバックする。"""
    row = None
    if property_type:
        row = conn.execute(
            "SELECT * FROM property_type_factors WHERE property_type = ?",
            (property_type,),
        ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM property_type_factors WHERE property_type = ?",
            (DEFAULT_PROPERTY_TYPE,),
        ).fetchone()
    if row is None:
        raise LookupError(
            "property_type_factors が未投入です。init_db を実行してください。"
        )
    return row


def get_templates(conn: sqlite3.Connection, recommendation: str) -> dict[str, str]:
    """セクション id → 本文。判定別の行があれば共通文より優先する。"""
    rows = conn.execute(
        """
        SELECT section_id, recommendation, body
          FROM advice_templates
         WHERE recommendation IN (?, ?)
         ORDER BY CASE recommendation WHEN ? THEN 1 ELSE 0 END
        """,
        (COMMON_TEMPLATE, recommendation, recommendation),
    ).fetchall()
    # 共通文 → 判定別の順で上書きされるので、判定別が最終的に残る
    return {row["section_id"]: row["body"] for row in rows}


def list_prefectures(conn: sqlite3.Connection) -> list[str]:
    return [
        r["prefecture"]
        for r in conn.execute(
            "SELECT prefecture FROM regions WHERE city = '' ORDER BY id"
        )
    ]


def save_diagnosis(
    conn: sqlite3.Connection,
    *,
    prefecture: str,
    city: str,
    detail: dict[str, Any],
    recommendation: str,
    scores: dict[str, float],
    sections: dict[str, str],
) -> int:
    cur = conn.execute(
        """
        INSERT INTO diagnoses (
            prefecture, city, detail_json, recommendation, scores_json, sections_json
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            prefecture,
            city,
            json.dumps(detail, ensure_ascii=False),
            recommendation,
            json.dumps(scores, ensure_ascii=False),
            json.dumps(sections, ensure_ascii=False),
        ),
    )
    return int(cur.lastrowid or 0)


def trim_history(conn: sqlite3.Connection, keep: int) -> None:
    """履歴が増えすぎないように、新しい方から keep 件だけ残す。"""
    conn.execute(
        """
        DELETE FROM diagnoses
         WHERE id NOT IN (SELECT id FROM diagnoses ORDER BY id DESC LIMIT ?)
        """,
        (keep,),
    )


def list_diagnoses(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, prefecture, city, detail_json, recommendation, scores_json, created_at
          FROM diagnoses
         ORDER BY id DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()


# --- 活用例のキャッシュ -----------------------------------------------------

def get_cached_usecase(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM usecase_cache WHERE cache_key = ?", (key,)
    ).fetchone()


def save_usecase(
    conn: sqlite3.Connection,
    *,
    key: str,
    prefecture: str,
    city: str,
    model: str,
    body: str,
) -> None:
    conn.execute(
        """
        INSERT INTO usecase_cache (cache_key, prefecture, city, model, body)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            body       = excluded.body,
            model      = excluded.model,
            created_at = datetime('now')
        """,
        (key, prefecture, city, model, body),
    )


def trim_usecase_cache(conn: sqlite3.Connection, keep: int) -> None:
    conn.execute(
        """
        DELETE FROM usecase_cache
         WHERE id NOT IN (SELECT id FROM usecase_cache ORDER BY id DESC LIMIT ?)
        """,
        (keep,),
    )


# --- 解体費用 / 活用事例（出典ありの参照データ） -----------------------------

def get_region_name(conn: sqlite3.Connection, prefecture: str) -> str | None:
    row = conn.execute(
        "SELECT region FROM prefecture_regions WHERE prefecture = ?", (prefecture,)
    ).fetchone()
    return row["region"] if row else None


def get_demolition_cost(
    conn: sqlite3.Connection, region: str | None, structure: str
) -> int | None:
    """地方 × 構造の解体坪単価（円/坪）。地方が不明なら全国平均で代替する。"""
    if region:
        row = conn.execute(
            "SELECT cost_per_tsubo FROM demolition_costs WHERE region = ? AND structure = ?",
            (region, structure),
        ).fetchone()
        if row:
            return row["cost_per_tsubo"]
    row = conn.execute(
        """
        SELECT CAST(AVG(cost_per_tsubo) AS INTEGER) AS cost
          FROM demolition_costs WHERE structure = ?
        """,
        (structure,),
    ).fetchone()
    return row["cost"] if row and row["cost"] else None


def pick_usecase_examples(
    conn: sqlite3.Connection,
    *,
    prefecture: str,
    region: str | None,
    category: str | None,
    limit: int = 4,
) -> list[sqlite3.Row]:
    """同じ都道府県 → 同じ地方 → 同じ分類 → その他、の優先順で事例を選ぶ。"""
    return conn.execute(
        """
        SELECT *,
               CASE
                   WHEN prefecture = ?      THEN 0
                   WHEN region = ?          THEN 1
                   WHEN category = ?        THEN 2
                   ELSE 3
               END AS rank
          FROM usecase_examples
         ORDER BY rank, id
         LIMIT ?
        """,
        (prefecture, region or "", category or "", limit),
    ).fetchall()
