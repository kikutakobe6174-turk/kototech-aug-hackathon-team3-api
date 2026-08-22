"""起動スクリプトのテスト。"""

from __future__ import annotations

import socket

import pytest

import run


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("1", True), ("on", True), ("false", False), ("", True)],
)
def test_bool_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("RELOAD_TEST", value)
    assert run._bool("RELOAD_TEST", True) is expected


def test_port_is_taken_detects_listener():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert run._port_is_taken("127.0.0.1", port) is True

    # クローズ後は空いている
    assert run._port_is_taken("127.0.0.1", port) is False


def test_main_refuses_when_port_is_taken(monkeypatch, capsys):
    """ポートが埋まっているときは uvicorn を起動せず、理由を出して終わる。"""
    started: list[str] = []

    monkeypatch.setattr(run, "_port_is_taken", lambda host, port: True)
    monkeypatch.setattr(run.uvicorn, "run", lambda *a, **k: started.append("ran"))
    monkeypatch.setenv("PORT", "8000")

    assert run.main() == 1
    assert started == []
    assert "ポート 8000 は既に使われています" in capsys.readouterr().err


def test_main_starts_uvicorn_with_narrow_reload_dirs(monkeypatch):
    """reload の監視対象を app/ に絞る（.venv まで見に行かせない）。"""
    captured: dict[str, object] = {}

    monkeypatch.setattr(run, "_port_is_taken", lambda host, port: False)
    monkeypatch.setattr(run.uvicorn, "run", lambda *a, **k: captured.update(k))

    assert run.main() == 0
    assert captured["reload_dirs"] == ["app"]
    assert "*.db" in captured["reload_excludes"]


def test_reload_excludes_cover_generated_files(monkeypatch, capsys):
    """data/*.db やリポジトリ全体の変更で再起動しない設定であること。

    ここを広げると、git の切り替えやコミットのたびにサーバーが再起動し、
    その最中のリクエストがフロント側で 502 になる。
    """
    monkeypatch.setattr(run, "_port_is_taken", lambda host, port: False)
    captured: dict[str, object] = {}
    monkeypatch.setattr(run.uvicorn, "run", lambda *a, **k: captured.update(k))

    assert run.main() == 0
    # 監視するのは app/ だけ。リポジトリ直下（.venv を含む）は見ない
    assert captured["reload_dirs"] == ["app"]
    excludes = captured["reload_excludes"]
    for pattern in ("*.db", "data/*", ".venv/*", ".git/*"):
        assert pattern in excludes


def test_banner_shows_url_and_reload_scope(monkeypatch, capsys):
    monkeypatch.setattr(run, "_port_is_taken", lambda host, port: False)
    monkeypatch.setattr(run.uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setenv("PORT", "8000")

    run.main()
    out = capsys.readouterr().out
    assert "http://127.0.0.1:8000" in out
    assert "app/ のみ監視" in out
    assert "API_BASE_URL=http://127.0.0.1:8000" in out


def test_banner_reports_reload_disabled(monkeypatch, capsys):
    monkeypatch.setattr(run, "_port_is_taken", lambda host, port: False)
    monkeypatch.setattr(run.uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setenv("RELOAD", "false")

    run.main()
    assert "自動リロード: なし" in capsys.readouterr().out
