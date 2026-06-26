"""Migracija: kreira tablice vozilo i putni_nalog.

Pokreni jednom:  py scripts/migrate_add_vozilo.py
Siguran za višestruko pokretanje (CREATE TABLE IF NOT EXISTS).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlalchemy
from services.db import engine


def run() -> None:
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text("""
            CREATE TABLE IF NOT EXISTS vozilo (
                id          SERIAL PRIMARY KEY,
                naziv       VARCHAR(100) NOT NULL DEFAULT '',
                registracija VARCHAR(20) NOT NULL DEFAULT '',
                km_stanje   FLOAT NOT NULL DEFAULT 0,
                aktivno     BOOLEAN NOT NULL DEFAULT TRUE
            )
        """))
        conn.execute(sqlalchemy.text("""
            CREATE TABLE IF NOT EXISTS putni_nalog (
                id                  SERIAL PRIMARY KEY,
                radnik_telegram_id  BIGINT NOT NULL REFERENCES radnik(telegram_id) ON DELETE CASCADE,
                vozilo_id           INTEGER NOT NULL REFERENCES vozilo(id) ON DELETE RESTRICT,
                datum               DATE NOT NULL DEFAULT CURRENT_DATE,
                projekt_key         VARCHAR(100),
                polaziste           VARCHAR(200) NOT NULL DEFAULT '',
                odrediste           VARCHAR(200) NOT NULL DEFAULT '',
                km_start            FLOAT NOT NULL DEFAULT 0,
                km_kraj             FLOAT NOT NULL DEFAULT 0,
                gorivo_l            FLOAT,
                gorivo_eur          FLOAT,
                napomena            TEXT,
                created_at          TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """))
        conn.execute(sqlalchemy.text(
            "CREATE INDEX IF NOT EXISTS ix_putni_nalog_radnik ON putni_nalog(radnik_telegram_id)"
        ))
        conn.execute(sqlalchemy.text(
            "CREATE INDEX IF NOT EXISTS ix_putni_nalog_vozilo ON putni_nalog(vozilo_id)"
        ))
        conn.commit()
    print("OK: tablice vozilo i putni_nalog kreirane (ili su već postojale).")


if __name__ == "__main__":
    run()
