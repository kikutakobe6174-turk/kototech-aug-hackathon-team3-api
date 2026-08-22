"""診断エンドポイント。

フロント（Next.js のルートハンドラ `src/app/api/advice/route.ts`）が
`POST {API_BASE_URL}/advice` に JSON をそのまま中継してくる。
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import advice as advice_logic
from .. import gemini, repository
from ..config import Settings, get_settings
from ..db import get_db
from ..models import AdviceRequest, AdviceResult

router = APIRouter(tags=["advice"])


def _build_factors(
    conn: sqlite3.Connection, body: AdviceRequest
) -> advice_logic.Factors:
    region = repository.find_region(conn, body.prefecture, body.city)
    if region is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"「{body.prefecture}」の相場データがありません。都道府県名をご確認ください。",
        )

    structure = repository.get_structure_factor(conn, body.detail.structure)
    property_type = repository.get_property_type_factor(conn, body.detail.property_type)

    region_name = repository.get_region_name(conn, body.prefecture)
    demolition = repository.get_demolition_cost(conn, region_name, structure["structure"])

    return advice_logic.Factors(
        prefecture=body.prefecture,
        city=body.city,
        land_price_per_tsubo=region["land_price_per_tsubo"],
        rent_per_tsubo=region["rent_per_tsubo"],
        rent_demand=region["rent_demand"],
        population_trend=region["population_trend"],
        vacancy_rate=region["vacancy_rate"],
        structure_name=structure["structure"],
        legal_life_years=structure["legal_life_years"],
        build_cost_per_tsubo=structure["build_cost_per_tsubo"],
        renovation_cost_per_tsubo=structure["renovation_cost_per_tsubo"],
        property_type_name=property_type["property_type"],
        sell_weight=property_type["sell_weight"],
        rent_weight=property_type["rent_weight"],
        hold_weight=property_type["hold_weight"],
        rentable=bool(property_type["rentable"]),
        region_name=region_name or "",
        demolition_cost_per_tsubo=demolition or 0,
    )


@router.post(
    "/advice",
    response_model=AdviceResult,
    summary="入力条件から売却 / 賃貸 / 保持を診断する",
)
async def create_advice(
    body: AdviceRequest,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AdviceResult:
    detail = body.detail.model_dump(exclude_none=True)
    factors = _build_factors(conn, body)

    result = advice_logic.diagnose(
        detail,
        factors,
        lambda recommendation: repository.get_templates(conn, recommendation),
    )

    sections = dict(result.sections)
    usecase = await _resolve_usecase(
        conn, settings, body, detail, factors, result.scores, result.recommendation
    )
    if usecase:
        sections["usecase"] = usecase

    if settings.save_history:
        repository.save_diagnosis(
            conn,
            prefecture=body.prefecture,
            city=body.city,
            detail=detail,
            recommendation=result.recommendation,
            scores=result.scores.as_dict(),
            sections=sections,
        )
        repository.trim_history(conn, settings.history_limit)

    return AdviceResult(recommendation=result.recommendation, sections=sections)


# 判定 → 事例分類の対応。優先的に拾う分類を決めるだけで、絞り込みはしない。
CATEGORY_FOR: dict[str, str] = {
    "rent": "賃貸住宅",
    "hold": "地域拠点",
    "sell": "商業施設",
}


def _load_examples(
    conn: sqlite3.Connection, request: AdviceRequest, factors: advice_logic.Factors
) -> list[dict]:
    """出典のある活用事例を、地域が近いものから選ぶ。"""
    rows = repository.pick_usecase_examples(
        conn,
        prefecture=request.prefecture,
        region=factors.region_name or None,
        category=CATEGORY_FOR.get(factors.property_type_name, None),
    )
    return [dict(row) for row in rows]


async def _resolve_usecase(
    conn: sqlite3.Connection,
    settings: Settings,
    request: AdviceRequest,
    detail: dict,
    factors: advice_logic.Factors,
    scores: advice_logic.Scores,
    recommendation: str,
) -> str | None:
    """「活用例」の本文を、キャッシュ → 生成 → 実例のそのまま表示 の順で用意する。"""
    examples = _load_examples(conn, request, factors)
    key = gemini.cache_key(detail, factors, recommendation, settings.gemini_model)

    if settings.usecase_cache_enabled:
        cached = repository.get_cached_usecase(conn, key)
        if cached is not None:
            gemini.log_cached(
                f"{factors.prefecture}{factors.city}",
                recommendation,
                cached["model"],
                cached["body"],
            )
            return cached["body"]

    body = await gemini.generate_usecase(
        detail, factors, scores, recommendation, examples
    )
    if not body:
        # LLM が使えなくても、出典のある実例はそのまま出す。
        # advice_templates の汎用文まで落ちるのは事例が 1 件も無いときだけ。
        return gemini.format_examples(examples) or None

    if body and settings.usecase_cache_enabled:
        repository.save_usecase(
            conn,
            key=key,
            prefecture=factors.prefecture,
            city=factors.city,
            model=settings.gemini_model,
            body=body,
        )
        repository.trim_usecase_cache(conn, settings.usecase_cache_limit)
    return body


@router.get("/advice/history", summary="診断履歴（動作確認用）")
def advice_history(
    limit: int = Query(default=20, ge=1, le=100),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    return [
        {
            "id": row["id"],
            "prefecture": row["prefecture"],
            "city": row["city"],
            "detail": json.loads(row["detail_json"]),
            "recommendation": row["recommendation"],
            "scores": json.loads(row["scores_json"]),
            "created_at": row["created_at"],
        }
        for row in repository.list_diagnoses(conn, limit)
    ]


@router.get("/regions", summary="登録済みの都道府県一覧（動作確認用）")
def regions(conn: sqlite3.Connection = Depends(get_db)) -> dict[str, list[str]]:
    return {"prefectures": repository.list_prefectures(conn)}
