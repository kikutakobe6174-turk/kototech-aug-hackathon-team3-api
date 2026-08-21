# kototech-aug-hackathon-team3-api

[`kotonara-tech/kototech-aug-hackathon-team3`](https://github.com/kotonara-tech/kototech-aug-hackathon-team3)
のサーバーサイド。

## 構成

```
backend/    Python / FastAPI + SQLite の API 本体
CLAUDE.md   フロント側リポジトリから移植（@AGENTS.md を読み込む）
AGENTS.md   同上
```

## クイックスタート

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Figma の Client ID / Secret を設定
uvicorn app.main:app --reload --port 8000
```

詳細は [`backend/README.md`](backend/README.md)、
実装方針は [`backend/BACKEND_GUIDE.md`](backend/BACKEND_GUIDE.md) を参照。
