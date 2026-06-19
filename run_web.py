"""Pokreni web admin panel.

    py run_web.py        →  http://127.0.0.1:8000

Preduvjet: DATA_BACKEND=postgres i DATABASE_URL u .env (panel čita iz Postgresa).
Ostavi prozor otvoren dok koristiš panel (kao i bota).
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
_fh = RotatingFileHandler(
    Path(__file__).resolve().parent / "web.log",
    maxBytes=2_000_000, backupCount=3, encoding="utf-8",
)
_fh.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
logging.getLogger().addHandler(_fh)

if __name__ == "__main__":
    print("Web panel: http://127.0.0.1:8000  (Ctrl+C za prekid)")
    uvicorn.run("web.app:app", host="127.0.0.1", port=8000, reload=False)
