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


def test_system_instruction_restricts_to_given_examples(app_env):
    """固有名詞は渡した参考事例のものだけ。作らせない。"""
    gemini = app_env["app.gemini"]
    assert "【参考事例】に書かれたものだけ" in gemini.SYSTEM_INSTRUCTION
    assert "作ってはいけない" in gemini.SYSTEM_INSTRUCTION
    assert "改変しない" in gemini.SYSTEM_INSTRUCTION


def test_prompt_embeds_reference_examples(app_env, factors):
    gemini = app_env["app.gemini"]
    advice = app_env["app.advice"]
    examples = [
        {
            "title": "尾道空き家再生プロジェクト（尾道市）",
            "prefecture": "広島県",
            "category": "商業施設",
            "summary": "斜面地の空き家をゲストハウスに再生。",
            "numbers": "20件以上を改修",
            "source_name": "自治体通信オンライン",
            "source_url": "https://jichitai.works/articles/3296",
        }
    ]
    prompt = gemini.build_prompt(
        {}, factors(), advice.Scores(1, 2, 3), "sell", examples
    )
    assert "【参考事例】" in prompt
    assert "尾道空き家再生プロジェクト（尾道市）" in prompt
    assert "20件以上を改修" in prompt
    assert "新しく作らないでください" in prompt


def test_format_examples_includes_source(app_env):
    gemini = app_env["app.gemini"]
    examples = [
        {
            "title": "神山プロジェクト（神山町）",
            "prefecture": "徳島県",
            "category": "サテライトオフィス",
            "summary": "空き家をIT企業のサテライトオフィスに転用。",
            "numbers": "16社進出",
            "source_name": "自治体通信オンライン",
            "source_url": "https://jichitai.works/articles/3296",
        }
    ]
    text = gemini.format_examples(examples)
    assert text.startswith(gemini.DISCLAIMER)
    assert "神山プロジェクト（神山町）" in text
    assert "16社進出" in text
    assert "https://jichitai.works/articles/3296" in text
    assert gemini.format_examples([]) == ""


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
            gemini.generate_usage({}, factors(), advice.Scores(1, 2, 3), "hold")
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
            gemini.generate_usage(
                {"tsubo": 35}, factors(), advice.Scores(1, 2, 3), "rent"
            )
        )

    assert "週末貸しの一軒家" in body
    assert "活用方法（生成）" in caplog.text
    # 金額の出どころの断り書きは、モデル任せにせず必ずこちらで付ける
    assert body.rstrip().endswith(gemini.USAGE_NOTE)
    assert "出典:" not in body  # 事例を渡していないので出典行は付かない
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
                gemini.generate_usage({}, factors(), advice.Scores(1, 2, 3), "sell")
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


def test_usage_uses_generated_text(client, app_env, monkeypatch):
    """生成できたら usage に入る（dev_simple のフロントが読む値）。"""
    _stub_generate(app_env, monkeypatch, "蔵をカフェとして貸す方法があります。")

    body = client.post("/advice", json=SAMPLE_REQUEST).json()

    assert "蔵をカフェとして貸す方法があります。" in body["usage"]
    assert body["usage"].rstrip().endswith(
        app_env["app.gemini"].source_note(
            [
                {
                    "source_name": "自治体通信オンライン",
                    "source_url": "https://jichitai.works/articles/3296",
                }
            ]
        ).strip()
    )
    # 活用例セクション（master 用）は実例のまま。生成文で上書きしない
    assert set(body["sections"]) == set(FRONTEND_SECTION_IDS)
    assert "蔵をカフェ" not in body["sections"]["usecase"]
    app_env["app.config"].get_settings.cache_clear()


def test_usage_falls_back_to_template_and_examples(client):
    """キーが無くても usage は空にならない。テンプレート文 + 実例が入る。"""
    body = client.post("/advice", json=SAMPLE_REQUEST).json()
    usage = body["usage"]

    assert usage.strip()
    # 判定に応じたテンプレート文
    assert "京都府京都市中京区" in usage
    # 出典のある実例も続けて出す
    assert "https://jichitai.works/articles/3296" in usage


def test_usecase_shows_real_examples_without_key(client):
    """キーが無くても、出典のある実例をそのまま出す。"""
    usecase = client.post("/advice", json=SAMPLE_REQUEST).json()["sections"]["usecase"]

    assert usecase.strip()
    assert "実際に行われた空き家活用の事例です" in usecase
    assert "https://jichitai.works/articles/3296" in usecase
    assert "出典:" in usecase


def test_usecase_prefers_nearby_examples(client):
    """近畿の物件なら、近畿の事例が先に出る。"""
    kyoto = client.post(
        "/advice", json={"prefecture": "京都府", "city": "京都市中京区", "detail": {}}
    ).json()["sections"]["usecase"]
    hiroshima = client.post(
        "/advice", json={"prefecture": "広島県", "city": "広島市中区", "detail": {}}
    ).json()["sections"]["usecase"]

    # 広島県には尾道の事例があるので、必ず含まれる
    assert "尾道空き家再生プロジェクト（尾道市）" in hiroshima
    assert kyoto != hiroshima


def test_usecase_falls_back_when_generation_fails(client, app_env, monkeypatch):
    gemini = app_env["app.gemini"]
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    app_env["app.config"].get_settings.cache_clear()

    async def boom(prompt: str) -> str:
        raise gemini.GeminiError("500")

    monkeypatch.setattr(gemini, "generate", boom)

    res = client.post("/advice", json=SAMPLE_REQUEST)
    assert res.status_code == 200, res.text
    usecase = res.json()["sections"]["usecase"]
    assert "https://jichitai.works/articles/3296" in usecase
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
