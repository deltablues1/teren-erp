"""Minimalno skladište: ledger transakcija (primka / zaduženje / povrat).

Stanje se NE pohranjuje — izračunava se iz transakcija:
    stanje(lokacija) = Σ ulaza − Σ izlaza, grupirano po artiklu.

Lokacije: 'dobavljac' | 'skladiste' (jedno centralno) | 'radnik' (telegram_id)
| 'gradiliste' (projekt_key). Samo Postgres backend (kao katalog i zadaci).
Buduće: otpremnica sa slike → automatska primka (vision dio već postoji).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from config import DATA_BACKEND

log = logging.getLogger(__name__)

ENABLED = DATA_BACKEND == "postgres"

SKLADISTE = ("skladiste", "")  # centralna lokacija (tip, id)


def resolve_artikl(tekst: str) -> tuple[int | None, str, str]:
    """Iz unosa (naziv ili šifra) nađi artikl. Vraća (artikl_id, opis, jm);
    artikl_id=None ako nema poklapanja — materijal se vodi po opisu."""
    tekst = (tekst or "").strip()
    if not ENABLED or not tekst:
        return None, tekst, ""
    from sqlalchemy import func, select
    from services import db
    from services.models import Artikl

    with db.session() as s:
        a = s.scalar(select(Artikl).where(func.lower(Artikl.naziv) == tekst.lower()))
        if not a and len(tekst) <= 120:
            a = s.scalar(select(Artikl).where(Artikl.sifra == tekst))
        if a:
            return a.id, a.naziv, a.jm
    return None, tekst, ""


def _add(
    tip: str,
    artikl_tekst: str,
    kolicina: float,
    *,
    iz: tuple[str, str],
    u: tuple[str, str],
    jm: str = "",
    dokument: str = "",
    napomena: str = "",
    created_by: int = 0,
) -> dict[str, Any] | None:
    if not ENABLED or kolicina <= 0:
        return None
    from services import db
    from services.models import SkladisteTransakcija

    artikl_id, opis, kat_jm = resolve_artikl(artikl_tekst)
    with db.session() as s:
        t = SkladisteTransakcija(
            tip=tip,
            artikl_id=artikl_id,
            opis=opis,
            jm=jm or kat_jm,
            kolicina=float(kolicina),
            iz_tip=iz[0], iz_id=str(iz[1]),
            u_tip=u[0], u_id=str(u[1]),
            dokument=dokument.strip(),
            napomena=napomena.strip(),
            created_by=int(created_by),
        )
        s.add(t)
        s.flush()
        return {"id": t.id, "artikl_id": artikl_id, "opis": opis, "jm": t.jm}


def primka(
    artikl_tekst: str, kolicina: float, *,
    dobavljac: str = "", na: tuple[str, str] = SKLADISTE,
    jm: str = "", dokument: str = "", napomena: str = "", created_by: int = 0,
) -> dict[str, Any] | None:
    """Ulaz robe: dobavljač → skladište (ili direktno gradilište)."""
    return _add(
        "primka", artikl_tekst, kolicina,
        iz=("dobavljac", dobavljac.strip()), u=na,
        jm=jm, dokument=dokument, napomena=napomena, created_by=created_by,
    )


def zaduzi(
    artikl_tekst: str, kolicina: float, *,
    na_tip: str, na_id: str,
    jm: str = "", napomena: str = "", created_by: int = 0,
) -> dict[str, Any] | None:
    """Izlaz iz skladišta: skladište → radnik ili gradilište."""
    return _add(
        "zaduzenje", artikl_tekst, kolicina,
        iz=SKLADISTE, u=(na_tip, na_id),
        jm=jm, napomena=napomena, created_by=created_by,
    )


def povrat(
    artikl_tekst: str, kolicina: float, *,
    od_tip: str, od_id: str,
    jm: str = "", napomena: str = "", created_by: int = 0,
) -> dict[str, Any] | None:
    """Povrat u skladište: radnik/gradilište → skladište."""
    return _add(
        "povrat", artikl_tekst, kolicina,
        iz=(od_tip, od_id), u=SKLADISTE,
        jm=jm, napomena=napomena, created_by=created_by,
    )


def prijenos(
    artikl_tekst: str, kolicina: float, *,
    od_tip: str, od_id: str, na_tip: str, na_id: str,
    jm: str = "", napomena: str = "", created_by: int = 0,
) -> dict[str, Any] | None:
    """Prijenos između radnika/gradilišta (npr. krivo zaduženje, ili radnik
    nosi materijal s jednog gradilišta na drugo)."""
    return _add(
        "prijenos", artikl_tekst, kolicina,
        iz=(od_tip, od_id), u=(na_tip, na_id),
        jm=jm, napomena=napomena, created_by=created_by,
    )


# --- potrošnja (ugrađeni materijal iz izvještaja) ------------------------
def _artikl_naziv(artikl_id: int) -> str:
    from services import db
    from services.models import Artikl

    with db.session() as s:
        a = s.get(Artikl, artikl_id)
        return a.naziv if a else ""


def _kandidat_na_gradilistu(opis: str, saldo_po_artiklu: dict[int, float]) -> int | None:
    """Token-pretragom nađi artikl koji odgovara opisu I postoji na gradilištu
    s pozitivnim saldom (najbolji kandidat)."""
    if not saldo_po_artiklu:
        return None
    from services import db_backend

    for k in db_backend.find_artikl_candidates(opis, limit=15):
        if saldo_po_artiklu.get(k["id"], 0) > 0:
            return k["id"]
    return None


def potrosnja_iz_izvjestaja(
    projekt_key: str,
    materijali: list[dict[str, Any]],
    created_by: int = 0,
) -> tuple[int, int]:
    """Ugrađeni materijal s potvrđenog izvještaja skini sa zaduženja
    gradilišta (gradilište → 'ugradnja'). Saldo smije otići u minus —
    to je signal da je materijal trošen mimo evidencije skladišta.

    Matching kaskada po stavci: katalog_artikl_id (postavio AI kod
    parsiranja) → točan naziv/šifra → token-kandidat među artiklima
    koje gradilište stvarno ima. Bez pogotka → stavka se preskače
    (dnevnik/knjiga je svejedno imaju — samo se skladište ne dira).

    Vraća (skinuto, preskočeno)."""
    if not ENABLED or not materijali:
        return 0, 0

    saldo_po_artiklu = {
        st["artikl_id"]: st["kolicina"]
        for st in stanje("gradiliste", projekt_key)
        if st["artikl_id"]
    }

    skinuto = preskoceno = 0
    for m in materijali:
        try:
            kol = float(m.get("kolicina") or 0)
        except (TypeError, ValueError):
            kol = 0.0
        if kol <= 0:
            continue

        opis = str(m.get("opis") or "").strip()
        artikl_id = m.get("katalog_artikl_id")
        if not artikl_id:
            artikl_id, _, _ = resolve_artikl(opis)
        if not artikl_id:
            artikl_id = _kandidat_na_gradilistu(opis, saldo_po_artiklu)
        if not artikl_id:
            preskoceno += 1
            continue

        naziv = _artikl_naziv(int(artikl_id))
        if not naziv:
            preskoceno += 1
            continue
        _add(
            "potrosnja", naziv, kol,
            iz=("gradiliste", projekt_key), u=("ugradnja", projekt_key),
            jm=str(m.get("jm") or ""),
            napomena=f"ugrađeno (izvještaj): {opis}"[:500],
            created_by=created_by,
        )
        saldo_po_artiklu[int(artikl_id)] = saldo_po_artiklu.get(int(artikl_id), 0) - kol
        skinuto += 1
    return skinuto, preskoceno


# --- stanja ------------------------------------------------------------------
def _kljuc(t) -> tuple:
    """Materijal identificira artikl_id, a za ne-katalog stavke opis."""
    return (t.artikl_id, "" if t.artikl_id else t.opis.lower())


def stanje(lok_tip: str = "skladiste", lok_id: str = "") -> list[dict[str, Any]]:
    """Trenutno stanje lokacije: [{artikl_id, opis, jm, kolicina}], samo ≠0."""
    if not ENABLED:
        return []
    from sqlalchemy import or_, select
    from services import db
    from services.models import SkladisteTransakcija

    lok_id = str(lok_id)
    with db.session() as s:
        rows = s.scalars(
            select(SkladisteTransakcija).where(or_(
                (SkladisteTransakcija.u_tip == lok_tip)
                & (SkladisteTransakcija.u_id == lok_id),
                (SkladisteTransakcija.iz_tip == lok_tip)
                & (SkladisteTransakcija.iz_id == lok_id),
            ))
        ).all()

        saldo: dict[tuple, float] = defaultdict(float)
        info: dict[tuple, dict[str, Any]] = {}
        for t in rows:
            k = _kljuc(t)
            if t.u_tip == lok_tip and t.u_id == lok_id:
                saldo[k] += t.kolicina
            if t.iz_tip == lok_tip and t.iz_id == lok_id:
                saldo[k] -= t.kolicina
            # zadnja viđena verzija opisa/jm
            info[k] = {"artikl_id": t.artikl_id, "opis": t.opis, "jm": t.jm}

        out = [
            {**info[k], "kolicina": round(q, 3)}
            for k, q in saldo.items()
            if abs(q) > 1e-9
        ]
        out.sort(key=lambda x: x["opis"].lower())
        return out


def zaduzenja_pregled() -> dict[str, list[dict[str, Any]]]:
    """Sva trenutna zaduženja: {'radnici': [{id, naziv, stavke}], 'gradilista': [...]}."""
    if not ENABLED:
        return {"radnici": [], "gradilista": []}
    from sqlalchemy import or_, select
    from services import db
    from services.models import Projekt, Radnik, SkladisteTransakcija

    with db.session() as s:
        rows = s.scalars(select(SkladisteTransakcija).where(or_(
            SkladisteTransakcija.u_tip.in_(("radnik", "gradiliste")),
            SkladisteTransakcija.iz_tip.in_(("radnik", "gradiliste")),
        ))).all()

        lokacije: set[tuple[str, str]] = set()
        for t in rows:
            if t.u_tip in ("radnik", "gradiliste"):
                lokacije.add((t.u_tip, t.u_id))
            if t.iz_tip in ("radnik", "gradiliste"):
                lokacije.add((t.iz_tip, t.iz_id))

        imena_radnika = {str(r.telegram_id): r.ime for r in s.scalars(select(Radnik)).all()}
        nazivi_projekata = {p.key: p.naziv for p in s.scalars(select(Projekt)).all()}

    out: dict[str, list[dict[str, Any]]] = {"radnici": [], "gradilista": []}
    for lok_tip, lok_id in sorted(lokacije):
        stavke = stanje(lok_tip, lok_id)
        if not stavke:
            continue
        if lok_tip == "radnik":
            out["radnici"].append({
                "id": lok_id,
                "naziv": imena_radnika.get(lok_id, f"ID {lok_id}"),
                "stavke": stavke,
            })
        else:
            out["gradilista"].append({
                "id": lok_id,
                "naziv": nazivi_projekata.get(lok_id, lok_id),
                "stavke": stavke,
            })
    return out


def promet(limit: int = 30) -> list[dict[str, Any]]:
    """Zadnje transakcije, s čitljivim nazivima lokacija."""
    if not ENABLED:
        return []
    from sqlalchemy import select
    from services import db
    from services.models import Projekt, Radnik, SkladisteTransakcija

    with db.session() as s:
        rows = s.scalars(
            select(SkladisteTransakcija)
            .order_by(SkladisteTransakcija.id.desc()).limit(limit)
        ).all()
        imena = {str(r.telegram_id): r.ime for r in s.scalars(select(Radnik)).all()}
        nazivi = {p.key: p.naziv for p in s.scalars(select(Projekt)).all()}

    def _lok(tip: str, lid: str) -> str:
        if tip == "skladiste":
            return "Skladište"
        if tip == "radnik":
            return imena.get(lid, f"ID {lid}")
        if tip == "gradiliste":
            return nazivi.get(lid, lid)
        if tip == "dobavljac":
            return lid or "dobavljač"
        if tip == "ugradnja":
            return f"Ugrađeno ({nazivi.get(lid, lid)})"
        return f"{tip}:{lid}"

    return [
        {
            "id": t.id,
            "datum": t.created_at.strftime("%d.%m.%Y %H:%M") if t.created_at else "",
            "tip": t.tip,
            "opis": t.opis,
            "kolicina": t.kolicina,
            "jm": t.jm,
            "iz": _lok(t.iz_tip, t.iz_id),
            "u": _lok(t.u_tip, t.u_id),
            "dokument": t.dokument,
            "napomena": t.napomena,
        }
        for t in rows
    ]


# --- pomoćno za forme/bot ------------------------------------------------
def list_radnici_svi() -> list[dict[str, Any]]:
    if not ENABLED:
        return []
    from sqlalchemy import select
    from services import db
    from services.models import Radnik

    with db.session() as s:
        rows = s.scalars(
            select(Radnik).where(Radnik.aktivan.is_(True)).order_by(Radnik.ime)
        ).all()
        return [{"telegram_id": r.telegram_id, "ime": r.ime} for r in rows]


def list_artikli_nazivi(limit: int = 3000) -> list[str]:
    """Nazivi artikala za datalist autocomplete u panelu."""
    if not ENABLED:
        return []
    from sqlalchemy import select
    from services import db
    from services.models import Artikl

    with db.session() as s:
        rows = s.scalars(
            select(Artikl.naziv).where(Artikl.aktivan.is_(True))
            .order_by(Artikl.naziv).limit(limit)
        ).all()
        return list(rows)
