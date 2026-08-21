from __future__ import annotations

import pytest


@pytest.fixture()
def conn(app_env):
    db = app_env["app.db"]
    db.init_db()
    c = db.connect()
    yield c
    c.close()


def test_schema_tables_exist(conn):
    names = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {
        "users",
        "oauth_tokens",
        "oauth_states",
        "sessions",
        "figma_files",
        "figma_nodes",
    } <= names


def test_foreign_keys_cascade(app_env, conn):
    repository = app_env["app.repository"]
    figma = app_env["app.figma"]
    from tests.conftest import FAKE_FILE

    user = repository.upsert_user(conn, {"id": "1", "handle": "a"})
    structure = figma.build_structure(FAKE_FILE)
    repository.save_structure(conn, user["id"], "KEY", structure)

    assert conn.execute("SELECT COUNT(*) c FROM figma_nodes").fetchone()["c"] == 5

    conn.execute("DELETE FROM users WHERE id = ?", (user["id"],))
    assert conn.execute("SELECT COUNT(*) c FROM figma_files").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM figma_nodes").fetchone()["c"] == 0


def test_save_structure_is_idempotent(app_env, conn):
    repository = app_env["app.repository"]
    figma = app_env["app.figma"]
    from tests.conftest import FAKE_FILE

    user = repository.upsert_user(conn, {"id": "1", "handle": "a"})
    structure = figma.build_structure(FAKE_FILE)
    first = repository.save_structure(conn, user["id"], "KEY", structure)
    second = repository.save_structure(conn, user["id"], "KEY", structure)

    assert first["id"] == second["id"]
    assert conn.execute("SELECT COUNT(*) c FROM figma_files").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM figma_nodes").fetchone()["c"] == 5


def test_upsert_user_updates_profile(app_env, conn):
    repository = app_env["app.repository"]
    repository.upsert_user(conn, {"id": "1", "handle": "old"})
    row = repository.upsert_user(conn, {"id": "1", "handle": "new", "email": "n@x.jp"})

    assert row["handle"] == "new"
    assert row["email"] == "n@x.jp"
    assert conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 1


def test_session_lookup_and_delete(app_env, conn):
    repository = app_env["app.repository"]
    user = repository.upsert_user(conn, {"id": "1", "handle": "a"})

    raw, _ = repository.create_session(conn, user["id"], 24)
    # 生トークンは保存されない（ハッシュのみ）
    stored = conn.execute("SELECT id FROM sessions").fetchone()["id"]
    assert stored != raw
    assert stored == repository.hash_token(raw)

    assert repository.get_session_user(conn, raw)["id"] == user["id"]
    repository.delete_session(conn, raw)
    assert repository.get_session_user(conn, raw) is None


def test_expired_session_is_rejected(app_env, conn):
    repository = app_env["app.repository"]
    user = repository.upsert_user(conn, {"id": "1", "handle": "a"})
    raw, _ = repository.create_session(conn, user["id"], 24)
    conn.execute("UPDATE sessions SET expires_at = '2000-01-01 00:00:00'")

    assert repository.get_session_user(conn, raw) is None


def test_state_is_single_use(app_env, conn):
    repository = app_env["app.repository"]
    state = repository.create_state(conn, "http://localhost:3000/file")

    row = repository.consume_state(conn, state)
    assert row["redirect_to"] == "http://localhost:3000/file"
    assert repository.consume_state(conn, state) is None


def test_query_nodes_filters(app_env, conn):
    repository = app_env["app.repository"]
    figma = app_env["app.figma"]
    from tests.conftest import FAKE_FILE

    user = repository.upsert_user(conn, {"id": "1", "handle": "a"})
    file_row = repository.save_structure(
        conn, user["id"], "KEY", figma.build_structure(FAKE_FILE)
    )

    assert len(repository.query_nodes(conn, file_row["id"], node_type="TEXT")) == 1
    assert len(repository.query_nodes(conn, file_row["id"], parent_node_id="2:1")) == 2
    assert len(repository.query_nodes(conn, file_row["id"], name_like="itl")) == 1
    assert len(repository.query_nodes(conn, file_row["id"], max_depth=1)) == 2
