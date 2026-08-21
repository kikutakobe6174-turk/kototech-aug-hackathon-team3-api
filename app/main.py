"""FastAPI アプリのエントリポイント。

起動: `uvicorn app.main:app --reload --port 8000`

エラー応答は必ず `{"error": "..."}` の形にする。
フロントの `src/lib/apiClient.ts` が `error` キーだけを読んで画面に出すため、
FastAPI 既定の `{"detail": ...}` のままだとメッセージが表示されない。
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import get_settings
from .db import init_db
from .routers import advice

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    # どの DB を掴んでいるかはトラブル時に一番効くので、起動時に出しておく
    logger.info("SQLite: %s", get_settings().database_path.resolve())
    yield


settings = get_settings()

def _configure_logging() -> None:
    """日本語のログが化けないようにしてから、root ロガーを設定する。

    - Windows では標準出力の既定が cp932 になり、リダイレクトすると
      ログの日本語が cp932 で書かれてしまう。UTF-8 に固定する。
    - uvicorn は root ロガーにハンドラを付けないため、自前で設定しないと
      アプリ側の logger.info がコンソールに出ない。
    """
    encoding = os.getenv("LOG_ENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding=encoding, errors="backslashreplace")
        except (ValueError, OSError):  # 差し替え済みのストリームなど
            pass

    # basicConfig は既にハンドラがあれば何もしないので、二重設定にはならない。
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )


_configure_logging()

app = FastAPI(
    title="空き家活用アドバイザー API",
    description=(
        "所在地と物件の詳細から、売却 / 賃貸 / 保持を診断して"
        "レポート本文を返す。相場データと本文テンプレートは SQLite に持つ。"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# フロントは Next.js のルートハンドラ経由で叩くため本来 CORS は不要だが、
# ブラウザから直接叩いて確認したいときのために許可オリジンを設定できるようにしておく。
if settings.allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(advice.router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse({"error": _validation_message(exc)}, status_code=400)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("未処理の例外", exc_info=exc)
    return JSONResponse(
        {"error": "サーバー内部でエラーが発生しました。"}, status_code=500
    )


def _validation_message(exc: RequestValidationError) -> str:
    """pydantic のエラーを 1 行の日本語メッセージにする。"""
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(p) for p in error.get("loc", ()) if p != "body")
        parts.append(f"{location or 'body'}: {error.get('msg', '不正な値です')}")
    detail = " / ".join(parts) or "リクエストの形式が正しくありません。"
    return f"入力内容を確認してください（{detail}）"


@app.get("/health", tags=["meta"], summary="ヘルスチェック")
def health() -> dict[str, object]:
    return {"status": "ok", "database": str(settings.database_path)}


@app.get("/", tags=["meta"], include_in_schema=False)
def root() -> dict[str, str]:
    return {"name": "空き家活用アドバイザー API", "docs": "/docs"}
