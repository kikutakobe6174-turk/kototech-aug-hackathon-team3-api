# Figma to JSON API

FigmaのファイルをJSON構造体で返すサーバーサイド。FastAPI + SQLite。

フロントエンド: [`kotonara-tech/kototech-aug-hackathon-team3`](https://github.com/kotonara-tech/kototech-aug-hackathon-team3)

このディレクトリ（`backend/`）がサーバーサイド一式。

## できること

- Figma OAuth でログイン（セッションは httpOnly cookie）
- file key を渡すと、Figma のノードツリーを整形した JSON を返す
- 取得結果を SQLite に保存し、履歴・キャッシュ・ノード検索ができる

## セットアップ

```bash
cd backend

python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

### Figma アプリの登録

<https://www.figma.com/developers/apps> で App を作り、以下を `.env` に設定する。

| 変数 | 値 |
| --- | --- |
| `FIGMA_CLIENT_ID` | App の Client ID |
| `FIGMA_CLIENT_SECRET` | App の Client Secret |
| `FIGMA_REDIRECT_URI` | `http://localhost:8000/callback`（Figma 側にも同じ値を登録する） |
| `FRONTEND_URL` | `http://localhost:3000` |
| `ALLOWED_ORIGINS` | `http://localhost:3000` |

### 起動

```bash
uvicorn app.main:app --reload --port 8000
```

- Swagger UI: <http://localhost:8000/docs>
- ヘルスチェック: <http://localhost:8000/health>

フロント側の `.env.local` には `NEXT_PUBLIC_API_URL=http://localhost:8000` を入れる。

### OAuth なしで試す（開発用）

Figma の Personal Access Token があれば、ログインを飛ばして動作確認できる。

```env
DEV_MODE=true
FIGMA_PERSONAL_ACCESS_TOKEN=figd_xxxxxxxx
```

```bash
curl -X POST http://localhost:8000/api/figma \
  -H 'Content-Type: application/json' \
  -d '{"fileKey":"あなたのfile key"}'
```

## エンドポイント

| メソッド | パス | 説明 |
| --- | --- | --- |
| GET | `/login` | Figma の認可画面へリダイレクト |
| GET | `/callback` | Figma からの戻り。cookie を発行し `FRONTEND_URL/file` へ |
| POST | `/logout` | ログアウト |
| GET | `/me` | ログイン中のユーザー |
| POST | `/api/figma` | **file key から JSON 構造体を取得（メイン）** |
| GET | `/api/figma/history` | 取得履歴 |
| GET | `/api/figma/{file_key}` | 保存済みの構造体 |
| GET | `/api/figma/{file_key}/nodes` | 保存済みノードを絞り込み検索 |
| GET | `/api/figma/{file_key}/stats` | ノード種別ごとの件数 |
| DELETE | `/api/figma/{file_key}` | 履歴を削除 |

### `POST /api/figma`

```jsonc
// リクエスト
{
  "fileKey": "ABCDEFG12345",
  "refresh": false,   // 省略可。true でキャッシュを無視して取り直す
  "maxDepth": 100     // 省略可。ツリーの最大深さ
}
```

```jsonc
// レスポンス
{
  "fileKey": "ABCDEFG12345",
  "name": "Hackathon UI",
  "version": "42",
  "lastModified": "2026-08-20T10:00:00Z",
  "thumbnailUrl": "https://...",
  "nodeCount": 128,
  "fetchedAt": "2026-08-21 11:22:33",
  "cached": false,
  "structure": {
    "name": "Hackathon UI",
    "document": {
      "id": "0:0",
      "name": "Document",
      "type": "DOCUMENT",
      "children": [
        {
          "id": "2:1",
          "name": "Login",
          "type": "FRAME",
          "layout": { "box": { "x": 0, "y": 0, "width": 375, "height": 812 },
                      "layoutMode": "VERTICAL", "itemSpacing": 16 },
          "fills": [{ "type": "SOLID", "color": "#FFFFFF" }],
          "children": [
            { "id": "2:2", "name": "Title", "type": "TEXT",
              "characters": "Figma to JSON",
              "textStyle": { "fontFamily": "Inter", "fontSize": 40 } }
          ]
        }
      ]
    },
    "components": {}, "componentSets": {}, "styles": {}
  }
}
```

生の Figma レスポンスをそのまま返すのではなく、色は `#RRGGBB` に、レイアウトは
`layout`、文字スタイルは `textStyle` にまとめて返す。

### ノード検索

取得時にツリーを `figma_nodes` テーブルへ展開しているので、JSON を辿らずに検索できる。

```bash
# テキストノードだけ
curl 'http://localhost:8000/api/figma/ABCDEFG12345/nodes?type=TEXT'
# ある親の直下だけ
curl 'http://localhost:8000/api/figma/ABCDEFG12345/nodes?parent=2:1'
# 名前の部分一致
curl 'http://localhost:8000/api/figma/ABCDEFG12345/nodes?name=Button'
```

## データ構造（SQLite）

```
users ──┬── oauth_tokens        (1:1  アクセストークン)
        ├── sessions            (1:N  ログインセッション / cookie はハッシュで保存)
        └── figma_files         (1:N  取得したファイル・変換済み JSON)
                └── figma_nodes (1:N  ノードを平坦化。parent_node_id で自己参照)

oauth_states                    (OAuth の state。CSRF 対策の使い捨て)
```

DB ファイルは `DATABASE_PATH`（既定 `./data/app.db`）。スキーマは起動時に自動作成される。
テーブル定義は `app/db.py` の `SCHEMA` にまとまっている。

## テスト

```bash
pip install -r requirements-dev.txt
pytest
```

Figma への通信はすべてモックしているので、ネットワークもトークンも不要。

## 本番デプロイ時の注意

フロントとサーバーが別ドメインになる場合は cookie の設定を変える。

```env
COOKIE_SECURE=true
COOKIE_SAMESITE=none
FRONTEND_URL=https://your-frontend.example.com
ALLOWED_ORIGINS=https://your-frontend.example.com
FIGMA_REDIRECT_URI=https://your-api.example.com/callback
```
