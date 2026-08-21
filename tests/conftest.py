from __future__ import annotations

import importlib
from pathlib import Path

import pytest

# フロント `src/lib/sections.ts` の ADVICE_SECTIONS と同じ id・同じ並び。
# ここがずれると画面がプレースホルダのままになるので、テストで固定する。
FRONTEND_SECTION_IDS = (
    "summary",
    "sell",
    "rent",
    "hold",
    "market",
    "cost",
    "risk",
    "next",
)

# フロント `src/lib/adviceRequest.ts` が組み立てる形。
SAMPLE_REQUEST = {
    "prefecture": "京都府",
    "city": "京都市中京区",
    "detail": {
        "tsubo": 35,
        "built_years": 40,
        "structure": "木造",
        "property_type": "戸建て",
        "floors": "2階建て",
        "parking": "あり（1台）",
    },
}


@pytest.fixture()
def app_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """テストごとに使い捨ての SQLite を指す設定でアプリを読み込み直す。"""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000")
    monkeypatch.setenv("SAVE_HISTORY", "true")
    monkeypatch.setenv("HISTORY_LIMIT", "100")
    # 実際の .env に GEMINI_API_KEY があってもテストが外へ出ていかないようにする
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")

    from app import config

    config.get_settings.cache_clear()

    modules = {}
    for name in (
        "app.config",
        "app.db",
        "app.seed",
        "app.advice",
        "app.gemini",
        "app.repository",
        "app.models",
        "app.routers.advice",
        "app.main",
    ):
        modules[name] = importlib.reload(importlib.import_module(name))

    yield modules
    config.get_settings.cache_clear()


@pytest.fixture()
def client(app_env):
    from fastapi.testclient import TestClient

    with TestClient(app_env["app.main"].app) as c:
        yield c


@pytest.fixture()
def conn(app_env):
    db = app_env["app.db"]
    db.init_db()
    c = db.connect()
    yield c
    c.close()


@pytest.fixture()
def factors(app_env):
    """テスト用に係数を直接組み立てるヘルパ。"""
    advice = app_env["app.advice"]

    def build(**overrides):
        base = dict(
            prefecture="京都府",
            city="京都市中京区",
            land_price_per_tsubo=240,
            rent_per_tsubo=7600,
            rent_demand=0.90,
            population_trend=0.15,
            vacancy_rate=0.11,
            structure_name="木造",
            legal_life_years=22,
            build_cost_per_tsubo=65,
            renovation_cost_per_tsubo=20,
            property_type_name="戸建て",
            sell_weight=1.0,
            rent_weight=1.0,
            hold_weight=1.0,
            rentable=True,
        )
        base.update(overrides)
        return advice.Factors(**base)

    return build
