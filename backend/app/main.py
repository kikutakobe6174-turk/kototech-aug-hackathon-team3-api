"""FastAPI アプリのエントリポイント。

起動: `uvicorn app.main:app --reload --port 8000`
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import init_db
from .routers import auth, figma


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


settings = get_settings()

app = FastAPI(
    title="Figma to JSON API",
    description="Figma でログインし、file key から JSON 構造体を返す API（保存先は SQLite）。",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=True,  # cookie を送るので必須
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(figma.router)


@app.get("/health", tags=["meta"], summary="ヘルスチェック")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "database": str(settings.database_path),
        "oauthConfigured": settings.oauth_configured,
        "devMode": settings.dev_mode,
    }


@app.get("/", tags=["meta"], include_in_schema=False)
def root() -> dict[str, str]:
    return {"name": "Figma to JSON API", "docs": "/docs"}
