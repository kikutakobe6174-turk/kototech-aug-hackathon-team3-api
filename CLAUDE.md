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

uvicorn app.main:app --reload --port 8000
pytest
```

## アーキテクチャ

```
app/
  config.py       環境変数 → Settings（frozen dataclass, lru_cache）
  db.py           sqlite3 接続と SCHEMA（テーブル定義はすべてここ）
  seed.py         マスタデータ（相場・係数・本文テンプレート）
  repository.py   SQL はすべてここに閉じ込める
  advice.py       採点と本文組み立て（純関数。DB にも FastAPI にも依存しない）
  models.py       pydantic のリクエスト / レスポンス
  main.py         アプリ生成・エラーハンドラ・ルーター登録
  routers/
    advice.py     POST /advice, GET /advice/history, GET /regions
tests/            37 件
```

### レイヤの約束

- **SQL は `repository.py` だけ**。ルーターに SQL を書かない。
- **テーブル定義は `db.SCHEMA` だけ**。`CREATE TABLE IF NOT EXISTS` で書き、
  `init_db()` が何度走っても同じ状態になるようにする。
- **マスタの値は `seed.py` だけ**。相場を更新したいときはここだけ直す。
  投入は `ON CONFLICT ... DO UPDATE` なので再実行で行が増えない。
- **`advice.py` は純関数**。係数は呼び出し側が SQLite から読んで `Factors` に詰める。
  ここにテストが集まっているので、DB や HTTP を持ち込まない。

## フロントとの契約（変えるときは向こうと揃える）

- エンドポイントは `POST /advice`。パスは `route.ts` の `ADVICE_PATH` で決まっている。
- リクエスト / レスポンスのキーは **snake_case のまま**。camelCase に変換しない。
- レスポンスは `{ recommendation, sections }` の 2 キーだけ。
  `recommendation` は `"sell" | "rent" | "hold"`。
- `sections` のキーは `src/lib/sections.ts` の `ADVICE_SECTIONS` の id と一致させる。
  **8 セクションすべてを必ず埋める**。欠けると画面がプレースホルダのままになる。
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

## 相場データの扱い

`seed.py` の数値は公開統計をもとにした**目安**であり、実勢価格ではない。
本文にも「個別の査定額とは差が出る」旨を必ず残すこと。
断定的な金額表現に書き換えない。
