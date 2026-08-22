"""Gemini で「活用例」セクションの本文を生成する。

フロントの `src/lib/sections.ts` にある `usecase`（活用例）に入る文章を作る。
生成できなかったときは `advice_templates` のフォールバック文がそのまま使われるので、
このモジュールは**例外を外に投げない**（`generate_usecase` は None を返す）。

Interactions API を使う。
  POST https://generativelanguage.googleapis.com/v1beta/interactions
  ヘッダー: x-goog-api-key / Content-Type / Api-Revision
  ボディ  : {"model": ..., "system_instruction": ..., "input": ..., "generation_config": {...}}
  応答    : output_text に本文が入る（無ければ steps を辿る）

参考: https://ai.google.dev/gemini-api/docs/interactions/text-generation
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import httpx

from .advice import Factors, Scores
from .config import get_settings

logger = logging.getLogger(__name__)

INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"

# 実在の事例だと誤解させないことを最優先にしている。
# 固有名詞つきの「実例」を作らせると、検証できない情報を断定することになる。
SYSTEM_INSTRUCTION = (
    "あなたは日本の空き家活用に詳しい不動産コンサルタントです。"
    "相談者は不動産の素人で、相続などで空き家を持て余しています。\n"
    "次を必ず守ってください。\n"
    "- 実在の事例として書かないこと。具体的な地名・団体名・人名・年月は書かない。"
    "「こういう条件ならこう活かせる」というモデルケースとして書く\n"
    "- 日本の制度（特定空家・管理不全空家の指定、空き家バンク、"
    "被相続人の居住用財産の3000万円特別控除、相続登記の義務化）を踏まえる\n"
    "- 具体的で、明日から着手できる粒度で書く\n"
    "- 金額や利回りは必ず「目安」と明記し、断定しない\n"
    "- 与えられた条件から言えないことは、推測せず「確認が必要」と書く\n"
    "- 専門用語には短い言い換えを添える\n"
    "- 出力は日本語のプレーンテキスト。Markdown の記法（#, *, -, ```）は使わない"
)

# 生成文の先頭に必ず付ける断り書き。モデルの出力に任せず、こちらで固定する。
DISCLAIMER = (
    "※ 以下は実在の事例ではなく、条件が近い空き家で取りうる活用のモデルケースです。"
    "金額はいずれも目安で、実際の費用や収益は個別の条件で変わります。"
)


class GeminiError(RuntimeError):
    """Gemini の呼び出しに失敗したとき。"""


def cache_key(
    detail: dict[str, Any], factors: Factors, recommendation: str, model: str
) -> str:
    """同じ入力なら同じキーになるようにする。"""
    payload = json.dumps(
        {
            "prefecture": factors.prefecture,
            "city": factors.city,
            "detail": detail,
            "recommendation": recommendation,
            "model": model,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_prompt(
    detail: dict[str, Any],
    factors: Factors,
    scores: Scores,
    recommendation: str,
) -> str:
    """物件条件と診断結果から、活用例を尋ねるプロンプトを組み立てる。"""
    label = {"sell": "売却", "rent": "賃貸", "hold": "保持"}.get(
        recommendation, recommendation
    )

    def line(name: str, value: Any, unit: str = "") -> str:
        if value in (None, ""):
            return f"- {name}: 未入力"
        return f"- {name}: {value}{unit}"

    conditions = "\n".join(
        [
            line("所在地", f"{factors.prefecture}{factors.city}"),
            line("坪数", detail.get("tsubo"), " 坪"),
            line("築年数", detail.get("built_years"), " 年"),
            line("造り", detail.get("structure")),
            line("種別", detail.get("property_type")),
            line("階層", detail.get("floors")),
            line("駐車場", detail.get("parking")),
        ]
    )
    area_stats = "\n".join(
        [
            f"- 土地の坪単価の目安: 約 {factors.land_price_per_tsubo:,} 万円",
            f"- 賃貸の坪単価の目安: 約 {factors.rent_per_tsubo:,} 円/月",
            f"- 賃貸需要: {factors.rent_demand:.2f}（0〜1）",
            f"- 人口動態: {factors.population_trend:+.2f}（-1〜1）",
            f"- 空き家率: {factors.vacancy_rate * 100:.0f}%",
        ]
    )

    return (
        "次の空き家について、「活用例」として読ませる文章を書いてください。\n\n"
        f"【物件の条件】\n{conditions}\n\n"
        f"【エリアの参考値】\n{area_stats}\n"
        "※ これらの参考値は簡易モデルの概算であり、公的統計の実測値ではありません。\n\n"
        "【こちらの簡易診断の結果】\n"
        f"- 最有力: {label}\n"
        f"- スコア: 売却 {scores.sell} / 賃貸 {scores.rent} / 保持 {scores.hold}\n\n"
        "【依頼】\n"
        "活用のモデルケースを 3〜4 個挙げてください。"
        "診断結果に引きずられすぎず、条件から見て現実的なものを選んでください。"
        "それぞれ次の形式で、この順番どおりに書いてください。\n\n"
        "■ 活用例のタイトル\n"
        "  どんな空き家か: （条件の要約を 1 文）\n"
        "  やったこと: （2〜3 文）\n"
        "  かかった費用の目安: （前提も一言添える）\n"
        "  その後どうなったか: （収益・維持費・税負担の変化）\n"
        "  この物件で試すなら: （最初に誰に何を相談するか）\n\n"
        "最後に「この物件で確認すべきこと」として、"
        "判断のために追加で調べるべき項目を 3 つ、箇条書きで挙げてください。"
    )


def _extract_text(payload: dict[str, Any]) -> str:
    """output_text を優先し、無ければ steps を辿ってテキストを集める。"""
    text = payload.get("output_text")
    if isinstance(text, str) and text.strip():
        return text.strip()

    collected: list[str] = []
    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        # content.parts[].text / parts[].text のどちらの形でも拾えるようにする
        for container in (step.get("content"), step):
            if not isinstance(container, dict):
                continue
            for part in container.get("parts") or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    collected.append(part["text"])
    if collected:
        return "\n".join(collected).strip()

    raise GeminiError(
        "応答からテキストを取り出せませんでした: "
        f"{json.dumps(payload, ensure_ascii=False)[:500]}"
    )


async def generate(prompt: str) -> str:
    """Gemini に問い合わせて本文を返す。失敗時は GeminiError。"""
    settings = get_settings()
    if not settings.gemini_api_key:
        raise GeminiError("GEMINI_API_KEY が未設定です。")

    body = {
        "model": settings.gemini_model,
        "system_instruction": SYSTEM_INSTRUCTION,
        "input": prompt,
        "generation_config": {"temperature": 0.7},
    }
    headers = {
        "x-goog-api-key": settings.gemini_api_key,
        "Content-Type": "application/json",
        "Api-Revision": settings.gemini_api_revision,
    }

    async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
        try:
            res = await client.post(INTERACTIONS_URL, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise GeminiError(f"Gemini に接続できませんでした: {exc}") from exc

    if not res.is_success:
        raise GeminiError(
            f"Gemini がエラーを返しました ({res.status_code}): {res.text[:500]}"
        )

    try:
        payload = res.json()
    except ValueError as exc:
        raise GeminiError(f"応答が JSON ではありません: {res.text[:200]}") from exc

    return _extract_text(payload)


def _log_result(area: str, recommendation: str, model: str, text: str, source: str) -> None:
    logger.info(
        "\n"
        "========== 活用例（%s） ==========\n"
        "対象: %s / 判定: %s / モデル: %s\n"
        "-----------------------------------\n"
        "%s\n"
        "===================================",
        source,
        area,
        recommendation,
        model,
        text,
    )


async def generate_usecase(
    detail: dict[str, Any],
    factors: Factors,
    scores: Scores,
    recommendation: str,
) -> str | None:
    """「活用例」の本文を返す。生成できなければ None（呼び出し側がフォールバック）。

    ここでは例外を外へ出さない。活用例が出せないだけで診断全体を失敗させたくないため。
    """
    settings = get_settings()
    area = f"{factors.prefecture}{factors.city}"

    if not settings.gemini_enabled:
        logger.info(
            "[Gemini] スキップ（GEMINI_API_KEY が未設定）: %s / 判定=%s",
            area,
            recommendation,
        )
        return None

    prompt = build_prompt(detail, factors, scores, recommendation)
    logger.debug("[Gemini] プロンプト:\n%s", prompt)

    try:
        text = await generate(prompt)
    except GeminiError as exc:
        logger.warning("[Gemini] 生成に失敗: %s / %s", area, exc)
        return None
    except Exception:  # pragma: no cover - 想定外は握りつぶさず記録する
        logger.exception("[Gemini] 想定外のエラー: %s", area)
        return None

    body = f"{DISCLAIMER}\n\n{text}"
    _log_result(area, recommendation, settings.gemini_model, body, "生成")
    return body


def log_cached(area: str, recommendation: str, model: str, text: str) -> None:
    """キャッシュを使ったときもログには残す。"""
    _log_result(area, recommendation, model, text, "キャッシュ")
