"""Figma ファイルを取得して JSON 構造体にするエンドポイント。"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import figma as figma_client
from .. import repository
from ..config import Settings
from ..db import get_db
from ..deps import get_current_user, get_figma_credentials, settings_dep
from ..models import (
    FigmaFileRequest,
    FigmaFileResponse,
    FigmaFileSummary,
    NodeOut,
    TypeCount,
)

router = APIRouter(prefix="/api/figma", tags=["figma"])


def _summary(row: sqlite3.Row) -> FigmaFileSummary:
    return FigmaFileSummary(
        file_key=row["file_key"],
        name=row["name"],
        version=row["version"],
        last_modified=row["last_modified"],
        thumbnail_url=row["thumbnail_url"],
        node_count=row["node_count"],
        fetched_at=row["fetched_at"],
    )


def _response(row: sqlite3.Row, *, cached: bool) -> FigmaFileResponse:
    return FigmaFileResponse(
        file_key=row["file_key"],
        name=row["name"],
        version=row["version"],
        last_modified=row["last_modified"],
        thumbnail_url=row["thumbnail_url"],
        node_count=row["node_count"],
        fetched_at=row["fetched_at"],
        cached=cached,
        structure=json.loads(row["structure_json"]),
    )


def _require_file(conn: sqlite3.Connection, user_id: int, file_key: str) -> sqlite3.Row:
    row = repository.get_file(conn, user_id, file_key)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"file_key '{file_key}' はまだ取得されていません。",
        )
    return row


@router.post("", response_model=FigmaFileResponse, summary="Figma ファイルを構造体で取得")
@router.post("/", response_model=FigmaFileResponse, include_in_schema=False)
async def fetch_file(
    body: FigmaFileRequest,
    user: sqlite3.Row = Depends(get_current_user),
    credentials: tuple[str, bool] = Depends(get_figma_credentials),
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> FigmaFileResponse:
    file_key = body.file_key.strip()
    cached_row = repository.get_file(conn, user["id"], file_key)
    if (
        not body.refresh
        and cached_row is not None
        and repository.file_is_fresh(cached_row, settings.file_cache_ttl_seconds)
    ):
        return _response(cached_row, cached=True)

    token, personal = credentials
    try:
        payload = await figma_client.get_file(token, file_key, personal=personal)
    except figma_client.FigmaError as exc:
        # 403/404 はそのまま返して、フロントで理由が分かるようにする
        code = exc.status_code if exc.status_code in (400, 403, 404, 429) else 502
        raise HTTPException(status_code=code, detail=exc.message) from exc

    structure = figma_client.build_structure(
        payload, max_depth=body.max_depth or settings.max_tree_depth
    )
    row = repository.save_structure(conn, user["id"], file_key, structure)
    return _response(row, cached=False)


@router.get("/history", response_model=list[FigmaFileSummary], summary="取得履歴")
def history(
    limit: int = Query(default=50, ge=1, le=200),
    user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[FigmaFileSummary]:
    return [_summary(r) for r in repository.list_files(conn, user["id"], limit)]


@router.get("/{file_key}", response_model=FigmaFileResponse, summary="保存済みの構造体")
def get_cached(
    file_key: str,
    user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> FigmaFileResponse:
    return _response(_require_file(conn, user["id"], file_key), cached=True)


@router.get(
    "/{file_key}/nodes",
    response_model=list[NodeOut],
    summary="保存済みノードを SQL で絞り込む",
)
def get_nodes(
    file_key: str,
    type: str | None = Query(default=None, description="NODE の type で絞り込む"),
    parent: str | None = Query(default=None, description="親ノード ID"),
    name: str | None = Query(default=None, description="名前の部分一致"),
    max_depth: int | None = Query(default=None, ge=0),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[NodeOut]:
    file_row = _require_file(conn, user["id"], file_key)
    rows = repository.query_nodes(
        conn,
        file_row["id"],
        node_type=type,
        parent_node_id=parent,
        name_like=name,
        max_depth=max_depth,
        limit=limit,
        offset=offset,
    )
    return [
        NodeOut(
            node_id=r["node_id"],
            parent_node_id=r["parent_node_id"],
            name=r["name"],
            type=r["type"],
            depth=r["depth"],
            order_index=r["order_index"],
            characters=r["characters"],
            attrs=json.loads(r["attrs_json"]),
        )
        for r in rows
    ]


@router.get(
    "/{file_key}/stats", response_model=list[TypeCount], summary="ノード種別ごとの件数"
)
def get_stats(
    file_key: str,
    user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[TypeCount]:
    file_row = _require_file(conn, user["id"], file_key)
    return [
        TypeCount(type=r["type"], count=r["count"])
        for r in repository.node_type_counts(conn, file_row["id"])
    ]


@router.delete("/{file_key}", status_code=status.HTTP_204_NO_CONTENT, summary="履歴を削除")
def delete_file(
    file_key: str,
    user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    if repository.delete_file(conn, user["id"], file_key) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="削除対象がありません。"
        )
