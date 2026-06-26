"""Migracija: dodaje push_subscription kolonu na tablicu radnik.

Pokreni jednom:  py scripts/migrate_add_push_subscription.py
Siguran za višestruko pokretanje (IF NOT EXISTS).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlalchemy
from services.db import engine


def run() -> None:
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text(
            "ALTER TABLE radnik ADD COLUMN IF NOT EXISTS push_subscription TEXT"
        ))
        conn.commit()
    print("OK: push_subscription kolona dodana (ili je već postojala).")


if __name__ == "__main__":
    run()
