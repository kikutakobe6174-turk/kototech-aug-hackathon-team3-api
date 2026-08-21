"""Figma OAuth によるログイン / ログアウト。"""

from __future__ import annotations

import sqlite3
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from .. import figma, repository
from ..config import Settings
from ..db import get_db
from ..deps import get_current_user, settings_dep
from ..models import UserOut

router = APIRouter(tags=["auth"])

VALID_SAMESITE = {"lax", "strict", "none"}


def _set_session_cookie(response: Response, raw_token: str, settings: Settings) -> None:
    samesite = settings.cookie_samesite if settings.cookie_samesite in VALID_SAMESITE else "lax"
    # SameSite=None は Secure 必須（別ドメインにフロントを置く構成向け）
    secure = settings.cookie_secure or samesite == "none"
    response.set_cookie(
        key=settings.cookie_name,
        value=raw_token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=secure,
        samesite=samesite,
        domain=settings.cookie_domain,
        path="/",
    )


def _frontend_redirect(settings: Settings, path: str, **params: str) -> str:
    url = f"{settings.frontend_url}{path}"
    return f"{url}?{urlencode(params)}" if params else url


@router.get("/login", summary="Figma の認可画面へリダイレクトする")
def login(
    redirect_to: str | None = Query(default=None, alias="redirect_to"),
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> RedirectResponse:
    if not settings.oauth_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FIGMA_CLIENT_ID / FIGMA_CLIENT_SECRET が未設定です。",
        )
    # オープンリダイレクト対策: 戻り先はフロントのオリジン配下だけ許す
    if redirect_to and not redirect_to.startswith(f"{settings.frontend_url}/"):
        redirect_to = None
    state = repository.create_state(conn, redirect_to)
    return RedirectResponse(
        figma.build_authorize_url(state), status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )


@router.get("/callback", summary="Figma からのコールバック")
async def callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> RedirectResponse:
    if error:
        return RedirectResponse(_frontend_redirect(settings, "/", error=error))
    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="code / state がありません。"
        )

    state_row = repository.consume_state(conn, state)
    if state_row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="state が不正、または期限切れです。もう一度ログインしてください。",
        )

    try:
        token_payload = await figma.exchange_code(code)
        profile = await figma.get_me(token_payload["access_token"])
    except figma.FigmaError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Figma からアクセストークンを取得できませんでした。",
        ) from exc

    user = repository.upsert_user(conn, profile)
    repository.save_tokens(conn, user["id"], token_payload)
    raw_token, _ = repository.create_session(conn, user["id"], settings.session_ttl_hours)

    target = state_row["redirect_to"] or _frontend_redirect(settings, "/file")
    response = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(response, raw_token, settings)
    return response


@router.post("/logout", summary="ログアウトする")
def logout(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> JSONResponse:
    raw_token = request.cookies.get(settings.cookie_name)
    if raw_token:
        repository.delete_session(conn, raw_token)
    response = JSONResponse({"ok": True})
    response.delete_cookie(
        key=settings.cookie_name, domain=settings.cookie_domain, path="/"
    )
    return response


@router.get("/me", response_model=UserOut, summary="ログイン中のユーザー")
def me(user: sqlite3.Row = Depends(get_current_user)) -> UserOut:
    return UserOut(**{k: user[k] for k in ("id", "figma_user_id", "handle", "email", "img_url")})
