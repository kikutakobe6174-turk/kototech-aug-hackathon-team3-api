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

python run.py
```

> [!IMPORTANT]
> **起動は `python run.py` を使うこと。**
> `uvicorn app.main:app --reload` を直接叩くと、リポジトリ全体（`.venv` の
> 3,000 ファイル超や `data/app.db` を含む）が監視対象になる。
> git の切り替えやコミットでファイルが動くたびにサーバーが再起動し、
> **その最中のリクエストは 502 になる**。
> どうしても uvicorn を直接使うなら監視対象を絞る:
>
> ```bash
> uvicorn app.main:app --reload --reload-dir app --port 8000
> ```
>
> デモ中など、そもそも再起動されたくないときは自動リロードを切る:
>
> ```bash
> RELOAD=false python run.py
> ```

> `python app/main.py` は動かない。`app` はパッケージで `main.py` が相対 import を
> 使っているため、単体スクリプトとして実行すると
> `attempted relative import with no known parent package` になる。
> 必ずリポジトリ直下から起動する。

### 画面のボタンで 502 が出るとき

`POST /api/advice 502` は、**Next.js から Python API に届いていない**という意味
（`src/app/api/advice/route.ts` が接続失敗時に返す）。
API 側のバグではないので、まず API が生きているか確認する。

```bash
curl http://localhost:8000/health
```

- 応答が無い → API が起動していない。`python run.py` で起動する
- 応答がある → フロントの `.env.local` の `API_BASE_URL` とポートが合っているか確認

一度起動したのに落ちている場合、いちばん多いのは
**`uvicorn --reload` がリポジトリ全体を監視していて、
別の操作（git の切り替え、コミット、エディタの保存）で再起動が走った**ケース。
`python run.py` なら監視対象が `app/` だけなので起きない。

### 起動しない / すぐ止まるとき

起動直後に `Shutting down` → `Stopping reloader process` と出て終わる場合、
**ポート 8000 が別のプロセスに使われている**ことがほとんど。
前に起動したサーバーが残っていると起きる。`run.py` は先にポートを確認して
理由を表示するので、メッセージに従って残プロセスを止めるか、別ポートで起動する。

```bash
PORT=8001 python run.py
```

残っているプロセスの調べ方（PowerShell）:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen |
  ForEach-Object { Get-CimInstance Win32_Process -Filter "ProcessId=$($_.OwningProcess)" } |
  Select-Object ProcessId, CommandLine
```

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

## 「活用例」（出典のある実例 + Gemini）

`sections.usecase`（画面の「活用例」）は、
**全国の自治体で実際に行われた空き家活用の事例 15 件**をもとに組み立てる。
出典は[自治体通信オンライン](https://jichitai.works/articles/3296)。
自治体名・補助金額・成約実績をそのまま `usecase_examples` テーブルに持っている。

物件の都道府県 → 同じ地方 → その他、の優先順で事例を 4 件選ぶ。

Gemini が使える場合は、その事例を**参考資料として渡し**、この物件に当てはめた
解説を書かせる。事例に無い自治体名・施設名・数値を作らせないよう
system instruction で縛り、出典行はサーバー側で必ず付ける。

`.env` に API キーを入れると有効になる。**キーが無くても API は普通に動く**（後述のフォールバック）。

```env
GEMINI_API_KEY=＜https://aistudio.google.com/apikey で取得＞
GEMINI_MODEL=gemini-3.5-flash
GEMINI_API_REVISION=2026-05-20
GEMINI_TIMEOUT_SECONDS=30
GEMINI_ENABLED=true
LOG_LEVEL=INFO          # DEBUG にするとプロンプトも出る
LOG_ENCODING=utf-8      # ターミナルが cp932 なら cp932
```

レスポンスに載せるので `POST /advice` は生成を待つ。生成内容はログにも出る。

```
========== 活用例（生成） ==========
対象: 京都府京都市中京区 / 判定: sell / モデル: gemini-3.5-flash
-----------------------------------
※ 以下は実在の事例ではなく、条件が近い空き家で取りうる活用のモデルケースです。

■ 蔵をカフェとして貸す
  どんな空き家か: ...
  やったこと: ...
===================================
```

### 生成できないときのフォールバック

`usecase` セクションが**空になることはない**。
Gemini が使えなくても、選んだ実例をそのまま整形して出す。

| 状況 | ログ | `sections.usecase` |
| --- | --- | --- |
| キー未設定 | `[Gemini] スキップ（GEMINI_API_KEY が未設定）` | 実例をそのまま整形（出典付き） |
| 生成失敗（429 / 400 など） | `[Gemini] 生成に失敗: ... (400): ...` | 同上 |
| 成功 | `========== 活用例（生成） ==========` | 実例に基づく生成文（出典付き） |

いずれの場合も `/advice` は **200** を返し、出典 URL が本文に入る。

### キャッシュ

同じ条件（所在地・詳細・判定・モデル）なら SQLite の `usecase_cache` から返し、
LLM を呼ばない。デモで同じ入力を繰り返しても待たされず、API 消費も増えない。
`USECASE_CACHE=false` で無効化できる。

### 事実性について

固有名詞（自治体名・事業名・補助金額・実績）は
**`usecase_examples` テーブルにある実データだけ**を使う。
Gemini には参考資料として渡すだけで、そこに無いものを作らせない。
断り書きと出典行はサーバー側で必ず付与する（モデルの出力任せにしていない）。

## 売却：そのまま売る / 解体して売る

`sections.sell` では 2 通りを金額付きで比較する。

- **【A】現況のまま売る** — 土地値 + 建物の残存価値
- **【B】解体して更地で売る** — 土地値 − 解体費用

解体費の坪単価は地方 × 構造で引く（`demolition_costs` テーブル）。
出典は[スッキリ解体](https://sukkiri-kaitai.com/kaitai-hiyou/kaitaihiyo-mokuzo/)で、
あんしん解体業者認定協会の 2020〜2024 年・30,000 件以上の工事データが元。
地域の業者は <https://sukkiri-kaitai.com/kaitaikoujigyousya/> から探せる。

```
【B】解体して更地で売る
  解体費用の目安: 約 102 万円（中国・四国の木造で坪あたり約 29,038 円）
  解体後の手残り: 約 2,068 万円
```

建物にまだ価値が残っている（残存価値 50% 以上）なら現況のまま、
そうでなければ手残りの差額で有利なほうを提示する。
1 月 1 日時点で更地だと住宅用地特例（固定資産税 1/6）が外れる点も明記している。

> [!NOTE]
> 出典が公表しているのは「木造の地方別」と「構造別の全国平均」の 2 つだけ。
> 地方 × 構造の値は木造の地域差を比率として掛けた**導出値**。
> 詳細は [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md)。

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

72 件。フロントとの契約（セクション id、レスポンスの形、エラーの形）を
`tests/test_api.py` で固定してある。
`src/lib/sections.ts` を変更したら `tests/conftest.py` の
`FRONTEND_SECTION_IDS` も合わせて直すこと。
