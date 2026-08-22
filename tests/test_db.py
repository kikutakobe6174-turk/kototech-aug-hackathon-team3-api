"""SQLite のスキーマとマスタ投入のテスト。"""

from __future__ import annotations

from tests.conftest import FRONTEND_SECTION_IDS


def test_schema_tables_exist(conn):
    names = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {
        "usecase_cache",
        "regions",
        "structure_factors",
        "property_type_factors",
        "advice_templates",
        "diagnoses",
    } <= names


def test_seed_is_idempotent(app_env, conn):
    """init_db を何度呼んでも行数が増えない。"""
    db = app_env["app.db"]

    def counts():
        return {
            table: conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
            for table in (
                "regions",
                "structure_factors",
                "property_type_factors",
                "advice_templates",
            )
        }

    before = counts()
    db.init_db(conn)
    db.init_db(conn)
    assert counts() == before
    assert before["regions"] == 47 + 12  # 都道府県 + 市区町村の上書き


def test_region_falls_back_to_prefecture(app_env, conn):
    repository = app_env["app.repository"]

    city = repository.find_region(conn, "京都府", "京都市中京区")
    assert city["land_price_per_tsubo"] == 240

    fallback = repository.find_region(conn, "京都府", "宇治市")
    assert fallback["city"] == ""
    assert fallback["land_price_per_tsubo"] == 130

    assert repository.find_region(conn, "架空県", "架空市") is None


def test_structure_and_property_type_fallback(app_env, conn):
    repository = app_env["app.repository"]

    assert repository.get_structure_factor(conn, "木造")["legal_life_years"] == 22
    assert repository.get_structure_factor(conn, None)["structure"] == "その他"
    assert repository.get_structure_factor(conn, "藁")["structure"] == "その他"

    assert repository.get_property_type_factor(conn, "土地のみ")["rentable"] == 0
    assert repository.get_property_type_factor(conn, None)["property_type"] == "戸建て"


def test_templates_cover_every_section(app_env, conn):
    repository = app_env["app.repository"]

    for recommendation in ("sell", "rent", "hold"):
        templates = repository.get_templates(conn, recommendation)
        # セクションに加えて、dev_simple 用の usage テンプレートも入る
        assert set(templates) == set(FRONTEND_SECTION_IDS) | {"usage"}, recommendation


def test_recommendation_specific_template_wins(app_env, conn):
    repository = app_env["app.repository"]

    sell = repository.get_templates(conn, "sell")["summary"]
    rent = repository.get_templates(conn, "rent")["summary"]
    assert "「売却」が最有力" in sell
    assert "「賃貸」が最有力" in rent

    # 共通文（recommendation = 'any'）は判定によらず同じ
    assert repository.get_templates(conn, "sell")["risk"] == (
        repository.get_templates(conn, "hold")["risk"]
    )


def test_history_is_trimmed(app_env, conn):
    repository = app_env["app.repository"]

    for i in range(5):
        repository.save_diagnosis(
            conn,
            prefecture="東京都",
            city=f"市{i}",
            detail={},
            recommendation="sell",
            scores={"sell": 1.0, "rent": 2.0, "hold": 3.0},
            sections={},
        )
    repository.trim_history(conn, 3)

    rows = repository.list_diagnoses(conn, 10)
    assert len(rows) == 3
    assert [r["city"] for r in rows] == ["市4", "市3", "市2"]


# --- 出典ありの参照データ ---------------------------------------------------

def test_reference_tables_are_seeded(app_env, conn):
    counts = {
        t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
        for t in ("prefecture_regions", "demolition_costs", "usecase_examples")
    }
    assert counts["prefecture_regions"] == 47
    assert counts["demolition_costs"] == 6 * 5  # 6 地方 × 5 構造
    assert counts["usecase_examples"] == 15


def test_reference_seed_is_idempotent(app_env, conn):
    db = app_env["app.db"]
    before = conn.execute("SELECT COUNT(*) c FROM usecase_examples").fetchone()["c"]
    db.init_db(conn)
    db.init_db(conn)
    assert conn.execute("SELECT COUNT(*) c FROM usecase_examples").fetchone()["c"] == before


def test_region_mapping(app_env, conn):
    repository = app_env["app.repository"]
    assert repository.get_region_name(conn, "京都府") == "近畿"
    assert repository.get_region_name(conn, "北海道") == "北海道・東北"
    assert repository.get_region_name(conn, "架空県") is None


def test_demolition_cost_lookup(app_env, conn):
    repository = app_env["app.repository"]

    # 木造の地方別は出典の実測値そのまま
    assert repository.get_demolition_cost(conn, "関東", "木造") == 35_270
    assert repository.get_demolition_cost(conn, "中国・四国", "木造") == 29_038

    # 構造が重いほど高い
    kinki_wood = repository.get_demolition_cost(conn, "近畿", "木造")
    kinki_rc = repository.get_demolition_cost(conn, "近畿", "鉄筋コンクリート造")
    assert kinki_rc > kinki_wood

    # 地方が不明でも全国平均で返す
    assert repository.get_demolition_cost(conn, None, "木造") is not None


def test_usecase_examples_prefer_same_prefecture_then_region(app_env, conn):
    repository = app_env["app.repository"]

    hiroshima = repository.pick_usecase_examples(
        conn, prefecture="広島県", region="中国・四国", category=None, limit=4
    )
    assert hiroshima[0]["prefecture"] == "広島県"

    # 同県が無ければ同じ地方が優先される
    tottori = repository.pick_usecase_examples(
        conn, prefecture="鳥取県", region="中国・四国", category=None, limit=4
    )
    assert tottori[0]["region"] == "中国・四国"

    # 出典は必ず入っている
    for row in hiroshima:
        assert row["source_url"] == "https://jichitai.works/articles/3296"
