"""診断ロジック。

スコアリングも本文の組み立ても、DB にも FastAPI にも依存しない純粋な関数として書く。
必要な係数は呼び出し側が SQLite から読んで `Factors` に詰めて渡す。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Recommendation = Literal["sell", "rent", "hold"]

# フロントの `src/lib/sections.ts` と同じ並び。ここを変えるときは向こうも直す。
SECTION_IDS: tuple[str, ...] = (
    "summary",
    "sell",
    "rent",
    "hold",
    "usecase",
    "market",
    "cost",
    "risk",
    "next",
)

RECOMMENDATION_LABEL: dict[str, str] = {
    "sell": "売却",
    "rent": "賃貸",
    "hold": "保持",
}

# 築年数が未入力のときに仮置きする残存価値。
UNKNOWN_REMAINING_RATIO = 0.45

PARKING_BONUS: dict[str, float] = {
    "あり（2台以上）": 8.0,
    "あり（1台）": 5.0,
    "なし": 0.0,
}
FLOORS_BONUS: dict[str, float] = {
    "平屋": 4.0,
    "2階建て": 2.0,
    "3階建て": 0.0,
    "4階建て以上": -2.0,
}


class _Blanks(dict):
    """テンプレートに未知のキーがあっても落とさないための穴埋め。"""

    def __missing__(self, key: str) -> str:  # pragma: no cover - 保険
        return "—"


@dataclass(frozen=True)
class Factors:
    """SQLite から読んだ係数一式。"""

    prefecture: str
    city: str
    land_price_per_tsubo: int
    rent_per_tsubo: int
    rent_demand: float
    population_trend: float
    vacancy_rate: float

    structure_name: str
    legal_life_years: int
    build_cost_per_tsubo: int
    renovation_cost_per_tsubo: int

    property_type_name: str
    sell_weight: float
    rent_weight: float
    hold_weight: float
    rentable: bool

    # 出典ありの参照データ（app/reference_data.py）
    region_name: str = ""
    demolition_cost_per_tsubo: int = 0


@dataclass(frozen=True)
class Scores:
    sell: float
    rent: float
    hold: float

    def best(self) -> Recommendation:
        # 同点なら 売却 > 賃貸 > 保持 の順で決める
        order: tuple[tuple[Recommendation, float], ...] = (
            ("sell", self.sell),
            ("rent", self.rent),
            ("hold", self.hold),
        )
        return max(order, key=lambda pair: pair[1])[0]

    def as_dict(self) -> dict[str, float]:
        return {"sell": self.sell, "rent": self.rent, "hold": self.hold}


# フロント dev_simple が読む 1 本の説明文。セクションとは別扱い。
USAGE_TEMPLATE_ID = "usage"


@dataclass(frozen=True)
class Diagnosis:
    recommendation: Recommendation
    scores: Scores
    sections: dict[str, str]
    usage: str
    context: dict[str, str]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def remaining_ratio(built_years: float | None, legal_life_years: int) -> float:
    """法定耐用年数に対する建物の残存価値（0.05〜1.0）。"""
    if built_years is None:
        return UNKNOWN_REMAINING_RATIO
    if legal_life_years <= 0:
        return UNKNOWN_REMAINING_RATIO
    return _clamp(1.0 - built_years / legal_life_years, 0.05, 1.0)


def score(detail: dict[str, Any], factors: Factors, remaining: float) -> Scores:
    """売却 / 賃貸 / 保持を 0〜100 で採点する。

    重みは「地価が高く古いほど売却」「需要が高く建物が生きているほど賃貸」
    「人口が増えていて空き家が少ないなら保持でも損しにくい」という考え方。
    """
    land_index = min(1.0, factors.land_price_per_tsubo / 200.0)
    demand = factors.rent_demand
    trend = factors.population_trend
    vacancy = factors.vacancy_rate

    sell = (
        40.0
        + land_index * 25.0
        + (1.0 - remaining) * 20.0
        + max(0.0, -trend) * 15.0
        - demand * 10.0
    )
    rent = (
        30.0
        + demand * 30.0
        + remaining * 25.0
        + PARKING_BONUS.get(str(detail.get("parking") or ""), 2.0)
        + FLOORS_BONUS.get(str(detail.get("floors") or ""), 1.0)
        + max(0.0, trend) * 10.0
    )
    hold = (
        38.0
        + max(0.0, trend) * 20.0
        + remaining * 10.0
        + (1.0 - min(1.0, vacancy * 4.0)) * 10.0
        - (1.0 - remaining) * 15.0
    )

    # 広すぎる家は借り手が限られ、狭すぎる土地は単独では売りにくい
    tsubo = detail.get("tsubo")
    if isinstance(tsubo, (int, float)):
        if tsubo >= 60:
            sell += 3.0
            rent -= 3.0
        elif tsubo <= 20:
            sell -= 3.0
            rent += 2.0

    sell *= factors.sell_weight
    rent *= factors.rent_weight
    hold *= factors.hold_weight
    if not factors.rentable:
        rent = 0.0

    return Scores(
        sell=round(_clamp(sell, 0.0, 100.0), 1),
        rent=round(_clamp(rent, 0.0, 100.0), 1),
        hold=round(_clamp(hold, 0.0, 100.0), 1),
    )


# --- 表示用の文字列づくり ---------------------------------------------------

def _man(value: float) -> str:
    """万円。1 億を超えたら億表記にする。"""
    if value >= 10_000:
        return f"約 {value / 10_000:,.2f} 億円"
    return f"約 {round(value):,} 万円"


def _yen(value: float) -> str:
    return f"約 {round(value / 1_000) * 1_000:,} 円/月"


def _trend_text(trend: float) -> str:
    if trend >= 0.25:
        return "増加傾向"
    if trend >= 0.0:
        return "横ばい"
    if trend >= -0.3:
        return "緩やかな減少"
    return "減少が続く見通し"


def _demand_text(demand: float) -> str:
    if demand >= 0.8:
        return "高い"
    if demand >= 0.6:
        return "やや高い"
    if demand >= 0.45:
        return "やや低い"
    return "低い"


def _demolition_verdict(
    sale_as_is: float, sale_after_demolition: float, remaining: float
) -> str:
    """そのまま売るのと解体して売るのの、どちらが手残りで有利かを一言で。"""
    diff = sale_after_demolition - sale_as_is
    if remaining >= 0.5:
        return (
            "建物にまだ価値が残っているため、まずは現況のまま売り出すのが基本です。"
            "解体は買主が見つからなかったときの次の手として考えてください。"
        )
    if diff > 50:
        return (
            f"解体して更地にしたほうが約 {round(diff):,} 万円ぶん手残りが多くなる試算です。"
            "古家付きだと買主が解体費を織り込んで指値をしてくるためです。"
        )
    if diff < -50:
        return (
            f"そのまま売るほうが約 {round(-diff):,} 万円ぶん有利な試算です。"
            "解体費が土地の評価を上回るため、現況のまま売り出すか、"
            "解体費を値引きに回して交渉する形が現実的です。"
        )
    return (
        "そのまま売る場合と解体して売る場合で、手残りに大きな差が出ない試算です。"
        "解体すると買主の幅は広がりますが、費用は先に自己負担になります。"
        "まず現況で査定を取り、反応を見てから判断するのが安全です。"
    )


def build_context(
    detail: dict[str, Any], factors: Factors, remaining: float, scores: Scores
) -> dict[str, str]:
    """テンプレートに差し込む値をまとめる。"""
    tsubo = detail.get("tsubo")
    has_tsubo = isinstance(tsubo, (int, float)) and tsubo > 0
    built_years = detail.get("built_years")

    ctx: dict[str, str] = {
        "prefecture": factors.prefecture,
        "city": factors.city,
        "area": f"{factors.prefecture}{factors.city}",
        "area_region": factors.region_name or factors.prefecture,
        "property_type_text": detail.get("property_type") or "物件",
        "structure_text": factors.structure_name,
        "legal_life": str(factors.legal_life_years),
        "floors_text": detail.get("floors") or "階層は未入力",
        "parking_text": detail.get("parking") or "駐車場の有無は未入力",
        "tsubo_text": f"{tsubo:g} 坪" if has_tsubo else "坪数は未入力",
        "built_text": (
            f"築 {built_years:g} 年"
            if isinstance(built_years, (int, float))
            else "築年数は未入力"
        ),
        "remaining_pct": str(round(remaining * 100)),
        "land_price": f"{factors.land_price_per_tsubo:,}",
        "rent_per_tsubo": f"{factors.rent_per_tsubo:,}",
        "vacancy_pct": f"{factors.vacancy_rate * 100:.0f}",
        "trend_text": _trend_text(factors.population_trend),
        "demand_text": _demand_text(factors.rent_demand),
        "score_sell": f"{scores.sell:.1f}",
        "score_rent": f"{scores.rent:.1f}",
        "score_hold": f"{scores.hold:.1f}",
    }

    if has_tsubo:
        land_value = float(tsubo) * factors.land_price_per_tsubo
        building_value = float(tsubo) * factors.build_cost_per_tsubo * remaining
        sale_price = land_value + building_value * 0.85
        rent_month = float(tsubo) * factors.rent_per_tsubo * (0.55 + 0.45 * remaining)
        renovation = (
            float(tsubo) * factors.renovation_cost_per_tsubo * (1.0 - remaining * 0.6)
        )
        # 住宅用地特例で土地の課税標準は 1/6。固定資産税 1.4% + 都市計画税 0.3%。
        tax_year = (land_value * 0.7 / 6.0 + building_value * 0.7) * 0.017

        # 解体して更地で売る場合。解体費は円/坪なので万円に直す。
        demolition = float(tsubo) * factors.demolition_cost_per_tsubo / 10_000.0
        # 更地は買い手が付きやすく、土地値がそのまま評価される
        sale_after_demolition = land_value - demolition

        ctx.update(
            {
                "land_value_text": _man(land_value),
                "building_value_text": _man(building_value),
                "sale_price_text": _man(sale_price),
                "rent_text": _yen(rent_month),
                "renovation_text": _man(renovation),
                "tax_text": _man(tax_year),
                "demolition_text": _man(demolition),
                "demolition_unit_text": f"{factors.demolition_cost_per_tsubo:,}",
                "sale_after_demolition_text": _man(max(0.0, sale_after_demolition)),
                "demolition_verdict_text": _demolition_verdict(
                    sale_price, sale_after_demolition, remaining
                ),
            }
        )
    else:
        unknown = "坪数を入力すると試算します"
        ctx.update(
            {
                "demolition_text": unknown,
                "demolition_unit_text": f"{factors.demolition_cost_per_tsubo:,}",
                "sale_after_demolition_text": unknown,
                "demolition_verdict_text": (
                    "坪数を入力すると、そのまま売る場合と解体して売る場合を比較します。"
                ),
                "land_value_text": unknown,
                "building_value_text": unknown,
                "sale_price_text": unknown,
                "rent_text": unknown,
                "renovation_text": unknown,
                "tax_text": unknown,
            }
        )
    return ctx


def render_sections(
    templates: dict[str, str], context: dict[str, str], recommendation: Recommendation
) -> dict[str, str]:
    """テンプレートに値を差し込む。フロントの全セクションを必ず埋める。"""
    sections: dict[str, str] = {}
    for section_id in SECTION_IDS:
        body = templates.get(section_id)
        if body is None:
            sections[section_id] = "この項目の診断文はまだ用意されていません。"
            continue
        text = body.format_map(_Blanks(context))
        if section_id == recommendation:
            text = f"【今回の診断ではこの選択肢が最有力です】\n\n{text}"
        sections[section_id] = text
    return sections


def render_usage(templates: dict[str, str], context: dict[str, str]) -> str:
    """推奨アクションを取ったうえでどう活用するか、を 1 本の文章にする。"""
    body = templates.get(USAGE_TEMPLATE_ID)
    if not body:
        return ""
    return body.format_map(_Blanks(context))


def diagnose(
    detail: dict[str, Any], factors: Factors, templates_for: Any
) -> Diagnosis:
    """採点 → 本文組み立てまでを通しで行う。

    `templates_for` は判定結果を受け取ってテンプレート辞書を返す callable
    （SQLite を読む関数を渡す）。
    """
    remaining = remaining_ratio(detail.get("built_years"), factors.legal_life_years)
    scores = score(detail, factors, remaining)
    recommendation = scores.best()
    context = build_context(detail, factors, remaining, scores)
    templates = templates_for(recommendation)
    sections = render_sections(templates, context, recommendation)
    usage = render_usage(templates, context)
    return Diagnosis(
        recommendation=recommendation,
        scores=scores,
        sections=sections,
        usage=usage,
        context=context,
    )
