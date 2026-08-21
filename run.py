"""開発用の起動スクリプト。

    python run.py

`app/main.py` を直接叩くと ImportError になる。
`app` はパッケージで、`main.py` は `from .config import ...` という相対 import を
使っているため、単体のスクリプトとして実行すると親パッケージが分からない
（"attempted relative import with no known parent package"）。
必ずリポジトリ直下から、パッケージとして読み込ませる必要がある。

同じことを uvicorn のコマンドで書くとこうなる:

    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    reload_enabled = os.getenv("RELOAD", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    uvicorn.run("app.main:app", host=host, port=port, reload=reload_enabled)


if __name__ == "__main__":
    main()
