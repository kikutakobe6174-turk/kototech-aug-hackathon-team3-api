"""FastAPI の依存関係（認証まわり）。"""

from __future__ import annotations

import sqlite3

from fastapi import Depends, HTTPException, Request, status

from . import figma, repository
from .config import Settings, get_settings
from .db import get_db

DEV_FIGMA_USER_ID = "__dev__"


def settings_dep() -> Settings:
    return get_settings()


def _dev_user(conn: sqlite3.Connection) -> sqlite3.Row:
    """DEV_MODE 用のローカルユーザー（Personal Access Token で動かすとき）。"""
    return repository.upsert_user(
        conn, {"id": DEV_FIGMA_USER_ID, "handle": "dev", "email": None}
    )


def get_current_user(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> sqlite3.Row:
    """セッション cookie からログイン中のユーザーを取り出す。"""
    raw_token = request.cookies.get(settings.cookie_name)
    if raw_token:
        user = repository.get_session_user(conn, raw_token)
        if user is not None:
            return user

    if settings.dev_mode and settings.figma_pat:
        return _dev_user(conn)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="ログインしていません。/login から Figma で認証してください。",
    )


async def get_figma_credentials(
    user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> tuple[str, bool]:
    """`(token, personal)` を返す。期限切れなら refresh token で更新する。"""
    if user["figma_user_id"] == DEV_FIGMA_USER_ID and settings.figma_pat:
        return settings.figma_pat, True

    row = repository.get_tokens(conn, user["id"])
    if row is None:
        if settings.dev_mode and settings.figma_pat:
            return settings.figma_pat, True
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Figma のアクセストークンがありません。再ログインしてください。",
        )

    if repository.token_is_expired(row) and row["refresh_token"]:
        try:
            payload = await figma.refresh_access_token(row["refresh_token"])
        except figma.FigmaError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"トークンの更新に失敗しました: {exc.message}",
            ) from exc
        repository.save_tokens(conn, user["id"], payload)
        return payload["access_token"], False

    return row["access_token"], False
