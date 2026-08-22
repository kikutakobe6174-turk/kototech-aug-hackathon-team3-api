"""活用例の生成（Gemini 連携）のテスト。実際の API は絶対に叩かない。"""

from __future__ import annotations

import asyncio
import logging

import pytest

from tests.conftest import FRONTEND_SECTION_IDS, SAMPLE_REQUEST


# --- プロンプト -------------------------------------------------------------

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
    assert "階層: 未入力" in prompt
    # 参考値が実測値でないことを必ず添える
    assert "公的統計の実測値ではありません" in prompt


def test_system_instruction_forbids_fabricating_real_cases(app_env):
    """「活用例」で実在の事例をでっち上げさせない。"""
    gemini = app_env["app.gemini"]
    assert "実在の事例として書かないこと" in gemini.SYSTEM_INSTRUCTION
    assert "地名・団体名・人名・年月は書かない" in gemini.SYSTEM_INSTRUCTION
    assert "実在の事例ではなく" in gemini.DISCLAIMER


# --- 応答のパース -----------------------------------------------------------

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


# --- generate_usecase -------------------------------------------------------

def test_returns_none_without_api_key(app_env, factors, caplog):
    gemini = app_env["app.gemini"]
    advice = app_env["app.advice"]

    with caplog.at_level(logging.INFO):
        result = asyncio.run(
            gemini.generate_usecase({}, factors(), advice.Scores(1, 2, 3), "hold")
        )

    assert result is None
    assert "GEMINI_API_KEY が未設定" in caplog.text


def test_prepends_disclaimer_and_logs(app_env, factors, caplog, monkeypatch):
    gemini = app_env["app.gemini"]
    advice = app_env["app.advice"]
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    app_env["app.config"].get_settings.cache_clear()

    async def fake_generate(prompt: str) -> str:
        assert "京都府" in prompt
        return "■ 週末貸しの一軒家\n  どんな空き家か: ..."

    monkeypatch.setattr(gemini, "generate", fake_generate)

    with caplog.at_level(logging.INFO):
        body = asyncio.run(
            gemini.generate_usecase(
                {"tsubo": 35}, factors(), advice.Scores(1, 2, 3), "rent"
            )
        )

    assert body.startswith(gemini.DISCLAIMER)
    assert "週末貸しの一軒家" in body
    assert "活用例（生成）" in caplog.text
    app_env["app.config"].get_settings.cache_clear()


def test_failure_returns_none_and_logs(app_env, factors, caplog, monkeypatch):
    gemini = app_env["app.gemini"]
    advice = app_env["app.advice"]
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    app_env["app.config"].get_settings.cache_clear()

    async def boom(prompt: str) -> str:
        raise gemini.GeminiError("429 Too Many Requests")

    monkeypatch.setattr(gemini, "generate", boom)

    with caplog.at_level(logging.WARNING):
        assert (
            asyncio.run(
                gemini.generate_usecase({}, factors(), advice.Scores(1, 2, 3), "sell")
            )
            is None
        )

    assert "生成に失敗" in caplog.text
    assert "429" in caplog.text
    app_env["app.config"].get_settings.cache_clear()


def test_cache_key_is_stable_and_input_sensitive(app_env, factors):
    gemini = app_env["app.gemini"]
    f = factors()

    a = gemini.cache_key({"tsubo": 35}, f, "sell", "m")
    b = gemini.cache_key({"tsubo": 35}, f, "sell", "m")
    assert a == b

    assert gemini.cache_key({"tsubo": 40}, f, "sell", "m") != a
    assert gemini.cache_key({"tsubo": 35}, f, "rent", "m") != a
    assert gemini.cache_key({"tsubo": 35}, f, "sell", "other") != a
    assert gemini.cache_key({"tsubo": 35}, factors(city="舞鶴市"), "sell", "m") != a


# --- エンドポイントとの結合 --------------------------------------------------

def _stub_generate(app_env, monkeypatch, text: str, calls: list[str] | None = None):
    gemini = app_env["app.gemini"]
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    app_env["app.config"].get_settings.cache_clear()

    async def fake_generate(prompt: str) -> str:
        if calls is not None:
            calls.append(prompt)
        return text

    monkeypatch.setattr(gemini, "generate", fake_generate)


def test_usecase_section_uses_generated_text(client, app_env, monkeypatch):
    _stub_generate(app_env, monkeypatch, "■ 蔵をカフェに貸す\n  ...")

    body = client.post("/advice", json=SAMPLE_REQUEST).json()

    assert set(body["sections"]) == set(FRONTEND_SECTION_IDS)
    usecase = body["sections"]["usecase"]
    assert "蔵をカフェに貸す" in usecase
    assert usecase.startswith(app_env["app.gemini"].DISCLAIMER)
    app_env["app.config"].get_settings.cache_clear()


def test_usecase_falls_back_to_template_without_key(client):
    """キーが無くても usecase は空にならない。"""
    body = client.post("/advice", json=SAMPLE_REQUEST).json()

    usecase = body["sections"]["usecase"]
    assert usecase.strip()
    assert "一般的な活用パターン" in usecase
    assert "古家付き土地" in usecase


def test_usecase_falls_back_when_generation_fails(client, app_env, monkeypatch):
    gemini = app_env["app.gemini"]
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    app_env["app.config"].get_settings.cache_clear()

    async def boom(prompt: str) -> str:
        raise gemini.GeminiError("500")

    monkeypatch.setattr(gemini, "generate", boom)

    res = client.post("/advice", json=SAMPLE_REQUEST)
    assert res.status_code == 200, res.text
    assert "一般的な活用パターン" in res.json()["sections"]["usecase"]
    app_env["app.config"].get_settings.cache_clear()


def test_second_request_uses_cache(client, app_env, monkeypatch):
    """同じ条件なら 2 回目は LLM を呼ばない。"""
    calls: list[str] = []
    _stub_generate(app_env, monkeypatch, "■ 駐車場にする\n  ...", calls)

    first = client.post("/advice", json=SAMPLE_REQUEST).json()
    second = client.post("/advice", json=SAMPLE_REQUEST).json()

    assert len(calls) == 1
    assert first["sections"]["usecase"] == second["sections"]["usecase"]

    # 条件が違えば再生成する
    other = dict(SAMPLE_REQUEST, city="舞鶴市")
    client.post("/advice", json=other)
    assert len(calls) == 2
    app_env["app.config"].get_settings.cache_clear()


def test_cache_can_be_disabled(client, app_env, monkeypatch):
    calls: list[str] = []
    monkeypatch.setenv("USECASE_CACHE", "false")
    _stub_generate(app_env, monkeypatch, "■ 駐車場にする", calls)

    client.post("/advice", json=SAMPLE_REQUEST)
    client.post("/advice", json=SAMPLE_REQUEST)

    assert len(calls) == 2
    app_env["app.config"].get_settings.cache_clear()
