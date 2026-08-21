"""リクエスト / レスポンスのスキーマ。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FigmaFileRequest(BaseModel):
    """`POST /api/figma` のボディ。フロントは `{ "fileKey": "..." }` を送る。"""

    model_config = ConfigDict(populate_by_name=True)

    file_key: str = Field(alias="fileKey", min_length=1, max_length=128)
    refresh: bool = False
    max_depth: int | None = Field(default=None, alias="maxDepth", ge=1, le=200)


class UserOut(BaseModel):
    id: int
    figma_user_id: str = Field(serialization_alias="figmaUserId")
    handle: str | None = None
    email: str | None = None
    img_url: str | None = Field(default=None, serialization_alias="imgUrl")

    model_config = ConfigDict(populate_by_name=True)


class FigmaFileResponse(BaseModel):
    """`POST /api/figma` のレスポンス。"""

    model_config = ConfigDict(populate_by_name=True)

    file_key: str = Field(serialization_alias="fileKey")
    name: str | None = None
    version: str | None = None
    last_modified: str | None = Field(default=None, serialization_alias="lastModified")
    thumbnail_url: str | None = Field(default=None, serialization_alias="thumbnailUrl")
    node_count: int = Field(default=0, serialization_alias="nodeCount")
    fetched_at: str | None = Field(default=None, serialization_alias="fetchedAt")
    cached: bool = False
    structure: dict[str, Any] = Field(default_factory=dict)


class FigmaFileSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_key: str = Field(serialization_alias="fileKey")
    name: str | None = None
    version: str | None = None
    last_modified: str | None = Field(default=None, serialization_alias="lastModified")
    thumbnail_url: str | None = Field(default=None, serialization_alias="thumbnailUrl")
    node_count: int = Field(default=0, serialization_alias="nodeCount")
    fetched_at: str | None = Field(default=None, serialization_alias="fetchedAt")


class NodeOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    node_id: str = Field(serialization_alias="nodeId")
    parent_node_id: str | None = Field(default=None, serialization_alias="parentNodeId")
    name: str | None = None
    type: str | None = None
    depth: int
    order_index: int = Field(serialization_alias="orderIndex")
    characters: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)


class TypeCount(BaseModel):
    type: str | None = None
    count: int
