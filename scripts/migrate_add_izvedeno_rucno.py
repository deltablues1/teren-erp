"""Migracija: dodaj TroskovnikStavka.izvedeno_rucno (ručni override izvedenog).

Idempotentno. Pokreni jednom:
    py scripts/migrate_add_izvedeno_rucno.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from services import db  # noqa: E402


def column_exists() -> bool:
    with db.engine.connect() as c:
        return c.execute(text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='troskovnik_stavka' AND column_name='izvedeno_rucno'"
        )).first() is not None


def main() -> None:
    if column_exists():
        print("Stupac troskovnik_stavka.izvedeno_rucno već postoji — preskačem.")
        return
    with db.engine.begin() as c:
        c.execute(text(
            "ALTER TABLE troskovnik_stavka ADD COLUMN izvedeno_rucno DOUBLE PRECISION"
        ))
    print("✅ Dodan stupac troskovnik_stavka.izvedeno_rucno.")


if __name__ == "__main__":
    main()
