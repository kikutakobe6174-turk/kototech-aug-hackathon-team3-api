# kototech-aug-hackathon-team3-api

[`kotonara-tech/kototech-aug-hackathon-team3`](https://github.com/kotonara-tech/kototech-aug-hackathon-team3)
（空き家活用アドバイザー）のサーバーサイド。

## 構成

```
backend/    Python / FastAPI + SQLite の API 本体
            └ CLAUDE.md   backend で作業するとき用の指針
CLAUDE.md   フロント側リポジトリから移植（@AGENTS.md を読み込む / Next.js 向け）
AGENTS.md   同上
```

## クイックスタート

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

フロント側の `.env.local` に `API_BASE_URL=http://localhost:8000` を設定すると繋がる。

```
ブラウザ → /api/advice（Next.js ルートハンドラ）→ POST /advice（このリポジトリ）
```

詳細は [`backend/README.md`](backend/README.md)。
