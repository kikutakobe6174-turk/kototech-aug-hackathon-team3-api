"""Figma REST API クライアントと、ノードツリーの JSON 変換。"""

from __future__ import annotations

from typing import Any

import httpx

from .config import get_settings

FIGMA_API = "https://api.figma.com"
FIGMA_OAUTH_AUTHORIZE = "https://www.figma.com/oauth"
FIGMA_OAUTH_TOKEN = f"{FIGMA_API}/v1/oauth/token"
FIGMA_OAUTH_REFRESH = f"{FIGMA_API}/v1/oauth/refresh"

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class FigmaError(RuntimeError):
    """Figma API がエラーを返したとき。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _raise_for_status(res: httpx.Response) -> dict[str, Any]:
    if res.is_success:
        return res.json()
    try:
        detail = res.json()
        message = detail.get("err") or detail.get("message") or res.text
    except Exception:
        message = res.text or res.reason_phrase
    raise FigmaError(res.status_code, f"Figma API error ({res.status_code}): {message}")


# --- OAuth ----------------------------------------------------------------

def build_authorize_url(state: str) -> str:
    settings = get_settings()
    params = httpx.QueryParams(
        {
            "client_id": settings.figma_client_id,
            "redirect_uri": settings.figma_redirect_uri,
            "scope": settings.figma_scope,
            "state": state,
            "response_type": "code",
        }
    )
    return f"{FIGMA_OAUTH_AUTHORIZE}?{params}"


async def exchange_code(code: str) -> dict[str, Any]:
    """認可コードをアクセストークンに交換する。"""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        res = await client.post(
            FIGMA_OAUTH_TOKEN,
            auth=(settings.figma_client_id, settings.figma_client_secret),
            data={
                "redirect_uri": settings.figma_redirect_uri,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
    return _raise_for_status(res)


async def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        res = await client.post(
            FIGMA_OAUTH_REFRESH,
            auth=(settings.figma_client_id, settings.figma_client_secret),
            data={"refresh_token": refresh_token},
        )
    return _raise_for_status(res)


# --- REST API -------------------------------------------------------------

def _auth_headers(token: str, *, personal: bool = False) -> dict[str, str]:
    # Personal Access Token は X-Figma-Token、OAuth は Bearer。
    return {"X-Figma-Token": token} if personal else {"Authorization": f"Bearer {token}"}


async def get_me(token: str, *, personal: bool = False) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        res = await client.get(
            f"{FIGMA_API}/v1/me", headers=_auth_headers(token, personal=personal)
        )
    return _raise_for_status(res)


async def get_file(
    token: str,
    file_key: str,
    *,
    personal: bool = False,
    depth: int | None = None,
    geometry: bool = False,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if depth is not None:
        params["depth"] = depth
    if geometry:
        params["geometry"] = "paths"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        res = await client.get(
            f"{FIGMA_API}/v1/files/{file_key}",
            headers=_auth_headers(token, personal=personal),
            params=params or None,
        )
    return _raise_for_status(res)


# --- JSON 変換 -------------------------------------------------------------

def _to_hex(color: dict[str, Any] | None) -> str | None:
    """Figma の 0..1 float RGBA を #RRGGBB / #RRGGBBAA に変換する。"""
    if not color:
        return None
    try:
        r, g, b = (round(float(color[c]) * 255) for c in ("r", "g", "b"))
    except (KeyError, TypeError, ValueError):
        return None
    a = float(color.get("a", 1))
    hex_rgb = f"#{r:02X}{g:02X}{b:02X}"
    return hex_rgb if a >= 0.999 else f"{hex_rgb}{round(a * 255):02X}"


def _paints(paints: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(paints, list):
        return out
    for paint in paints:
        if not isinstance(paint, dict) or paint.get("visible") is False:
            continue
        entry: dict[str, Any] = {"type": paint.get("type")}
        color = _to_hex(paint.get("color"))
        if color:
            entry["color"] = color
        if paint.get("opacity") is not None:
            entry["opacity"] = paint["opacity"]
        stops = paint.get("gradientStops")
        if isinstance(stops, list) and stops:
            entry["gradientStops"] = [
                {"position": s.get("position"), "color": _to_hex(s.get("color"))}
                for s in stops
                if isinstance(s, dict)
            ]
        out.append(entry)
    return out


def _layout(node: dict[str, Any]) -> dict[str, Any]:
    layout: dict[str, Any] = {}
    box = node.get("absoluteBoundingBox")
    if isinstance(box, dict):
        layout["box"] = {
            k: box.get(k) for k in ("x", "y", "width", "height") if box.get(k) is not None
        }
    for key in (
        "layoutMode",
        "layoutAlign",
        "layoutGrow",
        "primaryAxisAlignItems",
        "counterAxisAlignItems",
        "itemSpacing",
        "paddingLeft",
        "paddingRight",
        "paddingTop",
        "paddingBottom",
        "cornerRadius",
        "clipsContent",
    ):
        if node.get(key) is not None:
            layout[key] = node[key]
    radii = node.get("rectangleCornerRadii")
    if isinstance(radii, list):
        layout["rectangleCornerRadii"] = radii
    return layout


def _text_style(node: dict[str, Any]) -> dict[str, Any]:
    style = node.get("style")
    if not isinstance(style, dict):
        return {}
    keys = (
        "fontFamily",
        "fontPostScriptName",
        "fontWeight",
        "fontSize",
        "lineHeightPx",
        "letterSpacing",
        "textAlignHorizontal",
        "textAlignVertical",
        "textCase",
        "textDecoration",
    )
    return {k: style[k] for k in keys if style.get(k) is not None}


def simplify_node(
    node: dict[str, Any], *, max_depth: int = 100, _depth: int = 0
) -> dict[str, Any]:
    """Figma の生ノードを、扱いやすい構造体に落とす。"""
    simplified: dict[str, Any] = {
        "id": node.get("id"),
        "name": node.get("name"),
        "type": node.get("type"),
    }
    if node.get("visible") is False:
        simplified["visible"] = False
    if node.get("opacity") is not None:
        simplified["opacity"] = node["opacity"]

    layout = _layout(node)
    if layout:
        simplified["layout"] = layout

    fills = _paints(node.get("fills"))
    if fills:
        simplified["fills"] = fills
    strokes = _paints(node.get("strokes"))
    if strokes:
        simplified["strokes"] = strokes
        if node.get("strokeWeight") is not None:
            simplified["strokeWeight"] = node["strokeWeight"]

    if node.get("type") == "TEXT":
        if node.get("characters") is not None:
            simplified["characters"] = node["characters"]
        text_style = _text_style(node)
        if text_style:
            simplified["textStyle"] = text_style

    for key in ("componentId", "componentSetId"):
        if node.get(key):
            simplified[key] = node[key]

    children = node.get("children")
    if isinstance(children, list) and children:
        if _depth >= max_depth:
            simplified["truncated"] = True
            simplified["childCount"] = len(children)
        else:
            simplified["children"] = [
                simplify_node(child, max_depth=max_depth, _depth=_depth + 1)
                for child in children
                if isinstance(child, dict)
            ]
    return simplified


def build_structure(
    file_payload: dict[str, Any], *, max_depth: int = 100
) -> dict[str, Any]:
    """`GET /v1/files/:key` のレスポンス全体を、フロントに返す構造体にする。"""
    document = file_payload.get("document") or {}
    return {
        "name": file_payload.get("name"),
        "version": file_payload.get("version"),
        "lastModified": file_payload.get("lastModified"),
        "thumbnailUrl": file_payload.get("thumbnailUrl"),
        "role": file_payload.get("role"),
        "editorType": file_payload.get("editorType"),
        "document": simplify_node(document, max_depth=max_depth),
        "components": file_payload.get("components") or {},
        "componentSets": file_payload.get("componentSets") or {},
        "styles": file_payload.get("styles") or {},
    }


def flatten(root: dict[str, Any]) -> list[dict[str, Any]]:
    """変換済みツリーを figma_nodes の行に展開する（深さ優先・元の並び順を保持）。"""
    rows: list[dict[str, Any]] = []
    stack: list[tuple[dict[str, Any], str | None, int, int]] = [(root, None, 0, 0)]
    while stack:
        node, parent_id, depth, order = stack.pop()
        attrs = {
            k: v
            for k, v in node.items()
            if k not in {"id", "name", "type", "children", "characters"}
        }
        rows.append(
            {
                "node_id": node.get("id"),
                "parent_node_id": parent_id,
                "name": node.get("name"),
                "type": node.get("type"),
                "depth": depth,
                "order_index": order,
                "characters": node.get("characters"),
                "attrs": attrs,
            }
        )
        children = node.get("children") or []
        # 逆順に積むことで、pop したときに元の並び順で処理される
        for index in range(len(children) - 1, -1, -1):
            stack.append((children[index], node.get("id"), depth + 1, index))
    return rows
