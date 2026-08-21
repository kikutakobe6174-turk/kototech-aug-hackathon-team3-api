from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest


def _load_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """テストごとに使い捨ての SQLite を指す設定でアプリを読み込み直す。"""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("FIGMA_CLIENT_ID", "test-client")
    monkeypatch.setenv("FIGMA_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("FIGMA_REDIRECT_URI", "http://localhost:8000/callback")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000")
    monkeypatch.setenv("FILE_CACHE_TTL_SECONDS", "300")
    monkeypatch.setenv("DEV_MODE", os.getenv("DEV_MODE", "false"))
    if os.getenv("DEV_MODE", "false") != "true":
        monkeypatch.delenv("FIGMA_PERSONAL_ACCESS_TOKEN", raising=False)

    from app import config

    config.get_settings.cache_clear()

    modules = {}
    for name in (
        "app.config",
        "app.db",
        "app.figma",
        "app.repository",
        "app.deps",
        "app.models",
        "app.routers.auth",
        "app.routers.figma",
        "app.main",
    ):
        modules[name] = importlib.reload(importlib.import_module(name))

    yield modules
    config.get_settings.cache_clear()


@pytest.fixture()
def app_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    yield from _load_app(tmp_path, monkeypatch)


@pytest.fixture()
def client(app_env):
    from fastapi.testclient import TestClient

    with TestClient(app_env["app.main"].app) as c:
        yield c


@pytest.fixture()
def dev_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """DEV_MODE + Personal Access Token でログイン不要にした状態。"""
    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.setenv("FIGMA_PERSONAL_ACCESS_TOKEN", "figd_test")
    yield from _load_app(tmp_path, monkeypatch)


@pytest.fixture()
def dev_client(dev_env):
    from fastapi.testclient import TestClient

    with TestClient(dev_env["app.main"].app) as c:
        yield c


@pytest.fixture()
def logged_in(app_env, client):
    """DB に直接ユーザーとセッションを作り、cookie を仕込む。"""
    db = app_env["app.db"]
    repository = app_env["app.repository"]

    conn = db.connect()
    try:
        user = repository.upsert_user(
            conn, {"id": "1234", "handle": "tester", "email": "t@example.com"}
        )
        repository.save_tokens(
            conn,
            user["id"],
            {"access_token": "tok", "refresh_token": "ref", "expires_in": 3600},
        )
        raw, _ = repository.create_session(conn, user["id"], 24)
    finally:
        conn.close()

    client.cookies.set(os.getenv("SESSION_COOKIE_NAME", "session"), raw)
    return {"user_id": user["id"], "token": raw}


FAKE_FILE = {
    "name": "Hackathon UI",
    "version": "42",
    "lastModified": "2026-08-20T10:00:00Z",
    "thumbnailUrl": "https://example.com/thumb.png",
    "role": "owner",
    "editorType": "figma",
    "document": {
        "id": "0:0",
        "name": "Document",
        "type": "DOCUMENT",
        "children": [
            {
                "id": "1:1",
                "name": "Page 1",
                "type": "CANVAS",
                "children": [
                    {
                        "id": "2:1",
                        "name": "Login",
                        "type": "FRAME",
                        "absoluteBoundingBox": {
                            "x": 0,
                            "y": 0,
                            "width": 375,
                            "height": 812,
                        },
                        "layoutMode": "VERTICAL",
                        "itemSpacing": 16,
                        "fills": [
                            {"type": "SOLID", "color": {"r": 1, "g": 1, "b": 1, "a": 1}}
                        ],
                        "children": [
                            {
                                "id": "2:2",
                                "name": "Title",
                                "type": "TEXT",
                                "characters": "Figma to JSON",
                                "style": {"fontFamily": "Inter", "fontSize": 40},
                                "fills": [
                                    {
                                        "type": "SOLID",
                                        "color": {"r": 0, "g": 0, "b": 0, "a": 1},
                                    }
                                ],
                            },
                            {"id": "2:3", "name": "CTA", "type": "RECTANGLE"},
                        ],
                    }
                ],
            }
        ],
    },
    "components": {},
    "componentSets": {},
    "styles": {},
}
