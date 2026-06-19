"""Ponude kupcima: stavke iz kataloga (prodajna cijena se povuče sama)
ili slobodan unos (rad/usluga). Samo Postgres backend.

Broj ponude: P-{godina}-{redni:03d}. Cijene su bez PDV-a; PDV se
obračunava na ukupno (PDV_STOPA).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from config import DATA_BACKEND

log = logging.getLogger(__name__)

ENABLED = DATA_BACKEND == "postgres"

PDV_STOPA = 25.0
STATUSI = ("nacrt", "poslana", "prihvacena", "odbijena")


def prodajna_cijena(artikl_id: int) -> float | None:
    """Korisnikova prodajna cijena artikla (cjenik tip='prodajni');
    fallback: najniža nabavna (bolje išta nego prazno — vidljivo u panelu)."""
    if not ENABLED:
        return None
    from sqlalchemy import func, select
    from services import db
    from services.models import Cjenik, CjenikStavka

    with db.session() as s:
        prodajna = s.scalar(
            select(CjenikStavka.cijena)
            .join(Cjenik, Cjenik.id == CjenikStavka.cjenik_id)
            .where(
                CjenikStavka.artikl_id == artikl_id,
                Cjenik.tip == "prodajni",
                CjenikStavka.cijena.is_not(None),
            )
            .limit(1)
        )
        if prodajna is not None:
            return float(prodajna)
        nabavna = s.scalar(
            select(func.min(CjenikStavka.cijena))
            .join(Cjenik, Cjenik.id == CjenikStavka.cjenik_id)
            .where(
                CjenikStavka.artikl_id == artikl_id,
                Cjenik.tip != "prodajni",
                CjenikStavka.cijena.is_not(None),
            )
        )
        return float(nabavna) if nabavna is not None else None


def _novi_broj(s) -> str:
    from sqlalchemy import func, select
    from services.models import Ponuda

    godina = date.today().year
    n = s.scalar(
        select(func.count()).select_from(Ponuda)
        .where(Ponuda.broj.like(f"P-{godina}-%"))
    ) or 0
    return f"P-{godina}-{n + 1:03d}"


def create(kupac_naziv: str, predmet: str = "") -> int | None:
    if not ENABLED or not kupac_naziv.strip():
        return None
    from services import db
    from services.models import Ponuda

    with db.session() as s:
        p = Ponuda(
            broj=_novi_broj(s),
            kupac_naziv=kupac_naziv.strip()[:255],
            predmet=predmet.strip()[:512],
        )
        s.add(p)
        s.flush()
        return p.id


def _stavka_to_dict(st) -> dict[str, Any]:
    iznos = (st.cijena * st.kolicina) if st.cijena is not None else None
    return {
        "id": st.id,
        "artikl_id": st.artikl_id,
        "opis": st.opis,
        "jm": st.jm,
        "kolicina": st.kolicina,
        "cijena": st.cijena,
        "iznos": round(iznos, 2) if iznos is not None else None,
    }


def _totali(stavke: list[dict[str, Any]]) -> dict[str, float]:
    osnovica = sum(s["iznos"] or 0 for s in stavke)
    pdv = osnovica * PDV_STOPA / 100
    return {
        "osnovica": round(osnovica, 2),
        "pdv": round(pdv, 2),
        "ukupno": round(osnovica + pdv, 2),
    }


def _ponuda_to_dict(p, stavke: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    d = {
        "id": p.id,
        "broj": p.broj,
        "kupac_naziv": p.kupac_naziv,
        "kupac_adresa": p.kupac_adresa,
        "kupac_oib": p.kupac_oib,
        "predmet": p.predmet,
        "napomena": p.napomena,
        "status": p.status,
        "valjanost_dana": p.valjanost_dana,
        "datum": p.datum.strftime("%d.%m.%Y") if p.datum else "",
    }
    if stavke is not None:
        d["stavke"] = stavke
        d.update(_totali(stavke))
    return d


def get(ponuda_id: int) -> dict[str, Any] | None:
    if not ENABLED:
        return None
    from sqlalchemy import select
    from services import db
    from services.models import Ponuda, PonudaStavka

    with db.session() as s:
        p = s.get(Ponuda, ponuda_id)
        if not p:
            return None
        stavke = [
            _stavka_to_dict(st)
            for st in s.scalars(
                select(PonudaStavka)
                .where(PonudaStavka.ponuda_id == ponuda_id)
                .order_by(PonudaStavka.redoslijed, PonudaStavka.id)
            ).all()
        ]
        return _ponuda_to_dict(p, stavke)


def list_ponude() -> list[dict[str, Any]]:
    if not ENABLED:
        return []
    from sqlalchemy import select
    from services import db
    from services.models import Ponuda, PonudaStavka

    with db.session() as s:
        ponude = s.scalars(select(Ponuda).order_by(Ponuda.id.desc())).all()
        if not ponude:
            return []
        sve_stavke = s.scalars(select(PonudaStavka)).all()
        po_ponudi: dict[int, list[dict[str, Any]]] = {}
        for st in sve_stavke:
            po_ponudi.setdefault(st.ponuda_id, []).append(_stavka_to_dict(st))
        out = []
        for p in ponude:
            stavke = po_ponudi.get(p.id, [])
            d = _ponuda_to_dict(p)
            d.update(_totali(stavke))
            d["n_stavki"] = len(stavke)
            out.append(d)
        return out


def update_header(
    ponuda_id: int, *, kupac_naziv: str, kupac_adresa: str, kupac_oib: str,
    predmet: str, napomena: str, valjanost_dana: int,
) -> bool:
    if not ENABLED:
        return False
    from services import db
    from services.models import Ponuda

    with db.session() as s:
        p = s.get(Ponuda, ponuda_id)
        if not p:
            return False
        p.kupac_naziv = kupac_naziv.strip()[:255]
        p.kupac_adresa = kupac_adresa.strip()[:255]
        p.kupac_oib = kupac_oib.strip()[:20]
        p.predmet = predmet.strip()[:512]
        p.napomena = napomena.strip()
        p.valjanost_dana = max(1, int(valjanost_dana or 30))
        return True


def set_status(ponuda_id: int, status: str) -> bool:
    if not ENABLED or status not in STATUSI:
        return False
    from services import db
    from services.models import Ponuda

    with db.session() as s:
        p = s.get(Ponuda, ponuda_id)
        if not p:
            return False
        p.status = status
        return True


def dodaj_stavku(
    ponuda_id: int, artikl_tekst: str, kolicina: float,
    cijena: float | None = None, jm: str = "",
) -> bool:
    """Stavka iz kataloga (naziv/šifra → artikl + prodajna cijena) ili
    slobodan opis. Eksplicitna cijena ima prednost pred katalogom."""
    if not ENABLED or not artikl_tekst.strip() or kolicina <= 0:
        return False
    from sqlalchemy import func, select
    from services import db, skladiste
    from services.models import Ponuda, PonudaStavka

    artikl_id, opis, kat_jm = skladiste.resolve_artikl(artikl_tekst)
    if cijena is None and artikl_id:
        cijena = prodajna_cijena(artikl_id)

    with db.session() as s:
        if not s.get(Ponuda, ponuda_id):
            return False
        sljedeci = (s.scalar(
            select(func.max(PonudaStavka.redoslijed))
            .where(PonudaStavka.ponuda_id == ponuda_id)
        ) or 0) + 1
        s.add(PonudaStavka(
            ponuda_id=ponuda_id,
            redoslijed=sljedeci,
            artikl_id=artikl_id,
            opis=opis[:512],
            jm=(jm or kat_jm)[:40],
            kolicina=float(kolicina),
            cijena=cijena,
        ))
        return True


def update_stavku(stavka_id: int, kolicina: float, cijena: float | None) -> bool:
    if not ENABLED:
        return False
    from services import db
    from services.models import PonudaStavka

    with db.session() as s:
        st = s.get(PonudaStavka, stavka_id)
        if not st:
            return False
        if kolicina > 0:
            st.kolicina = float(kolicina)
        st.cijena = cijena
        return True


def obrisi_stavku(stavka_id: int) -> int | None:
    """Obriši stavku; vrati ponuda_id (za redirect) ili None."""
    if not ENABLED:
        return None
    from services import db
    from services.models import PonudaStavka

    with db.session() as s:
        st = s.get(PonudaStavka, stavka_id)
        if not st:
            return None
        pid = st.ponuda_id
        s.delete(st)
        return pid
