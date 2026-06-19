"""Engine, sesija i inicijalizacija sheme za Postgres backend (Faza 1).

DATABASE_URL se čita iz .env, npr.:
    postgresql+psycopg://postgres:LOZINKA@localhost:5432/teren_bot
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import DATABASE_URL
from services.models import Base

log = logging.getLogger(__name__)

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL nije postavljen u .env. Primjer:\n"
        "  DATABASE_URL=postgresql+psycopg://postgres:LOZINKA@localhost:5432/teren_bot"
    )

# pool_pre_ping: izbjegni 'server closed connection' nakon dugog mirovanja bota.
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session() -> Iterator[Session]:
    """Sesija s automatskim commit/rollback/close."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def init_db() -> None:
    """Kreiraj sve tablice ako ne postoje (create_all).

    Za prvu verziju koristimo create_all umjesto Alembic migracija — jednostavnije
    za lokalni setup. Alembic se uvodi pri prvoj promjeni sheme (npr. Faza 3:
    katalog stavki) preko `alembic stamp` na postojeću shemu.
    """
    Base.metadata.create_all(engine)
    log.info("Tablice kreirane/provjerene u %s", engine.url.render_as_string(hide_password=True))
