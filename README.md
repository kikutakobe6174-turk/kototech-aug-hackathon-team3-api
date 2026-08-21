# 空き家活用アドバイザー API

[`kotonara-tech/kototech-aug-hackathon-team3`](https://github.com/kotonara-tech/kototech-aug-hackathon-team3)
のバックエンド。FastAPI + SQLite。

所在地と物件の詳細を受け取り、**売却 / 賃貸 / 保持**のどれが有力かを判定して、
画面の 8 セクション分のレポート本文を返す。

## フロントとの接続

```
ブラウザ
  └─ POST /api/advice            … src/lib/apiClient.ts
       └─ Next.js ルートハンドラ  … src/app/api/advice/route.ts
            └─ POST {API_BASE_URL}/advice   ← このリポジトリ
```

ブラウザから直接ではなく Next.js のサーバー側を経由するので、**CORS の設定は不要**。

フロント側 `.env.local`:

```env
API_BASE_URL=http://localhost:8000
```

## セットアップ

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env             # そのままでも動く

python run.py                    # または uvicorn app.main:app --reload --port 8000
```

> `python app/main.py` は動かない。`app` はパッケージで `main.py` が相対 import を
> 使っているため、単体スクリプトとして実行すると
> `attempted relative import with no known parent package` になる。
> **リポジトリ直下から** `python run.py` か `uvicorn app.main:app` で起動する。

- Swagger UI: <http://localhost:8000/docs>
- ヘルスチェック: <http://localhost:8000/health>

起動時に SQLite のスキーマ作成とマスタ投入が自動で走る。初期設定は不要。

## エンドポイント

| メソッド | パス | 説明 |
| --- | --- | --- |
| POST | `/advice` | **診断（フロントが叩くのはこれだけ）** |
| GET | `/advice/history` | 診断履歴（動作確認用） |
| GET | `/regions` | 登録済みの都道府県一覧（動作確認用） |
| GET | `/health` | ヘルスチェック |

### `POST /advice`

リクエスト（`src/lib/types.ts` の `AdviceRequest`）。
未入力の項目はキーごと送られてこない前提で、すべて省略可。

```jsonc
{
  "prefecture": "京都府",
  "city": "京都市中京区",
  "detail": {
    "tsubo": 35,               // 坪数
    "built_years": 40,         // 築年数
    "structure": "木造",
    "property_type": "戸建て",
    "floors": "2階建て",
    "parking": "あり（1台）"
  }
}
```

レスポンス（`AdviceResult`）。`sections` のキーは
`src/lib/sections.ts` の `ADVICE_SECTIONS` の id と一致する。

```jsonc
{
  "recommendation": "sell",
  "sections": {
    "summary": "京都府京都市中京区の戸建てについて、診断結果は「売却」が最有力です。…",
    "sell":    "【今回の診断ではこの選択肢が最有力です】\n\n想定売却価格は約 8,497 万円です。…",
    "rent":    "…",
    "hold":    "…",
    "market":  "…",
    "cost":    "…",
    "risk":    "…",
    "next":    "…"
  }
}
```

エラーは必ず `{"error": "..."}` の形で返す（`apiClient.ts` が `error` キーを読むため）。

```jsonc
// 400
{ "error": "「架空県」の相場データがありません。都道府県名をご確認ください。" }
```

### 動かしてみる

```bash
curl -X POST http://localhost:8000/advice \
  -H 'Content-Type: application/json' \
  -d '{"prefecture":"京都府","city":"京都市中京区","detail":{"tsubo":35,"built_years":40,"structure":"木造"}}'
```

## 診断ロジック

`app/advice.py`。売却・賃貸・保持を 0〜100 で採点し、最高点を `recommendation` にする。

| 効く方向 | 要素 |
| --- | --- |
| 売却↑ | 坪単価が高い / 築古で建物の残存価値が低い / 人口減少 |
| 賃貸↑ | 賃貸需要が高い / 建物が生きている / 駐車場あり / 平屋 / 人口増加 |
| 保持↑ | 人口増加 / 空き家率が低い / 建物が新しい |

- 建物の残存価値 = `1 - 築年数 / 法定耐用年数`（下限 5%、上限 100%）
- 種別ごとの重みを最後に掛ける。「土地のみ」は賃貸を 0 点にして候補から外す
- 同点なら 売却 → 賃貸 → 保持 の順で決まる
- 坪数が未入力なら金額の試算は行わず、その旨を本文に出す

## データ構造（SQLite）

```
regions                地域ごとの相場・需要（city='' が都道府県のデフォルト）
structure_factors      造りごとの法定耐用年数・再建築費・リフォーム費
property_type_factors  種別ごとの重みと賃貸可否
advice_templates       セクション本文のテンプレート（recommendation='any' は共通文）
diagnoses              診断履歴（入力・判定・スコア・本文）
```

DB ファイルは `DATABASE_PATH`（既定 `./data/app.db`）。
テーブル定義は `app/db.py` の `SCHEMA`、初期データは `app/seed.py` にまとまっている。

> [!WARNING]
> **相場データは実データではない。** `app/seed.py` の数値はオープンデータから
> 取得・算出したものではなく、動作確認用に手で置いたプレースホルダの概算値。
> 実勢価格でも統計値でもないので、そのまま外部に出さないこと。
> 実データへの差し替え方法と出典候補は [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) を参照。

市区町村レベルの行は主要 12 地点のみ登録済みで、
未登録の市区町村は都道府県のデフォルト値にフォールバックする。

## 活用方法の生成（Gemini）

診断とは別に、**その空き家の具体的な活用方法**を Gemini に生成させる。
いまのところ結果は**サーバーログに出すだけ**で、フロントへのレスポンスには含めない
（生成内容を目視で確認してから組み込むため）。

`.env` に API キーを入れると有効になる。キーが無くても API は普通に動く。

```env
GEMINI_API_KEY=＜https://aistudio.google.com/apikey で取得＞
GEMINI_MODEL=gemini-3.5-flash
GEMINI_API_REVISION=2026-05-20
GEMINI_TIMEOUT_SECONDS=30
GEMINI_ENABLED=true
LOG_LEVEL=INFO          # DEBUG にするとプロンプトも出る
LOG_ENCODING=utf-8      # ターミナルが cp932 なら cp932
```

`POST /advice` を受けると、レスポンスを返した**あと**にバックグラウンドで生成する。
診断の応答は待たされない。ログはこう出る。

```
========== Gemini 活用方法 ==========
対象: 京都府京都市中京区 / 判定: sell / モデル: gemini-3.5-flash
-------------------------------------
■ 解体して駐車場として貸す
  概要: ...
  想定初期費用: ...
  ...
=====================================
```

キー未設定なら `[Gemini] スキップ（GEMINI_API_KEY が未設定）: ...`、
失敗したら `[Gemini] 生成に失敗: ... / Gemini がエラーを返しました (400): ...` が出る。
**生成が失敗しても `/advice` は 200 のまま**なので、画面には影響しない。

使っているのは Interactions API
（`POST https://generativelanguage.googleapis.com/v1beta/interactions`、
`x-goog-api-key` ヘッダー）。
仕様が変わったら `GEMINI_API_REVISION` と `app/gemini.py` を見直す。

## データ出典

相場・需要データの出どころと、実データ（不動産情報ライブラリ / e-Stat）への
差し替え手順は [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) にまとめてある。

## テスト

```bash
pip install -r requirements-dev.txt
pytest
```

48 件。フロントとの契約（セクション id、レスポンスの形、エラーの形）を
`tests/test_api.py` で固定してある。
`src/lib/sections.ts` を変更したら `tests/conftest.py` の
`FRONTEND_SECTION_IDS` も合わせて直すこと。
