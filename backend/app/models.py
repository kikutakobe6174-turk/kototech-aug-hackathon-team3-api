"""リクエスト / レスポンスのスキーマ。

フロント側（`src/lib/types.ts`）の型と 1 対 1 で対応させる。
キー名は snake_case のまま。ここを変えるときは必ずフロントと揃えること。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Recommendation = Literal["sell", "rent", "hold"]


class AdviceRequestDetail(BaseModel):
    """詳細入力タブの内容。未入力の項目はキーごと送られてこない。"""

    model_config = ConfigDict(extra="ignore")

    tsubo: float | None = Field(default=None, gt=0, le=10_000, description="坪数")
    built_years: float | None = Field(
        default=None, ge=0, le=200, description="築年数（年）"
    )
    structure: str | None = Field(default=None, max_length=50, description="造り")
    property_type: str | None = Field(default=None, max_length=50, description="種別")
    floors: str | None = Field(default=None, max_length=50, description="階層")
    parking: str | None = Field(default=None, max_length=50, description="駐車場の有無")


class AdviceRequest(BaseModel):
    """`POST /advice` のボディ。"""

    model_config = ConfigDict(extra="ignore")

    prefecture: str = Field(min_length=1, max_length=20)
    city: str = Field(min_length=1, max_length=50)
    detail: AdviceRequestDetail = Field(default_factory=AdviceRequestDetail)


class AdviceResult(BaseModel):
    """`POST /advice` のレスポンス。フロントの `AdviceResult` と同じ形。"""

    recommendation: Recommendation
    sections: dict[str, str]


class ErrorResponse(BaseModel):
    """エラー時の形。フロントは `error` キーだけを読む。"""

    error: str
