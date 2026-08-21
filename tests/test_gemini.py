"""Gemini 連携のテスト。実際の API は絶対に叩かない。"""

from __future__ import annotations

import asyncio
import logging

import pytest


def test_prompt_contains_conditions_and_scores(app_env, factors):
    gemini = app_env["app.gemini"]
    advice = app_env["app.advice"]

    detail = {"tsubo": 35, "built_years": 40, "structure": "木造", "parking": "なし"}
    prompt = gemini.build_prompt(
        detail, factors(), advice.Scores(sell=75.0, rent=66.8, hold=32.9), "sell"
    )

    assert "京都府京都市中京区" in prompt
    assert "35 坪" in prompt
    assert "40 年" in prompt
    assert "木造" in prompt
    assert "売却 75.0" in prompt
    assert "最有力: 売却" in prompt
    # 未入力の項目はその旨を伝える
    assert "階層: 未入力" in prompt
    # 参考値が実測値でないことを必ず添える
    assert "公的統計の実測値ではありません" in prompt


def test_extract_text_prefers_output_text(app_env):
    gemini = app_env["app.gemini"]
    assert gemini._extract_text({"output_text": "  本文  "}) == "本文"


def test_extract_text_falls_back_to_steps(app_env):
    gemini = app_env["app.gemini"]
    payload = {
        "output_text": "",
        "steps": [
            {"content": {"parts": [{"text": "前半"}]}},
            {"parts": [{"text": "後半"}]},
        ],
    }
    assert gemini._extract_text(payload) == "前半\n後半"


def test_extract_text_raises_when_empty(app_env):
    gemini = app_env["app.gemini"]
    with pytest.raises(gemini.GeminiError):
        gemini._extract_text({"steps": []})


def test_generate_requires_api_key(app_env):
    gemini = app_env["app.gemini"]
    with pytest.raises(gemini.GeminiError, match="GEMINI_API_KEY"):
        asyncio.run(gemini.generate("こんにちは"))


def test_logs_skip_without_api_key(app_env, factors, caplog):
    gemini = app_env["app.gemini"]
    advice = app_env["app.advice"]

    with caplog.at_level(logging.INFO):
        asyncio.run(
            gemini.log_utilization_ideas({}, factors(), advice.Scores(1, 2, 3), "hold")
        )

    assert "GEMINI_API_KEY が未設定" in caplog.text


def test_logs_generated_text(app_env, factors, caplog, monkeypatch):
    gemini = app_env["app.gemini"]
    advice = app_env["app.advice"]
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    app_env["app.config"].get_settings.cache_clear()

    async def fake_generate(prompt: str) -> str:
        assert "京都府" in prompt
        return "■ 週末貸しの一軒家\n  概要: ..."

    monkeypatch.setattr(gemini, "generate", fake_generate)

    with caplog.at_level(logging.INFO):
        asyncio.run(
            gemini.log_utilization_ideas(
                {"tsubo": 35}, factors(), advice.Scores(1, 2, 3), "rent"
            )
        )

    assert "Gemini 活用方法" in caplog.text
    assert "週末貸しの一軒家" in caplog.text
    app_env["app.config"].get_settings.cache_clear()


def test_failure_is_logged_and_swallowed(app_env, factors, caplog, monkeypatch):
    """生成に失敗してもリクエスト側に例外を伝播させない。"""
    gemini = app_env["app.gemini"]
    advice = app_env["app.advice"]
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    app_env["app.config"].get_settings.cache_clear()

    async def boom(prompt: str) -> str:
        raise gemini.GeminiError("429 Too Many Requests")

    monkeypatch.setattr(gemini, "generate", boom)

    with caplog.at_level(logging.WARNING):
        asyncio.run(
            gemini.log_utilization_ideas({}, factors(), advice.Scores(1, 2, 3), "sell")
        )

    assert "生成に失敗" in caplog.text
    assert "429" in caplog.text
    app_env["app.config"].get_settings.cache_clear()


def test_advice_endpoint_triggers_generation(client, app_env, monkeypatch, caplog):
    """POST /advice のバックグラウンドタスクとして呼ばれること。"""
    from tests.conftest import SAMPLE_REQUEST

    gemini = app_env["app.gemini"]
    called: list[str] = []

    async def fake_log(detail, factors, scores, recommendation):
        called.append(recommendation)

    monkeypatch.setattr(gemini, "log_utilization_ideas", fake_log)

    res = client.post("/advice", json=SAMPLE_REQUEST)
    assert res.status_code == 200
    # TestClient はバックグラウンドタスクの完了まで待つ
    assert called and called[0] in {"sell", "rent", "hold"}


def test_advice_succeeds_even_if_generation_explodes(client, app_env, monkeypatch):
    """活用方法の生成が落ちても診断レスポンスは 200 のまま。"""
    from tests.conftest import SAMPLE_REQUEST

    gemini = app_env["app.gemini"]

    async def boom(*args, **kwargs):
        raise RuntimeError("想定外")

    monkeypatch.setattr(gemini, "generate", boom)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    app_env["app.config"].get_settings.cache_clear()

    res = client.post("/advice", json=SAMPLE_REQUEST)
    assert res.status_code == 200, res.text
    app_env["app.config"].get_settings.cache_clear()
