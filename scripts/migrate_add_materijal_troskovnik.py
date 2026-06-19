"""Migracija: dodaj Materijal.troskovnik_stavka_id (FK → troskovnik_stavka).

create_all ne mijenja postojeće tablice, pa stupac dodajemo ručno. Idempotentno —
provjeri postoji li stupac prije ALTER-a. Pokreni jednom:
    py scripts/migrate_add_materijal_troskovnik.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from services import db  # noqa: E402


def column_exists() -> bool:
    with db.engine.connect() as c:
        row = c.execute(text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='materijal' AND column_name='troskovnik_stavka_id'"
        )).first()
        return row is not None


def main() -> None:
    if column_exists():
        print("Stupac materijal.troskovnik_stavka_id već postoji — preskačem.")
        return
    with db.engine.begin() as c:
        c.execute(text(
            "ALTER TABLE materijal ADD COLUMN troskovnik_stavka_id INTEGER "
            "REFERENCES troskovnik_stavka(id) ON DELETE SET NULL"
        ))
        c.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_materijal_troskovnik_stavka_id "
            "ON materijal (troskovnik_stavka_id)"
        ))
    print("✅ Dodan stupac materijal.troskovnik_stavka_id + indeks.")


if __name__ == "__main__":
    main()
