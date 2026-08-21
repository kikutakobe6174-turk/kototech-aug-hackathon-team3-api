from __future__ import annotations

import copy

import pytest

from tests.conftest import FAKE_FILE


@pytest.fixture()
def fake_figma(app_env, monkeypatch):
    """Figma への HTTP 呼び出しを差し替え、呼ばれた回数を記録する。"""
    calls: list[str] = []

    async def fake_get_file(token, file_key, *, personal=False, depth=None, geometry=False):
        calls.append(file_key)
        return copy.deepcopy(FAKE_FILE)

    monkeypatch.setattr(app_env["app.figma"], "get_file", fake_get_file)
    return calls


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert res.json()["oauthConfigured"] is True


def test_requires_login(client):
    res = client.post("/api/figma", json={"fileKey": "ABC123"})
    assert res.status_code == 401


def test_login_redirects_to_figma(client):
    res = client.get("/login", follow_redirects=False)
    assert res.status_code == 307
    location = res.headers["location"]
    assert location.startswith("https://www.figma.com/oauth?")
    assert "client_id=test-client" in location
    assert "state=" in location


def test_me(client, logged_in):
    res = client.get("/me")
    assert res.status_code == 200
    assert res.json()["handle"] == "tester"


def test_fetch_file_stores_structure_and_nodes(client, logged_in, fake_figma):
    res = client.post("/api/figma", json={"fileKey": "ABC123"})
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["fileKey"] == "ABC123"
    assert body["name"] == "Hackathon UI"
    assert body["cached"] is False
    assert body["nodeCount"] == 5
    assert body["structure"]["document"]["children"][0]["type"] == "CANVAS"
    assert fake_figma == ["ABC123"]

    # 2 回目はキャッシュ（TTL 内）なので Figma を叩かない
    again = client.post("/api/figma", json={"fileKey": "ABC123"})
    assert again.json()["cached"] is True
    assert fake_figma == ["ABC123"]

    # refresh: true で強制的に取り直す
    forced = client.post("/api/figma", json={"fileKey": "ABC123", "refresh": True})
    assert forced.json()["cached"] is False
    assert fake_figma == ["ABC123", "ABC123"]


def test_history_and_nodes_and_stats(client, logged_in, fake_figma):
    client.post("/api/figma", json={"fileKey": "ABC123"})

    history = client.get("/api/figma/history").json()
    assert [h["fileKey"] for h in history] == ["ABC123"]
    assert history[0]["nodeCount"] == 5

    texts = client.get("/api/figma/ABC123/nodes", params={"type": "TEXT"}).json()
    assert len(texts) == 1
    assert texts[0]["characters"] == "Figma to JSON"
    assert texts[0]["parentNodeId"] == "2:1"

    children = client.get("/api/figma/ABC123/nodes", params={"parent": "2:1"}).json()
    assert [c["nodeId"] for c in children] == ["2:2", "2:3"]

    stats = {s["type"]: s["count"] for s in client.get("/api/figma/ABC123/stats").json()}
    assert stats == {"DOCUMENT": 1, "CANVAS": 1, "FRAME": 1, "TEXT": 1, "RECTANGLE": 1}


def test_get_cached_and_delete(client, logged_in, fake_figma):
    client.post("/api/figma", json={"fileKey": "ABC123"})

    cached = client.get("/api/figma/ABC123")
    assert cached.status_code == 200
    assert cached.json()["cached"] is True

    assert client.delete("/api/figma/ABC123").status_code == 204
    assert client.get("/api/figma/ABC123").status_code == 404
    assert client.delete("/api/figma/ABC123").status_code == 404


def test_unknown_file_key_is_404(client, logged_in):
    assert client.get("/api/figma/NOPE/nodes").status_code == 404


def test_figma_error_is_forwarded(client, logged_in, app_env, monkeypatch):
    figma = app_env["app.figma"]

    async def boom(*args, **kwargs):
        raise figma.FigmaError(403, "Invalid token")

    monkeypatch.setattr(figma, "get_file", boom)
    res = client.post("/api/figma", json={"fileKey": "ABC123"})
    assert res.status_code == 403
    assert "Invalid token" in res.json()["detail"]


def test_logout_clears_session(client, logged_in, fake_figma):
    assert client.post("/logout").status_code == 200
    client.cookies.clear()
    assert client.get("/me").status_code == 401


def test_callback_rejects_bad_state(client):
    res = client.get(
        "/callback", params={"code": "x", "state": "nope"}, follow_redirects=False
    )
    assert res.status_code == 400


def test_dev_mode_works_without_login(dev_env, dev_client, monkeypatch):
    """DEV_MODE + Personal Access Token なら OAuth なしで叩ける。"""
    used: list[tuple[str, bool]] = []

    async def fake_get_file(token, file_key, *, personal=False, depth=None, geometry=False):
        used.append((token, personal))
        return copy.deepcopy(FAKE_FILE)

    monkeypatch.setattr(dev_env["app.figma"], "get_file", fake_get_file)

    res = dev_client.post("/api/figma", json={"fileKey": "ABC123"})
    assert res.status_code == 200, res.text
    assert res.json()["nodeCount"] == 5
    # Personal Access Token は X-Figma-Token で送られる
    assert used == [("figd_test", True)]
