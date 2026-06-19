"""Zadaci: voditelj zada zadatak za projekt, radnik ga rješava u botu.

CRUD sloj koji dijele bot (handlers/zadaci.py) i web panel (web/app.py).
Kao i katalog, zadaci postoje samo na Postgres backendu; u sheets modu
sve funkcije vraćaju prazno / ne rade ništa.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any

from config import DATA_BACKEND

log = logging.getLogger(__name__)

ENABLED = DATA_BACKEND == "postgres"

# u koliko sati se odgođeni zadatak ponovno pojavi
SNOOZE_DO_SATI = 7


def _zadatak_to_dict(z, *, broj_komentara: int = 0) -> dict[str, Any]:
    return {
        "id": z.id,
        "projekt_key": z.projekt_key,
        "tekst": z.tekst,
        "telegram_id": z.telegram_id,
        "created_by": z.created_by,
        "created_at": z.created_at.strftime("%d.%m.%Y %H:%M") if z.created_at else "",
        "rok": z.rok.strftime("%d.%m.%Y") if z.rok else "",
        "status": z.status,
        "snooze_until": z.snooze_until.isoformat(timespec="minutes") if z.snooze_until else "",
        "completed_at": z.completed_at.strftime("%d.%m.%Y %H:%M") if z.completed_at else "",
        "completed_by": z.completed_by,
        "broj_komentara": broj_komentara,
    }


def create(
    projekt_key: str,
    tekst: str,
    *,
    created_by: int,
    telegram_id: int | None = None,
    rok: date | None = None,
) -> dict[str, Any] | None:
    if not ENABLED or not tekst.strip():
        return None
    from services import db
    from services.models import Zadatak

    with db.session() as s:
        z = Zadatak(
            projekt_key=projekt_key,
            tekst=tekst.strip(),
            telegram_id=telegram_id,
            created_by=created_by,
            rok=rok,
            status="otvoren",
        )
        s.add(z)
        s.flush()
        return _zadatak_to_dict(z)


def get(zadatak_id: int) -> dict[str, Any] | None:
    if not ENABLED:
        return None
    from sqlalchemy import func, select
    from services import db
    from services.models import Zadatak, ZadatakKomentar

    with db.session() as s:
        z = s.get(Zadatak, zadatak_id)
        if not z:
            return None
        n = s.scalar(
            select(func.count()).select_from(ZadatakKomentar)
            .where(ZadatakKomentar.zadatak_id == zadatak_id)
        ) or 0
        return _zadatak_to_dict(z, broj_komentara=n)


def list_otvoreni(
    projekt_key: str | None = None,
    telegram_id: int | None = None,
    include_snoozed: bool = False,
) -> list[dict[str, Any]]:
    """Otvoreni zadaci. telegram_id filtrira na 'za sve' + 'baš za njega'.
    Odgođeni (snooze_until u budućnosti) se preskaču osim ako include_snoozed."""
    if not ENABLED:
        return []
    from sqlalchemy import func, or_, select
    from services import db
    from services.models import Zadatak, ZadatakKomentar

    with db.session() as s:
        stmt = select(Zadatak).where(Zadatak.status == "otvoren")
        if projekt_key:
            stmt = stmt.where(Zadatak.projekt_key == projekt_key)
        if telegram_id is not None:
            stmt = stmt.where(or_(
                Zadatak.telegram_id.is_(None),
                Zadatak.telegram_id == int(telegram_id),
            ))
        if not include_snoozed:
            stmt = stmt.where(or_(
                Zadatak.snooze_until.is_(None),
                Zadatak.snooze_until <= datetime.now(),
            ))
        rows = s.scalars(stmt.order_by(Zadatak.rok.is_(None), Zadatak.rok, Zadatak.id)).all()
        if not rows:
            return []
        ids = [z.id for z in rows]
        counts = dict(s.execute(
            select(ZadatakKomentar.zadatak_id, func.count())
            .where(ZadatakKomentar.zadatak_id.in_(ids))
            .group_by(ZadatakKomentar.zadatak_id)
        ).all())
        return [_zadatak_to_dict(z, broj_komentara=counts.get(z.id, 0)) for z in rows]


def list_nedavno_gotovi(limit: int = 15) -> list[dict[str, Any]]:
    if not ENABLED:
        return []
    from sqlalchemy import select
    from services import db
    from services.models import Zadatak

    with db.session() as s:
        rows = s.scalars(
            select(Zadatak).where(Zadatak.status == "gotov")
            .order_by(Zadatak.completed_at.desc()).limit(limit)
        ).all()
        return [_zadatak_to_dict(z) for z in rows]


def zadaci_za_podsjetnik(include_rok: bool = True) -> list[dict[str, Any]]:
    """Zadaci za dnevni podsjetnik: odgođeni koji su dospjeli (snooze prošao)
    + (opcionalno) oni s rokom danas ili prekoračenim."""
    if not ENABLED:
        return []
    from sqlalchemy import or_, select
    from services import db
    from services.models import Zadatak

    now = datetime.now()
    uvjeti = [
        Zadatak.snooze_until.is_not(None) & (Zadatak.snooze_until <= now),
    ]
    if include_rok:
        uvjeti.append(Zadatak.rok.is_not(None) & (Zadatak.rok <= date.today()))

    with db.session() as s:
        rows = s.scalars(
            select(Zadatak)
            .where(Zadatak.status == "otvoren", or_(*uvjeti))
            .order_by(Zadatak.id)
        ).all()
        return [_zadatak_to_dict(z) for z in rows]


def ocisti_snooze(zadatak_id: int) -> None:
    """Nakon poslanog podsjetnika makni snooze da se isti odgođeni zadatak
    ne šalje ponovno (rok-podsjetnici se namjerno ponavljaju dnevno)."""
    if not ENABLED:
        return
    from services import db
    from services.models import Zadatak

    with db.session() as s:
        z = s.get(Zadatak, zadatak_id)
        if z:
            z.snooze_until = None


def oznaci_gotovo(zadatak_id: int, telegram_id: int) -> dict[str, Any] | None:
    """Označi gotovim. Vraća dict zadatka, ili None ako ne postoji/već gotov."""
    if not ENABLED:
        return None
    from services import db
    from services.models import Zadatak

    with db.session() as s:
        z = s.get(Zadatak, zadatak_id)
        if not z or z.status == "gotov":
            return None
        z.status = "gotov"
        z.completed_at = datetime.now()
        z.completed_by = int(telegram_id)
        s.flush()
        return _zadatak_to_dict(z)


def odgodi(zadatak_id: int) -> dict[str, Any] | None:
    """Sakrij zadatak do sutra u SNOOZE_DO_SATI sati."""
    if not ENABLED:
        return None
    from services import db
    from services.models import Zadatak

    sutra = datetime.combine(
        date.today() + timedelta(days=1), time(hour=SNOOZE_DO_SATI)
    )
    with db.session() as s:
        z = s.get(Zadatak, zadatak_id)
        if not z or z.status != "otvoren":
            return None
        z.snooze_until = sutra
        s.flush()
        return _zadatak_to_dict(z)


def dodaj_komentar(zadatak_id: int, telegram_id: int, ime: str, tekst: str) -> bool:
    if not ENABLED or not tekst.strip():
        return False
    from services import db
    from services.models import Zadatak, ZadatakKomentar

    with db.session() as s:
        if not s.get(Zadatak, zadatak_id):
            return False
        s.add(ZadatakKomentar(
            zadatak_id=zadatak_id,
            telegram_id=int(telegram_id),
            ime=ime.strip(),
            tekst=tekst.strip(),
        ))
        return True


def list_komentari(zadatak_id: int) -> list[dict[str, Any]]:
    if not ENABLED:
        return []
    from sqlalchemy import select
    from services import db
    from services.models import ZadatakKomentar

    with db.session() as s:
        rows = s.scalars(
            select(ZadatakKomentar)
            .where(ZadatakKomentar.zadatak_id == zadatak_id)
            .order_by(ZadatakKomentar.id)
        ).all()
        return [
            {
                "ime": k.ime,
                "tekst": k.tekst,
                "created_at": k.created_at.strftime("%d.%m.%Y %H:%M") if k.created_at else "",
            }
            for k in rows
        ]


# --- mapiranje poslanih Telegram poruka na zadatke (za reply-detekciju) ------
def zabiljezi_poruku(zadatak_id: int, chat_id: int, message_id: int) -> None:
    if not ENABLED:
        return
    from services import db
    from services.models import ZadatakPoruka

    with db.session() as s:
        s.add(ZadatakPoruka(
            zadatak_id=zadatak_id, chat_id=int(chat_id), message_id=int(message_id)
        ))


def zadatak_za_reply(chat_id: int, message_id: int) -> dict[str, Any] | None:
    """Ako je poruka na koju korisnik odgovara bila poruka zadatka, vrati zadatak."""
    if not ENABLED:
        return None
    from sqlalchemy import select
    from services import db
    from services.models import ZadatakPoruka

    with db.session() as s:
        zp = s.scalar(
            select(ZadatakPoruka).where(
                ZadatakPoruka.chat_id == int(chat_id),
                ZadatakPoruka.message_id == int(message_id),
            )
        )
        if not zp:
            return None
    return get(zp.zadatak_id)


# --- pomoćno --------------------------------------------------------------
def primatelji(projekt_key: str, telegram_id: int | None = None) -> list[dict[str, Any]]:
    """Kome poslati push za zadatak: konkretni radnik ili svi na projektu."""
    if not ENABLED:
        return []
    from sqlalchemy import select
    from services import db
    from services.models import ProjektRadnik, Radnik

    with db.session() as s:
        stmt = (
            select(Radnik)
            .join(ProjektRadnik, ProjektRadnik.telegram_id == Radnik.telegram_id)
            .where(ProjektRadnik.projekt_key == projekt_key, Radnik.aktivan.is_(True))
        )
        if telegram_id is not None:
            stmt = stmt.where(Radnik.telegram_id == int(telegram_id))
        rows = s.scalars(stmt).all()
        return [{"telegram_id": r.telegram_id, "ime": r.ime} for r in rows]


def ime_radnika(telegram_id: int | None) -> str:
    if not ENABLED or telegram_id is None:
        return ""
    from services import db
    from services.models import Radnik

    with db.session() as s:
        r = s.get(Radnik, int(telegram_id))
        return r.ime if r else ""
