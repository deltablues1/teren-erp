"""Migracija: dodaje default_vozilo_id kolonu na tablicu radnik.

Pokreni jednom:  py scripts/migrate_add_default_vozilo.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sqlalchemy
from services.db import engine

def run() -> None:
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text(
            "ALTER TABLE radnik ADD COLUMN IF NOT EXISTS "
            "default_vozilo_id INTEGER REFERENCES vozilo(id) ON DELETE SET NULL"
        ))
        conn.commit()
    print("OK: default_vozilo_id kolona dodana.")

if __name__ == "__main__":
    run()
