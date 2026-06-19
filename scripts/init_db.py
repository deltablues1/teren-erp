"""CLI: kreiraj sve Postgres tablice (create_all).

Preduvjet: u .env postavi DATABASE_URL (vidi .env.example).

Korištenje:
    py scripts/init_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Windows konzola je cp1250 — prebaci na UTF-8 da ✅/č/š ne pucaju u ispisu.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import db  # noqa: E402


def main() -> None:
    print("Kreiram tablice u bazi...")
    db.init_db()
    url = db.engine.url.render_as_string(hide_password=True)
    print(f"✅ Gotovo. Tablice spremne u: {url}")


if __name__ == "__main__":
    main()
