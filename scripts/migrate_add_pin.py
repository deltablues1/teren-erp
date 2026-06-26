"""Migracija: dodaje pin_hash kolonu na tablicu radnik.

Pokreni jednom:  py scripts/migrate_add_pin.py
Siguran za višestruko pokretanje (IF NOT EXISTS).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import engine


def run() -> None:
    with engine.connect() as conn:
        conn.execute(
            __import__("sqlalchemy").text(
                "ALTER TABLE radnik ADD COLUMN IF NOT EXISTS pin_hash VARCHAR(64)"
            )
        )
        conn.commit()
    print("OK: pin_hash kolona dodana (ili je već postojala).")


if __name__ == "__main__":
    run()
