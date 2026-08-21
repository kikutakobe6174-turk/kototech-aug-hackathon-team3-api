"""開発用の起動スクリプト。

    python run.py

`app/main.py` を直接叩くと ImportError になる。
`app` はパッケージで、`main.py` は `from .config import ...` という相対 import を
使っているため、単体のスクリプトとして実行すると親パッケージが分からない
（"attempted relative import with no known parent package"）。
必ずリポジトリ直下から、パッケージとして読み込ませる必要がある。

同じことを uvicorn のコマンドで書くとこうなる:

    uvicorn app.main:app --reload --reload-dir app --port 8000

環境変数:
    HOST    既定 127.0.0.1
    PORT    既定 8000
    RELOAD  既定 true
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import uvicorn

# reload の監視対象。リポジトリ全体を見せると .venv の数千ファイルまで
# 走査してしまい、起動が遅くなったり監視が不安定になる。
RELOAD_DIRS = ["app"]
RELOAD_EXCLUDES = ["*.db", "*.db-wal", "*.db-shm", "data/*", ".venv/*", ".git/*"]


def _bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _port_is_taken(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def main() -> int:
    if not (Path(__file__).parent / "app" / "main.py").exists():
        print(
            "app/main.py が見つかりません。リポジトリ直下で実行してください。",
            file=sys.stderr,
        )
        return 1

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))

    if _port_is_taken(host, port):
        print(
            f"ポート {port} は既に使われています。\n"
            f"  - 前に起動したサーバーが残っていないか確認してください\n"
            f"  - 別のポートで動かすなら: PORT=8001 python run.py",
            file=sys.stderr,
        )
        return 1

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=_bool("RELOAD", True),
        reload_dirs=RELOAD_DIRS,
        reload_excludes=RELOAD_EXCLUDES,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
