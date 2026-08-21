"""診断ロジック（純関数）のテスト。"""

from __future__ import annotations

import pytest

from tests.conftest import FRONTEND_SECTION_IDS


def test_section_ids_match_frontend(app_env):
    assert app_env["app.advice"].SECTION_IDS == FRONTEND_SECTION_IDS


@pytest.mark.parametrize(
    ("built_years", "expected"),
    [
        (None, 0.45),  # 未入力は仮置き
        (0, 1.0),
        (11, 0.5),
        (22, 0.05),  # 耐用年数ちょうどでも下限で止める
        (60, 0.05),
    ],
)
def test_remaining_ratio(app_env, built_years, expected):
    advice = app_env["app.advice"]
    assert advice.remaining_ratio(built_years, 22) == pytest.approx(expected)


def test_old_house_in_expensive_area_leans_sell(app_env, factors):
    advice = app_env["app.advice"]
    f = factors(land_price_per_tsubo=350, rent_demand=0.95, population_trend=0.45)
    remaining = advice.remaining_ratio(45, f.legal_life_years)

    scores = advice.score({"tsubo": 40}, f, remaining)
    assert scores.best() == "sell"


def test_new_house_with_demand_leans_rent(app_env, factors):
    advice = app_env["app.advice"]
    f = factors(land_price_per_tsubo=95, rent_demand=0.9, population_trend=0.2)
    remaining = advice.remaining_ratio(3, f.legal_life_years)

    scores = advice.score({"tsubo": 30, "parking": "あり（2台以上）"}, f, remaining)
    assert scores.best() == "rent"


def test_land_only_scores_zero_for_rent(app_env, factors):
    advice = app_env["app.advice"]
    f = factors(rentable=False, rent_weight=0.4, sell_weight=1.25)

    scores = advice.score({"tsubo": 50}, f, 0.5)
    assert scores.rent == 0.0
    assert scores.best() != "rent"


def test_parking_and_floors_help_rent(app_env, factors):
    advice = app_env["app.advice"]
    f = factors()
    base = advice.score({"tsubo": 30}, f, 0.6)
    better = advice.score(
        {"tsubo": 30, "parking": "あり（2台以上）", "floors": "平屋"}, f, 0.6
    )
    assert better.rent > base.rent


def test_scores_stay_in_range(app_env, factors):
    advice = app_env["app.advice"]
    extreme = factors(
        land_price_per_tsubo=9999,
        rent_demand=1.0,
        population_trend=1.0,
        vacancy_rate=0.9,
        sell_weight=5.0,
        rent_weight=5.0,
        hold_weight=5.0,
    )
    scores = advice.score({"tsubo": 500}, extreme, 1.0)
    for value in scores.as_dict().values():
        assert 0.0 <= value <= 100.0


def test_ties_prefer_sell_then_rent(app_env):
    advice = app_env["app.advice"]
    assert advice.Scores(sell=50.0, rent=50.0, hold=50.0).best() == "sell"
    assert advice.Scores(sell=10.0, rent=50.0, hold=50.0).best() == "rent"


def test_context_formats_money_and_falls_back(app_env, factors):
    advice = app_env["app.advice"]
    f = factors()
    scores = advice.Scores(sell=1.0, rent=2.0, hold=3.0)

    with_tsubo = advice.build_context({"tsubo": 35}, f, 0.5, scores)
    assert with_tsubo["sale_price_text"].startswith("約 ")
    assert "万円" in with_tsubo["sale_price_text"] or "億円" in with_tsubo["sale_price_text"]
    assert with_tsubo["rent_text"].endswith("円/月")

    without = advice.build_context({}, f, 0.5, scores)
    assert without["sale_price_text"] == "坪数を入力すると試算します"
    assert without["tsubo_text"] == "坪数は未入力"


def test_large_amounts_switch_to_oku(app_env, factors):
    advice = app_env["app.advice"]
    f = factors(land_price_per_tsubo=350)
    ctx = advice.build_context({"tsubo": 400}, f, 1.0, advice.Scores(1.0, 2.0, 3.0))
    assert "億円" in ctx["land_value_text"]


def test_render_sections_fills_every_section(app_env):
    advice = app_env["app.advice"]
    sections = advice.render_sections({}, {}, "sell")

    assert set(sections) == set(FRONTEND_SECTION_IDS)
    assert all(text.strip() for text in sections.values())


def test_render_sections_marks_recommendation(app_env):
    advice = app_env["app.advice"]
    templates = {sid: "本文" for sid in FRONTEND_SECTION_IDS}
    sections = advice.render_sections(templates, {}, "rent")

    assert sections["rent"].startswith("【今回の診断ではこの選択肢が最有力です】")
    assert sections["sell"] == "本文"


def test_render_sections_survives_unknown_placeholder(app_env):
    advice = app_env["app.advice"]
    sections = advice.render_sections({"summary": "{nope} です"}, {}, "sell")
    assert sections["summary"] == "— です"
