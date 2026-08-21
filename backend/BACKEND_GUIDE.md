# CLAUDE.md

このファイルは Claude Code (claude.ai/code) がこのリポジトリで作業するときの指針です。

## プロジェクト概要

**Figma to JSON** のサーバーサイド API。フロントエンド（Next.js /
`kikutakobe6174-turk/script-from-figma`）から呼ばれ、次の 2 つを担当する。

1. Figma OAuth でログインさせ、セッション cookie を発行する
2. file key を受け取り Figma REST API を叩き、**扱いやすい JSON 構造体**に変換して返す

永続化はすべて **SQLite（標準ライブラリの `sqlite3`）**。ORM は使わない。

## コマンド

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt   # Windows
# source .venv/bin/activate && pip install -r requirements-dev.txt  # macOS / Linux

cp .env.example .env            # 値を埋める
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
.venv/Scripts/python.exe -m pytest          # テスト（23 件）
```

API ドキュメント: http://localhost:8000/docs

## アーキテクチャ

```
app/
  config.py       環境変数 → Settings（frozen dataclass, lru_cache）
  db.py           sqlite3 接続と SCHEMA（テーブル定義はすべてここ）
  repository.py   SQL はすべてここに閉じ込める。ルーターから直接 SQL を書かない
  figma.py        Figma REST クライアント + ノードツリーの JSON 変換（HTTP と純関数）
  models.py       pydantic のリクエスト / レスポンススキーマ
  deps.py         FastAPI 依存関係（セッション認証・アクセストークン取得）
  main.py         アプリ生成・CORS・ルーター登録
  routers/
    auth.py       /login /callback /logout /me
    figma.py      /api/figma 以下
```

### レイヤの約束

- **SQL は `repository.py` だけ**。ルーターやサービスに SQL を散らさない。
- **テーブル定義は `db.SCHEMA` だけ**。追加するときは `CREATE TABLE IF NOT EXISTS` /
  `CREATE INDEX IF NOT EXISTS` で書き、`init_db()` が何度走っても安全な状態を保つ。
- **`figma.py` の変換関数は純関数**（`simplify_node` / `build_structure` / `flatten`）。
  DB にも設定にも依存させない。テストしやすさを優先する。
- ルーターは「入力の検証 → repository / figma の呼び出し → pydantic に詰める」だけ。

## データ構造（SQLite）

| テーブル | 役割 |
| --- | --- |
| `users` | Figma でログインしたユーザー（`figma_user_id` が UNIQUE） |
| `oauth_tokens` | アクセストークン / リフレッシュトークン（`user_id` が PK） |
| `oauth_states` | OAuth の state。CSRF 対策の使い捨て |
| `sessions` | ログインセッション。`id` は cookie トークンの **SHA-256** |
| `figma_files` | 変換済み構造体（`structure_json`）。`UNIQUE(user_id, file_key)` |
| `figma_nodes` | ノードツリーをフラットに展開。`parent_node_id` で自己参照 |

`figma_nodes` があるので、JSON を舐めずに SQL でノードを検索できる
（`GET /api/figma/{file_key}/nodes?type=TEXT` など）。

外部キーは `PRAGMA foreign_keys = ON` を接続ごとに有効化し、すべて `ON DELETE CASCADE`。

## 実装上の注意

- `sqlite3.connect(..., check_same_thread=False)` は必須。FastAPI が sync な依存関係を
  スレッドプールで実行するため、これがないと `ProgrammingError` になる。
  コネクションはリクエストごとに 1 本（`db.get_db`）なので競合はしない。
- `isolation_level=None`（オートコミット）。明示的にまとめたいところだけ
  `BEGIN` / `COMMIT` を書く（`repository.save_structure` が例）。
- セッショントークンは **生の値を DB に入れない**。cookie に生、DB にハッシュ。
- CORS は `allow_credentials=True`。したがって `allow_origins` に `"*"` は使えない。
  フロントの `fetch` は `credentials: "include"` で呼んでくる。
- クロスドメイン構成にするときは `COOKIE_SAMESITE=none` かつ `COOKIE_SECURE=true`。
- Figma のトークンは `Authorization: Bearer`、Personal Access Token は `X-Figma-Token`。
  この違いは `figma._auth_headers(personal=...)` に閉じ込めてある。
- 日付は SQLite の `datetime('now')`（UTC・`YYYY-MM-DD HH:MM:SS`）に揃える。
  Python 側から入れるときは `repository._iso()` を通す。

## フロントエンドとの契約

変えるときは `script-from-figma` 側と揃えること。

- `GET /login` → Figma の認可画面へ 307。完了後 `FRONTEND_URL/file` へ 303。
- `POST /api/figma` ← `{ "fileKey": "..." }`、cookie 必須。
  レスポンスのキーは **camelCase**（`fileKey` / `nodeCount` / `lastModified` …）。
  pydantic の `serialization_alias` で変換しているので、フィールドを足すときも合わせる。

## テスト

- `tests/conftest.py` がテストごとに一時 SQLite を指して **アプリを reload** する
  （`get_settings` が `lru_cache` なので `cache_clear()` も必要）。
- Figma への HTTP は必ずモンキーパッチする（`fake_figma` フィクスチャ）。
  実際の Figma を叩くテストは書かない。
- 期待値のサンプルは `tests/conftest.py` の `FAKE_FILE`。
