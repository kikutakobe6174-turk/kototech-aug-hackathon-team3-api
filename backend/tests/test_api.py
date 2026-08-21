"""フロント（`src/lib/apiClient.ts` / `src/app/api/advice/route.ts`）との契約テスト。"""

from __future__ import annotations

from tests.conftest import FRONTEND_SECTION_IDS, SAMPLE_REQUEST


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_advice_returns_frontend_shape(client):
    res = client.post("/advice", json=SAMPLE_REQUEST)
    assert res.status_code == 200, res.text
    body = res.json()

    # AdviceResult = { recommendation, sections }
    assert set(body) == {"recommendation", "sections"}
    assert body["recommendation"] in {"sell", "rent", "hold"}

    # 画面の全セクションが埋まっていること（欠けるとプレースホルダのままになる）
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
