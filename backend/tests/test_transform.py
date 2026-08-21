from __future__ import annotations

from app import figma
from tests.conftest import FAKE_FILE


def test_color_to_hex():
    assert figma._to_hex({"r": 1, "g": 0, "b": 0, "a": 1}) == "#FF0000"
    assert figma._to_hex({"r": 0, "g": 0, "b": 0, "a": 0.5}) == "#00000080"
    assert figma._to_hex(None) is None
    assert figma._to_hex({"r": "x"}) is None


def test_build_structure_keeps_tree_and_text():
    structure = figma.build_structure(FAKE_FILE)
    assert structure["name"] == "Hackathon UI"

    page = structure["document"]["children"][0]
    frame = page["children"][0]
    assert frame["type"] == "FRAME"
    assert frame["layout"]["box"] == {"x": 0, "y": 0, "width": 375, "height": 812}
    assert frame["layout"]["layoutMode"] == "VERTICAL"
    assert frame["fills"][0]["color"] == "#FFFFFF"

    title = frame["children"][0]
    assert title["characters"] == "Figma to JSON"
    assert title["textStyle"] == {"fontFamily": "Inter", "fontSize": 40}


def test_max_depth_truncates():
    structure = figma.build_structure(FAKE_FILE, max_depth=2)
    frame = structure["document"]["children"][0]["children"][0]
    assert frame["truncated"] is True
    assert frame["childCount"] == 2
    assert "children" not in frame


def test_flatten_preserves_order_and_parents():
    structure = figma.build_structure(FAKE_FILE)
    rows = figma.flatten(structure["document"])

    assert [r["node_id"] for r in rows] == ["0:0", "1:1", "2:1", "2:2", "2:3"]
    by_id = {r["node_id"]: r for r in rows}
    assert by_id["0:0"]["parent_node_id"] is None
    assert by_id["2:2"]["parent_node_id"] == "2:1"
    assert by_id["2:3"]["order_index"] == 1
    assert by_id["2:1"]["depth"] == 2
    assert by_id["2:2"]["characters"] == "Figma to JSON"
    # attrs には id/name/type/children/characters 以外が入る
    assert "layout" in by_id["2:1"]["attrs"]
    assert "children" not in by_id["2:1"]["attrs"]
