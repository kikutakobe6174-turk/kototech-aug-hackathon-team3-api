"""フロント（`src/lib/apiClient.ts` / `src/app/api/advice/route.ts`）との契約テスト。"""

from __future__ import annotations

from tests.conftest import (
    DEV_SIMPLE_KEYS,
    FRONTEND_SECTION_IDS,
    RECOMMENDATIONS,
    SAMPLE_REQUEST,
)


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_advice_returns_dev_simple_shape(client):
    """dev_simple ブランチのフロントが読む形（recommendation + usage）。"""
    res = client.post("/advice", json=SAMPLE_REQUEST)
    assert res.status_code == 200, res.text
    body = res.json()

    for key in DEV_SIMPLE_KEYS:
        assert key in body, key
    assert body["recommendation"] in set(RECOMMENDATIONS)
    assert isinstance(body["usage"], str)
    assert body["usage"].strip(), "usage が空だと画面に何も出ない"


def test_advice_keeps_master_shape(client):
    """master ブランチのフロントも壊さない（sections を残す）。"""
    body = client.post("/advice", json=SAMPLE_REQUEST).json()

    assert set(body["sections"]) == set(FRONTEND_SECTION_IDS)
    for section_id, text in body["sections"].items():
        assert isinstance(text, str), section_id
        assert text.strip(), section_id


def test_recommended_section_is_marked(client):
    body = client.post("/advice", json=SAMPLE_REQUEST).json()
    recommended = body["sections"][body["recommendation"]]
    assert recommended.startswith("【今回の診断ではこの選択肢が最有力です】")


def test_detail_can_be_empty(client):
    """未入力の項目はキーごと送られてこない（adviceRequest.ts の compact）。"""
    res = client.post("/advice", json={"prefecture": "秋田県", "city": "横手市", "detail": {}})
    assert res.status_code == 200, res.text
    assert set(res.json()["sections"]) == set(FRONTEND_SECTION_IDS)
    # 坪数が無いので金額は試算せず、その旨を出す
    assert "坪数を入力すると試算します" in res.json()["sections"]["sell"]


def test_detail_key_is_optional(client):
    res = client.post("/advice", json={"prefecture": "東京都", "city": "世田谷区"})
    assert res.status_code == 200, res.text


def test_city_level_data_is_used(client):
    """市区町村の行があればそれを、無ければ都道府県のデフォルトを使う。"""
    chukyo = client.post(
        "/advice", json={"prefecture": "京都府", "city": "京都市中京区", "detail": {}}
    ).json()
    maizuru = client.post(
        "/advice", json={"prefecture": "京都府", "city": "舞鶴市", "detail": {}}
    ).json()
    unknown = client.post(
        "/advice", json={"prefecture": "京都府", "city": "宇治市", "detail": {}}
    ).json()

    assert "約240万円" in chukyo["sections"]["market"].replace(" ", "")
    assert "約22万円" in maizuru["sections"]["market"].replace(" ", "")
    # 未登録の市区町村は京都府のデフォルト（130万円）にフォールバック
    assert "約130万円" in unknown["sections"]["market"].replace(" ", "")


def test_land_only_never_recommends_rent(client):
    body = client.post(
        "/advice",
        json={
            "prefecture": "秋田県",
            "city": "横手市",
            "detail": {"tsubo": 60, "property_type": "土地のみ"},
        },
    ).json()
    assert body["recommendation"] != "rent"


# --- エラー応答 -------------------------------------------------------------

def test_unknown_prefecture_returns_error_key(client):
    res = client.post(
        "/advice", json={"prefecture": "架空県", "city": "架空市", "detail": {}}
    )
    assert res.status_code == 400
    # apiClient.ts は `error` キーだけを読む
    assert "error" in res.json()
    assert "架空県" in res.json()["error"]


def test_validation_error_uses_error_key(client):
    res = client.post("/advice", json={"prefecture": "", "city": "", "detail": {}})
    assert res.status_code == 400
    assert "error" in res.json()
    assert "detail" not in res.json()


def test_invalid_tsubo_is_rejected(client):
    res = client.post(
        "/advice",
        json={"prefecture": "東京都", "city": "世田谷区", "detail": {"tsubo": -5}},
    )
    assert res.status_code == 400
    assert "error" in res.json()


def test_unknown_detail_values_fall_back(client):
    """DetailPanel の選択肢外の文字列が来ても 500 にしない。"""
    res = client.post(
        "/advice",
        json={
            "prefecture": "東京都",
            "city": "世田谷区",
            "detail": {"structure": "藁", "property_type": "城", "floors": "地下"},
        },
    )
    assert res.status_code == 200, res.text


# --- 履歴 -------------------------------------------------------------------

def test_history_records_each_diagnosis(client):
    client.post("/advice", json=SAMPLE_REQUEST)
    client.post("/advice", json={"prefecture": "沖縄県", "city": "那覇市", "detail": {}})

    history = client.get("/advice/history").json()
    assert len(history) == 2
    assert history[0]["prefecture"] == "沖縄県"  # 新しい順
    assert history[1]["detail"]["tsubo"] == 35
    assert set(history[0]["scores"]) == {"sell", "rent", "hold"}


def test_regions_lists_47_prefectures(client):
    body = client.get("/regions").json()
    assert len(body["prefectures"]) == 47
    assert body["prefectures"][0] == "北海道"
    assert body["prefectures"][-1] == "沖縄県"


def test_recovers_when_database_file_disappears(client, app_env):
    """起動後に DB ファイルが消えても、次のリクエストで作り直して応答する。

    sqlite3 は存在しないパスを開くと空の DB を黙って作るため、
    復旧しないと「no such table: regions」で 500 になる。
    """
    settings = app_env["app.config"].get_settings()
    assert client.post("/advice", json=SAMPLE_REQUEST).status_code == 200

    settings.database_path.unlink()
    assert not settings.database_path.exists()

    res = client.post("/advice", json=SAMPLE_REQUEST)
    assert res.status_code == 200, res.text
    assert set(res.json()["sections"]) == set(FRONTEND_SECTION_IDS)


# --- 売却: そのまま売る / 解体して売る ---------------------------------------

def test_sell_section_compares_as_is_and_demolition(client):
    body = client.post("/advice", json=SAMPLE_REQUEST).json()
    sell = body["sections"]["sell"]

    assert "現況のまま売る" in sell
    assert "解体して更地で売る" in sell
    assert "どちらが有利か" in sell
    # 解体費の坪単価と総額が入る
    assert "解体費用の目安" in sell
    assert "解体後の手残り" in sell
    # 出典を必ず載せる
    assert "sukkiri-kaitai.com" in sell


def test_demolition_cost_reflects_region(client):
    """地方が違えば解体費の坪単価も変わる。"""
    def unit_price(prefecture: str, city: str) -> str:
        sell = client.post(
            "/advice",
            json={
                "prefecture": prefecture,
                "city": city,
                "detail": {"tsubo": 30, "structure": "木造", "built_years": 40},
            },
        ).json()["sections"]["sell"]
        return sell.split("坪あたり約 ")[1].split(" 円")[0]

    assert unit_price("東京都", "世田谷区") == "35,270"      # 関東
    assert unit_price("広島県", "広島市中区") == "29,038"    # 中国・四国
    assert unit_price("京都府", "京都市中京区") == "36,185"  # 近畿


def test_sell_section_without_tsubo_is_still_readable(client):
    sell = client.post(
        "/advice", json={"prefecture": "秋田県", "city": "横手市", "detail": {}}
    ).json()["sections"]["sell"]

    assert "現況のまま売る" in sell
    assert "坪数を入力すると" in sell
