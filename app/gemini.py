"""Gemini で「活用方法」を生成する。

Interactions API を使う。
  POST https://generativelanguage.googleapis.com/v1beta/interactions
  ヘッダー: x-goog-api-key / Content-Type / Api-Revision
  ボディ  : {"model": ..., "system_instruction": ..., "input": ..., "generation_config": {...}}
  応答    : output_text に本文が入る（無ければ steps を辿る）

参考: https://ai.google.dev/gemini-api/docs/interactions/text-generation

いまのところ結果はサーバーログに出すだけで、フロントへのレスポンスには含めない。
生成内容を目視で確認してから組み込む段階のため。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .advice import Factors, Scores
from .config import get_settings

logger = logging.getLogger(__name__)

INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"

SYSTEM_INSTRUCTION = (
    "あなたは日本の空き家活用に詳しい不動産コンサルタントです。"
    "相談者は不動産の素人で、相続などで空き家を持て余しています。\n"
    "次を必ず守ってください。\n"
    "- 日本の制度（特定空家・管理不全空家の指定、空き家バンク、"
    "被相続人の居住用財産の3000万円特別控除、相続登記の義務化）を踏まえる\n"
    "- 具体的で、明日から着手できる粒度で書く\n"
    "- 金額や利回りは必ず「目安」と明記し、断定しない\n"
    "- 与えられた条件から言えないことは、推測せず「確認が必要」と書く\n"
    "- 専門用語には短い言い換えを添える\n"
    "- 出力は日本語のプレーンテキスト。Markdown の見出し記法は使わない"
)


class GeminiError(RuntimeError):
    """Gemini の呼び出しに失敗したとき。"""


def build_prompt(
    detail: dict[str, Any],
    factors: Factors,
    scores: Scores,
    recommendation: str,
) -> str:
    """物件条件と診断結果から、活用方法を尋ねるプロンプトを組み立てる。"""
    label = {"sell": "売却", "rent": "賃貸", "hold": "保持"}.get(
        recommendation, recommendation
    )

    def line(name: str, value: Any, unit: str = "") -> str:
        return f"- {name}: {value}{unit}" if value not in (None, "") else f"- {name}: 未入力"

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
        "次の空き家について、具体的な活用方法を提案してください。\n\n"
        f"【物件の条件】\n{conditions}\n\n"
        f"【エリアの参考値】\n{area_stats}\n"
        "※ これらの参考値は簡易モデルの概算であり、公的統計の実測値ではありません。\n\n"
        "【こちらの簡易診断の結果】\n"
        f"- 最有力: {label}\n"
        f"- スコア: 売却 {scores.sell} / 賃貸 {scores.rent} / 保持 {scores.hold}\n\n"
        "【依頼】\n"
        "活用方法を 3〜5 個挙げてください。診断結果に引きずられすぎず、"
        "条件から見て有望なものを選んでください。それぞれ次の形式で書いてください。\n\n"
        "■ 活用方法の名前\n"
        "  概要: （2〜3 文）\n"
        "  想定初期費用: （目安。根拠となる前提も一言）\n"
        "  期待できる効果: （収益・維持費削減・税負担など）\n"
        "  向いている条件: \n"
        "  注意点・リスク: \n"
        "  最初の一歩: （誰に何を相談するか）\n\n"
        "最後に「確認すべきこと」として、"
        "判断のために追加で調べるべき項目を箇条書きで 3 つ挙げてください。"
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
        containers = [step.get("content"), step]
        for container in containers:
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
    """Gemini に問い合わせて本文を返す。"""
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
        raise GeminiError(f"Gemini がエラーを返しました ({res.status_code}): {res.text[:500]}")

    try:
        payload = res.json()
    except ValueError as exc:
        raise GeminiError(f"応答が JSON ではありません: {res.text[:200]}") from exc

    return _extract_text(payload)


async def log_utilization_ideas(
    detail: dict[str, Any],
    factors: Factors,
    scores: Scores,
    recommendation: str,
) -> None:
    """活用方法を生成してサーバーログに出す。

    バックグラウンドで動かす前提。ここで例外を投げてもリクエストには影響しないが、
    握りつぶさずに必ずログへ残す。
    """
    settings = get_settings()
    area = f"{factors.prefecture}{factors.city}"
    if not settings.gemini_enabled:
        logger.info(
            "[Gemini] スキップ（GEMINI_API_KEY が未設定）: %s / 判定=%s",
            area,
            recommendation,
        )
        return

    prompt = build_prompt(detail, factors, scores, recommendation)
    logger.debug("[Gemini] プロンプト:\n%s", prompt)

    try:
        text = await generate(prompt)
    except GeminiError as exc:
        logger.warning("[Gemini] 生成に失敗: %s / %s", area, exc)
        return
    except Exception:  # pragma: no cover - 想定外は握りつぶさず記録する
        logger.exception("[Gemini] 想定外のエラー: %s", area)
        return

    logger.info(
        "\n"
        "========== Gemini 活用方法 ==========\n"
        "対象: %s / 判定: %s / モデル: %s\n"
        "-------------------------------------\n"
        "%s\n"
        "=====================================",
        area,
        recommendation,
        settings.gemini_model,
        text,
    )
