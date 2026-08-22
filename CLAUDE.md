@AGENTS.md

<!--
上の AGENTS.md はフロント側リポジトリ
(kotonara-tech/kototech-aug-hackathon-team3) から移植したもので、
中身は `next dev` が自動生成した Next.js 向けのルール。
このリポジトリは Python なので、実際に従うのは以下の内容。
-->

# CLAUDE.md

Claude Code (claude.ai/code) がこのリポジトリで作業するときの指針。

## このリポジトリは何か

[`kotonara-tech/kototech-aug-hackathon-team3`](https://github.com/kotonara-tech/kototech-aug-hackathon-team3)
（空き家活用アドバイザー / Next.js）のバックエンド。FastAPI + SQLite。

所在地と物件の詳細を受け取り、**売却 / 賃貸 / 保持**のどれが有力かを判定して、
画面の 8 セクション分のレポート本文を返す。

フロントが叩くのは **`POST /advice` の 1 本だけ**。
ブラウザ → Next.js のルートハンドラ（`src/app/api/advice/route.ts`）→ この API、
という中継構成なので **CORS は本来不要**。

## コマンド

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

python run.py                    # = uvicorn app.main:app --reload --port 8000
pytest
```

`python app/main.py` は動かない（`app` はパッケージで、`main.py` が相対 import を
使っているため）。必ずリポジトリ直下からパッケージとして読み込ませる。

`run.py` は reload の監視対象を `app/` に絞っている。**ここを広げないこと。**
リポジトリ全体を監視すると `.venv` の 3,000 ファイル超や `data/app.db` まで対象になり、
git の切り替えやコミットのたびにサーバーが再起動する。
再起動中のリクエストは接続できず、フロントには 502 として出る（実測で確認済み）。

`uvicorn app.main:app --reload` を直接案内しないこと。案内するなら必ず
`--reload-dir app` を付ける。デモ用途なら `RELOAD=false python run.py`。

起動前にポートの空きも確認する。埋まっていると uvicorn は
`Application startup complete` の直後に黙って終了してしまい、原因が分かりにくい。

## アーキテクチャ

```
app/
  config.py       環境変数 → Settings（frozen dataclass, lru_cache）
  db.py           sqlite3 接続と SCHEMA（テーブル定義はすべてここ）
  seed.py         マスタデータ（相場・係数・本文テンプレート）※出典なしの仮値
  usage_templates.py dev_simple 用 `usage` のテンプレート（判定ごと）
  reference_data.py 出典のある実データ（活用事例・解体費用）
  repository.py   SQL はすべてここに閉じ込める
  advice.py       採点と本文組み立て（純関数。DB にも FastAPI にも依存しない）
  gemini.py       `usage` の生成（Gemini Interactions API）
  models.py       pydantic のリクエスト / レスポンス
  main.py         アプリ生成・エラーハンドラ・ルーター登録
  routers/
    advice.py     POST /advice, GET /advice/history, GET /regions
tests/            77 件
run.py            開発用の起動スクリプト
docs/
  DATA_SOURCES.md 相場データの出典と、実データへの差し替え手順
```

### レイヤの約束

- **SQL は `repository.py` だけ**。ルーターに SQL を書かない。
- **テーブル定義は `db.SCHEMA` だけ**。`CREATE TABLE IF NOT EXISTS` で書き、
  `init_db()` が何度走っても同じ状態になるようにする。
- **マスタの値は `seed.py` と `reference_data.py` だけ**。
  投入は `ON CONFLICT ... DO UPDATE` なので再実行で行が増えない。
  **2 つを混ぜないこと**: `reference_data.py` は出典のある実データ
  （活用事例・解体費用）、`seed.py` は出典のない手書きの仮値。
  新しいデータを足すときも、出典の有無で置き場所を分ける。
- **`advice.py` は純関数**。係数は呼び出し側が SQLite から読んで `Factors` に詰める。
  ここにテストが集まっているので、DB や HTTP を持ち込まない。

## フロントとの契約（変えるときは向こうと揃える）

- エンドポイントは `POST /advice`。パスは `route.ts` の `ADVICE_PATH` で決まっている。
- リクエスト / レスポンスのキーは **snake_case のまま**。camelCase に変換しない。
- **フロントは 2 系統ある。両方が読める形で返す。**
  - `dev_simple`（`~/ienomirai_front_branch`）… `{ recommendation, usage }` を読む
  - `master`（`~/ienomirai_front`）… `{ recommendation, sections }` を読む
  どちらも余分なキーは無視するので、両方入れておけば片方を壊さない。
  片方を消すときは、対応するフロントが無くなったことを確認してから。
- `recommendation` は `"sell" | "rent" | "hold"`。
- `usage` は 1 本の文章。画面では `whitespace-pre-wrap` で表示されるため、
  Markdown は効かない。改行と全角記号だけで組み立てる。
  判定ごとのテンプレートは `app/usage_templates.py`。
  **空文字を返さないこと**（画面に何も出なくなる）。
- `sections` のキーは `src/lib/sections.ts` の `ADVICE_SECTIONS` の id と一致させる。
  **9 セクションすべてを必ず埋める**。欠けると画面がプレースホルダのままになる。
  この並びは `advice.SECTION_IDS` と `tests/conftest.py` の
  `FRONTEND_SECTION_IDS` の 2 か所で固定してある。
- **エラーは必ず `{"error": "..."}`**。FastAPI 既定の `{"detail": ...}` のままだと
  `src/lib/apiClient.ts` がメッセージを拾えない。
  `main.py` の例外ハンドラ 3 つ（HTTPException / RequestValidationError / Exception）で
  変換しているので、新しいエラー経路を足すときもこの形を守る。
- 未入力の詳細項目は**キーごと送られてこない**（`src/lib/adviceRequest.ts` の `compact`）。
  `detail` そのものが無い場合もある。全項目が省略可能である前提を崩さない。

## 実装上の注意

- `sqlite3.connect(..., check_same_thread=False)` は必須。FastAPI が sync な依存関係を
  スレッドプールで実行するため、これがないと `ProgrammingError` になる。
  コネクションはリクエストごとに 1 本（`db.get_db`）なので競合はしない。
- `isolation_level=None`（オートコミット）。
- `advice_templates` の共通文は `recommendation = 'any'` で表す。
  **NULL にしてはいけない** — SQLite の UNIQUE は NULL 同士を別物として扱うので、
  再投入のたびに行が重複する。
- 未知の造り・種別は `repository` 側で「その他」「戸建て」にフォールバックする。
  DetailPanel の選択肢が増えても 500 にしない。
- 未登録の市区町村は都道府県のデフォルト行（`city = ''`）にフォールバックする。
  都道府県自体が無ければ 400 を返す。
- 本文テンプレートは `str.format_map` で埋める。未知のプレースホルダは
  `advice._Blanks` が `—` に置き換えるので、テンプレートのタイポで 500 にはならない。
- `sections.usecase` の本文だけ Gemini で生成し、レスポンスに載せている。
  他のセクションはテンプレートのまま。
- **`gemini.generate_usecase` は例外を外へ投げない**（失敗時は None）。
  活用例が出せないだけで診断全体を落とさないため。
  None のときは `advice_templates` のフォールバック文がそのまま残るので、
  `usecase` が空になることはない。フォールバック文を消さないこと。
- 同じ条件の生成結果は `usecase_cache` テーブルにキャッシュする。
  キーは所在地・詳細・判定・モデル名のハッシュ（`gemini.cache_key`）。
  モデル名をキーに含めているので、`GEMINI_MODEL` を変えれば自然に作り直される。
- **固有名詞は `usecase_examples` の実データだけを使わせる。**
  Gemini には【参考事例】として渡すだけで、そこに無い自治体名・施設名・
  補助金額を作らせない（system instruction で明示）。
  `gemini.DISCLAIMER` と `gemini.source_note()` はサーバー側で必ず付ける。
  モデルの出力任せにしないこと。ここを緩めない。
- Gemini が使えないときは `gemini.format_examples()` で実例をそのまま出す。
  `advice_templates` の汎用文まで落ちるのは事例が 1 件も無いときだけ。
- ログは `main._configure_logging()` で UTF-8 に固定している。
  Windows だと標準出力が cp932 になり、リダイレクト時に日本語が化けるため。
- `db.ensure_schema` がリクエストごとにテーブルの有無を確認し、無ければ作り直す。
  sqlite3 は存在しないパスを開くと空の DB を黙って作るため、これが無いと
  「data/ を消した」「DATABASE_PATH を変えた」だけで
  `no such table: regions` の 500 になる。

## 相場データの扱い

`seed.py` の数値は**実データではない**。オープンデータから取得・算出したものではなく、
動作確認用に手で置いたプレースホルダの概算値。

- 本文にも「個別の査定額とは差が出る」旨を必ず残すこと。断定的な金額表現に書き換えない。
- 「公開統計に基づく」「実勢価格」などと説明してはいけない。実際に取得していない。
- 実データへ差し替えるときの出典と手順は `docs/DATA_SOURCES.md` にまとめてある。
