"""PostgreSQL backend — implementira isti "port" kao services/sheets.py.

repository.py bira ovaj modul kad je config.DATA_BACKEND == "postgres".
Sve get_* funkcije vraćaju dictove s ISTIM ključevima kao Sheets (npr.
'Opis stavke', 'Ključne riječi', 'Telegram_ID'), da docgen i claude_parser
rade bez promjene.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, func, or_, select

from services import db
from services.models import (
    Artikl,
    Cjenik,
    CjenikStavka,
    DnevnikUnos,
    Materijal,
    Partner,
    Projekt,
    ProjektRadnik,
    Radnik,
    Situacija,
    SituacijaStavka,
    TroskovnikStavka,
    Vrijeme,
)

log = logging.getLogger(__name__)


# --- helperi -----------------------------------------------------------------
def _to_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()


def _num_or_none(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _num(v: Any) -> float:
    n = _num_or_none(v)
    return 0.0 if n is None else n


def _clip(v: Any, maxlen: int) -> str:
    """Sigurno skrati string na duljinu kolone (izbjegni StringDataRightTruncation)."""
    s = "" if v is None else str(v).strip()
    return s[:maxlen]


# --- projekti ----------------------------------------------------------------
def list_projekti() -> list[dict[str, Any]]:
    with db.session() as s:
        rows = s.scalars(
            select(Projekt).where(Projekt.aktivan.is_(True)).order_by(Projekt.kreiran)
        ).all()
        return [p.to_dict() for p in rows]


def get_projekt(key: str) -> dict[str, Any] | None:
    with db.session() as s:
        p = s.get(Projekt, key)
        return p.to_dict() if p else None


def create_projekt(
    key: str,
    naziv: str,
    adresa: str = "",
    investitor: str = "",
    izvodac: str = "",
    nadzorni: str = "",
    broj_dozvole: str = "",
) -> dict[str, Any]:
    with db.session() as s:
        if s.get(Projekt, key):
            raise ValueError(f"Projekt '{key}' već postoji.")
        p = Projekt(
            key=key,
            naziv=naziv,
            adresa=adresa,
            investitor=investitor,
            izvodac=izvodac,
            nadzorni=nadzorni,
            broj_dozvole=broj_dozvole,
            spreadsheet_id="",
            spreadsheet_url="",
            kreiran=datetime.now(),
            aktivan=True,
        )
        s.add(p)
        s.flush()
        log.info("Kreiran projekt %s (Postgres)", key)
        return p.to_dict()


# --- troškovnik --------------------------------------------------------------
def get_troskovnik(projekt_key: str) -> list[dict[str, Any]]:
    with db.session() as s:
        rows = s.scalars(
            select(TroskovnikStavka)
            .where(TroskovnikStavka.projekt_key == projekt_key)
            .order_by(TroskovnikStavka.redoslijed, TroskovnikStavka.id)
        ).all()
        return [
            {
                "Šifra": r.sifra,
                "Sekcija": r.sekcija,
                "Pozicija": r.pozicija,
                "Opis stavke": r.opis,
                "JM": r.jm,
                "Ugovorena količina": "" if r.ugovorena_kolicina is None else r.ugovorena_kolicina,
                "Jedinična cijena": "" if r.jedinicna_cijena is None else r.jedinicna_cijena,
                "Tip": r.tip,
                "Ključne riječi": r.kljucne_rijeci,
                "Izvedeno": r.izvedeno,
                "Razlika": r.razlika,
            }
            for r in rows
        ]


def zadnja_situacija_broj(projekt_key: str) -> int | None:
    """Redni broj zadnje (najveće) situacije projekta, ili None ako ih nema."""
    with db.session() as s:
        return s.scalar(
            select(func.max(Situacija.broj)).where(Situacija.projekt_key == projekt_key)
        )


def situacija_kumulativ(projekt_key: str, broj: int) -> tuple[dict[str, float], dict[str, float]]:
    """Vrati ({sifra: kumulativ} za situaciju 'broj', {sifra: kumulativ} za prethodnu).
    Prazni dictovi ako situacija ne postoji. Koristi docgen da knjiga/situacija
    prikaže izvedene količine iz uvezenih situacija (ne iz materijala)."""
    with db.session() as s:
        def kum(b: int) -> dict[str, float]:
            if b < 1:
                return {}
            sit = s.scalar(select(Situacija).where(
                Situacija.projekt_key == projekt_key, Situacija.broj == b))
            if not sit:
                return {}
            return {
                ss.sifra: ss.kolicina_kumulativ
                for ss in s.scalars(select(SituacijaStavka).where(
                    SituacijaStavka.situacija_id == sit.id)).all()
            }
        return kum(broj), kum(broj - 1)


def replace_troskovnik(projekt_key: str, rows: list[list[Any]]) -> int:
    """Zamijeni cijeli troškovnik projekta. Svaki redak je lista u redoslijedu
    HEADERS[Troskovnik]: Šifra, Sekcija, Pozicija, Opis stavke, JM,
    Ugovorena količina, Jedinična cijena, Tip, Ključne riječi, Izvedeno, Razlika."""
    with db.session() as s:
        s.execute(
            delete(TroskovnikStavka).where(TroskovnikStavka.projekt_key == projekt_key)
        )
        for i, row in enumerate(rows):
            row = list(row) + [""] * (11 - len(row))  # padding ako je kraći
            s.add(TroskovnikStavka(
                projekt_key=projekt_key,
                redoslijed=i,
                sifra=str(row[0] or ""),
                sekcija=str(row[1] or ""),
                pozicija=str(row[2] or ""),
                opis=str(row[3] or ""),
                jm=str(row[4] or ""),
                ugovorena_kolicina=_num_or_none(row[5]),
                jedinicna_cijena=_num_or_none(row[6]),
                tip=str(row[7] or "stavka"),
                kljucne_rijeci=str(row[8] or ""),
                izvedeno=_num(row[9]),
                razlika=str(row[10] or ""),
            ))
        return len(rows)


# --- dnevnik -----------------------------------------------------------------
def _dnevnik_obj(
    projekt_key: str,
    *,
    radnik: str,
    telegram_id: int,
    opis: str,
    lokacija: str,
    sirova: str,
    msg_id: int,
    datum_rada: str = "",
    vrijeme_rada: str = "",
    sati: float | None = None,
    radnici_spomenuti: list[str] | None = None,
    problemi: list[str] | None = None,
    confidence: str = "",
    strujni_krug: str = "",
    dt: datetime | None = None,
) -> DnevnikUnos:
    dt = dt or datetime.now()
    datum = _to_date(datum_rada) if datum_rada else dt.date()
    return DnevnikUnos(
        projekt_key=projekt_key,
        datum=datum,
        upisano_at=dt,
        radnik=radnik,
        telegram_id=int(telegram_id),
        opis=opis,
        lokacija=lokacija,
        vrijeme_rada=vrijeme_rada,
        sati=sati,
        radnici_spomenuti=", ".join(radnici_spomenuti or []),
        problemi=" | ".join(problemi or []),
        sirova=sirova,
        confidence=confidence,
        strujni_krug=_clip(strujni_krug, 120),
        telegram_msg_id=int(msg_id) if msg_id else 0,
    )


def append_dnevnik(projekt_key: str, **kwargs: Any) -> None:
    with db.session() as s:
        s.add(_dnevnik_obj(projekt_key, **kwargs))


def _dnevnik_to_dict(r: DnevnikUnos) -> dict[str, Any]:
    return {
        "Datum": r.datum.strftime("%Y-%m-%d"),
        "Upisano_at": r.upisano_at.isoformat(timespec="seconds") if r.upisano_at else "",
        "Radnik": r.radnik,
        "Telegram_ID": r.telegram_id,
        "Opis rada": r.opis,
        "Lokacija": r.lokacija,
        "Vrijeme_rada": r.vrijeme_rada,
        "Sati": "" if r.sati is None else r.sati,
        "Radnici_spomenuti": r.radnici_spomenuti,
        "Problemi": r.problemi,
        "Sirova_poruka": r.sirova,
        "Confidence": r.confidence,
        "Strujni_krug": r.strujni_krug,
        "Telegram_msg_id": r.telegram_msg_id,
    }


def get_dnevnik_za_datum(projekt_key: str, datum: str) -> list[dict[str, Any]]:
    d = _to_date(datum)
    with db.session() as s:
        rows = s.scalars(
            select(DnevnikUnos)
            .where(DnevnikUnos.projekt_key == projekt_key, DnevnikUnos.datum == d)
            .order_by(DnevnikUnos.upisano_at)
        ).all()
        return [_dnevnik_to_dict(r) for r in rows]


def get_dnevnik_period(
    projekt_key: str,
    od: str | None = None,
    do: str | None = None,
) -> list[dict[str, Any]]:
    """Dnevnik unosi u rasponu [od, do] (uključivo), sortirani po datumu."""
    with db.session() as s:
        stmt = select(DnevnikUnos).where(DnevnikUnos.projekt_key == projekt_key)
        if od:
            stmt = stmt.where(DnevnikUnos.datum >= _to_date(od))
        if do:
            stmt = stmt.where(DnevnikUnos.datum <= _to_date(do))
        rows = s.scalars(
            stmt.order_by(DnevnikUnos.datum, DnevnikUnos.upisano_at, DnevnikUnos.id)
        ).all()
        return [_dnevnik_to_dict(r) for r in rows]


# --- materijali --------------------------------------------------------------
def _materijal_obj(
    projekt_key: str,
    *,
    radnik: str,
    telegram_id: int,
    sifra: str,
    opis: str,
    kolicina: float,
    jm: str,
    lokacija: str = "",
    napomena: str = "",
    strujni_krug: str = "",
    dt: datetime | None = None,
) -> Materijal:
    dt = dt or datetime.now()
    return Materijal(
        projekt_key=projekt_key,
        datum=dt.date(),
        vrijeme=dt.strftime("%H:%M"),
        radnik=radnik,
        telegram_id=int(telegram_id),
        sifra_stavke=sifra,
        opis=opis,
        kolicina=_num(kolicina),
        jm=jm,
        lokacija=lokacija,
        napomena=napomena,
        strujni_krug=_clip(strujni_krug, 60),
    )


def append_materijal(projekt_key: str, **kwargs: Any) -> None:
    with db.session() as s:
        s.add(_materijal_obj(projekt_key, **kwargs))


def append_izvjestaj(
    projekt_key: str,
    *,
    dnevnik: dict[str, Any],
    materijali: list[dict[str, Any]],
) -> None:
    """Upiši dnevnik + sve materijale jedne potvrde u JEDNOJ transakciji —
    ili sve prođe ili ništa (nema polovičnih izvještaja)."""
    with db.session() as s:
        s.add(_dnevnik_obj(projekt_key, **dnevnik))
        for m in materijali:
            s.add(_materijal_obj(projekt_key, **m))


def _materijal_to_dict(r: Materijal) -> dict[str, Any]:
    return {
        "Datum": r.datum.strftime("%Y-%m-%d"),
        "Vrijeme": r.vrijeme,
        "Radnik": r.radnik,
        "Telegram_ID": r.telegram_id,
        "Šifra_stavke": r.sifra_stavke,
        "Opis": r.opis,
        "Količina": r.kolicina,
        "JM": r.jm,
        "Lokacija": r.lokacija,
        "Napomena": r.napomena,
        "Strujni_krug": r.strujni_krug,
    }


def get_materijali_za_datum(projekt_key: str, datum: str) -> list[dict[str, Any]]:
    d = _to_date(datum)
    with db.session() as s:
        rows = s.scalars(
            select(Materijal)
            .where(Materijal.projekt_key == projekt_key, Materijal.datum == d)
            .order_by(Materijal.id)
        ).all()
        return [_materijal_to_dict(r) for r in rows]


def get_materijali_period(
    projekt_key: str,
    od: str | None = None,
    do: str | None = None,
) -> list[dict[str, Any]]:
    with db.session() as s:
        stmt = select(Materijal).where(Materijal.projekt_key == projekt_key)
        if od:
            stmt = stmt.where(Materijal.datum >= _to_date(od))
        if do:
            stmt = stmt.where(Materijal.datum <= _to_date(do))
        rows = s.scalars(stmt.order_by(Materijal.datum, Materijal.id)).all()
        return [_materijal_to_dict(r) for r in rows]


# --- vrijeme -----------------------------------------------------------------
def append_weather(
    projekt_key: str,
    *,
    datum: str,
    min_temp: float,
    max_temp: float,
    oborine: float,
    opis: str,
) -> None:
    d = _to_date(datum)
    with db.session() as s:
        exists = s.scalar(
            select(Vrijeme.id).where(
                Vrijeme.projekt_key == projekt_key, Vrijeme.datum == d
            )
        )
        if exists:
            return
        s.add(Vrijeme(
            projekt_key=projekt_key,
            datum=d,
            min_temp=_num_or_none(min_temp),
            max_temp=_num_or_none(max_temp),
            oborine=_num_or_none(oborine),
            opis=opis,
        ))


def get_weather_za_datum(projekt_key: str, datum: str) -> dict[str, Any] | None:
    d = _to_date(datum)
    with db.session() as s:
        r = s.scalar(
            select(Vrijeme).where(
                Vrijeme.projekt_key == projekt_key, Vrijeme.datum == d
            )
        )
        if not r:
            return None
        return {
            "Datum": r.datum.strftime("%Y-%m-%d"),
            "Min_temp": "" if r.min_temp is None else r.min_temp,
            "Max_temp": "" if r.max_temp is None else r.max_temp,
            "Oborine_mm": 0 if r.oborine is None else r.oborine,
            "Vrijeme_opis": r.opis,
        }


# --- radnici -----------------------------------------------------------------
def _radnik_to_dict(r: Radnik) -> dict[str, Any]:
    return {
        "Telegram_ID": r.telegram_id,
        "Ime": r.ime,
        "Kvalifikacija": r.kvalifikacija,
        "Aktivan": "Da" if r.aktivan else "Ne",
    }


def list_radnici(projekt_key: str) -> list[dict[str, Any]]:
    with db.session() as s:
        rows = s.scalars(
            select(Radnik)
            .join(ProjektRadnik, ProjektRadnik.telegram_id == Radnik.telegram_id)
            .where(
                ProjektRadnik.projekt_key == projekt_key,
                Radnik.aktivan.is_(True),
            )
            .order_by(Radnik.ime)
        ).all()
        return [_radnik_to_dict(r) for r in rows]


def is_known_worker(telegram_id: int) -> bool:
    with db.session() as s:
        r = s.get(Radnik, int(telegram_id))
        return bool(r and r.aktivan)


def upsert_radnik(
    projekt_key: str,
    telegram_id: int,
    ime: str,
    kvalifikacija: str = "",
) -> None:
    tid = int(telegram_id)
    with db.session() as s:
        r = s.get(Radnik, tid)
        if r:
            r.ime = ime
            r.kvalifikacija = kvalifikacija
            r.aktivan = True
        else:
            s.add(Radnik(telegram_id=tid, ime=ime, kvalifikacija=kvalifikacija, aktivan=True))
        link = s.get(ProjektRadnik, {"projekt_key": projekt_key, "telegram_id": tid})
        if not link:
            s.add(ProjektRadnik(projekt_key=projekt_key, telegram_id=tid))


def get_radnik(projekt_key: str, telegram_id: int) -> dict[str, Any] | None:
    tid = int(telegram_id)
    with db.session() as s:
        link = s.get(ProjektRadnik, {"projekt_key": projekt_key, "telegram_id": tid})
        if not link:
            return None
        r = s.get(Radnik, tid)
        if not r or not r.aktivan:
            return None
        return _radnik_to_dict(r)


# =============================================================================
# KATALOG / CJENICI
# =============================================================================
def import_cjenik(
    *,
    dobavljac: str,
    naziv_cjenika: str,
    tip: str = "nabavni",
    datum: date | None = None,
    valuta: str = "EUR",
    stavke: list[dict[str, Any]],
) -> dict[str, Any]:
    """Upiši cjenik + njegove stavke u jednoj transakciji.

    stavke: lista dictova {sifra_dobavljaca, naziv, jm, cijena, rabat, kategorija,
    proizvodjac, zargon}. Svaka stavka se spaja na postojeći artikl (po nazivu)
    ili kreira novi. Idempotentno: postojeći cjenik istog naziva za tog dobavljača
    se prvo obriše.
    """
    with db.session() as s:
        partner = s.scalar(
            select(Partner).where(func.lower(Partner.naziv) == dobavljac.strip().lower())
        )
        if not partner:
            partner = Partner(naziv=dobavljac.strip(), tip="dobavljac")
            s.add(partner)
            s.flush()

        # idempotentnost: makni stari cjenik istog naziva
        for c in s.scalars(
            select(Cjenik).where(Cjenik.partner_id == partner.id, Cjenik.naziv == naziv_cjenika)
        ).all():
            s.execute(delete(CjenikStavka).where(CjenikStavka.cjenik_id == c.id))
            s.delete(c)
        s.flush()

        cjenik = Cjenik(
            partner_id=partner.id, naziv=naziv_cjenika, tip=tip, datum=datum, valuta=valuta
        )
        s.add(cjenik)
        s.flush()

        novi_artikli = 0
        povezani = 0
        for i, st in enumerate(stavke):
            naziv = _clip(st.get("naziv", ""), 512)
            if not naziv:
                continue
            sifra = _clip(st.get("sifra_dobavljaca", ""), 120)
            jm = _clip(st.get("jm", ""), 40)
            kategorija = _clip(st.get("kategorija", ""), 120)
            proizvodjac = _clip(st.get("proizvodjac", ""), 255)
            zargon = str(st.get("zargon", "") or "")

            a = s.scalar(select(Artikl).where(func.lower(Artikl.naziv) == naziv.lower()))
            if a:
                povezani += 1
                # nadopuni prazna polja iz cjenika (kategorija/žargon)
                if not a.kategorija and kategorija:
                    a.kategorija = kategorija
                if not a.zargon_aliasi and zargon:
                    a.zargon_aliasi = zargon
            else:
                a = Artikl(
                    naziv=naziv,
                    jm=jm,
                    kategorija=kategorija,
                    proizvodjac=proizvodjac,
                    sifra=sifra,
                    zargon_aliasi=zargon,
                    treba_pregled=False,
                )
                s.add(a)
                s.flush()
                novi_artikli += 1

            s.add(CjenikStavka(
                cjenik_id=cjenik.id,
                artikl_id=a.id,
                sifra_dobavljaca=sifra,
                naziv=naziv,
                jm=jm,
                cijena=_num_or_none(st.get("cijena")),
                rabat=_num_or_none(st.get("rabat")),
                redoslijed=i,
            ))

        return {
            "cjenik_id": cjenik.id,
            "partner": partner.naziv,
            "stavki": len(stavke),
            "novi_artikli": novi_artikli,
            "povezani_postojeci": povezani,
        }


def search_artikli(pojam: str, limit: int = 10) -> list[dict[str, Any]]:
    """Pretraži katalog po nazivu/žargonu (za prepoznavanje materijala s terena)."""
    pojam = pojam.strip()
    if not pojam:
        return []
    like = f"%{pojam.lower()}%"
    with db.session() as s:
        rows = s.scalars(
            select(Artikl)
            .where(
                Artikl.aktivan.is_(True),
                func.lower(Artikl.naziv).like(like) | func.lower(Artikl.zargon_aliasi).like(like),
            )
            .limit(limit)
        ).all()
        return [
            {
                "id": a.id,
                "sifra": a.sifra,
                "naziv": a.naziv,
                "jm": a.jm,
                "kategorija": a.kategorija,
                "zargon": a.zargon_aliasi,
            }
            for a in rows
        ]


_STOP_TOKENI = {
    "sam", "smo", "su", "je", "za", "sa", "na", "po", "od", "do", "kom",
    "stavio", "postavio", "ugradio", "spojio", "metar", "metara", "komada",
}


def find_artikl_candidates(opis: str, limit: int = 15) -> list[dict[str, Any]]:
    """Jeftina pretraga: iz opisa materijala izvuci tokene i nađi artikle čiji
    naziv/žargon sadrži najviše tokena. Vraća kandidate za AI odabir."""
    tokens = [
        t for t in re.split(r"[^0-9a-zžšđčćA-ZĐŠŽČĆ]+", opis.lower())
        if len(t) >= 2 and t not in _STOP_TOKENI
    ]
    if not tokens:
        return []
    uniq = list(dict.fromkeys(tokens))
    with db.session() as s:
        conds = []
        for t in uniq:
            like = f"%{t}%"
            conds.append(func.lower(Artikl.naziv).like(like))
            conds.append(func.lower(Artikl.zargon_aliasi).like(like))
        rows = s.scalars(
            select(Artikl).where(Artikl.aktivan.is_(True), or_(*conds)).limit(200)
        ).all()
        scored = []
        for a in rows:
            hay = f"{a.naziv} {a.zargon_aliasi}".lower()
            score = sum(1 for t in uniq if t in hay)
            if score > 0:
                scored.append((score, a))
        scored.sort(key=lambda x: (-x[0], len(x[1].naziv)))
        return [
            {"id": a.id, "sifra": a.sifra, "naziv": a.naziv, "jm": a.jm}
            for _, a in scored[:limit]
        ]


# =============================================================================
# VEZA MATERIJAL ↔ TROŠKOVNIČKA STAVKA (izvedeno za knjigu/obračun)
# =============================================================================
def izvedeno_po_stavci_id(projekt_key: str) -> dict[int, float]:
    """{troskovnik_stavka_id: zbroj AUTOMATSKI povezanih materijala} — za web obračun."""
    with db.session() as s:
        rows = s.execute(
            select(Materijal.troskovnik_stavka_id, func.sum(Materijal.kolicina))
            .where(
                Materijal.projekt_key == projekt_key,
                Materijal.troskovnik_stavka_id.is_not(None),
            )
            .group_by(Materijal.troskovnik_stavka_id)
        ).all()
        return {sid: float(kol or 0) for sid, kol in rows}


def izvedeno_krug_po_sifri(projekt_key: str) -> dict[str, dict[str, float]]:
    """{troškovnik_šifra: {strujni_krug: zbroj izvedenih količina}} iz povezanih
    materijala — za 'razradu po strujnim krugovima' u građevinskoj knjizi.
    Prazan krug → '(bez kruga)'. Stavke bez šifre se preskaču."""
    with db.session() as s:
        rows = s.execute(
            select(
                TroskovnikStavka.sifra,
                Materijal.strujni_krug,
                func.sum(Materijal.kolicina),
            )
            .join(Materijal, Materijal.troskovnik_stavka_id == TroskovnikStavka.id)
            .where(TroskovnikStavka.projekt_key == projekt_key)
            .group_by(TroskovnikStavka.sifra, Materijal.strujni_krug)
        ).all()
    out: dict[str, dict[str, float]] = {}
    for sifra, krug, kol in rows:
        sifra = (sifra or "").strip()
        if not sifra:
            continue
        k = (krug or "").strip() or "(bez kruga)"
        out.setdefault(sifra, {})[k] = out.setdefault(sifra, {}).get(k, 0.0) + float(kol or 0)
    return out


def izvedeno_efektivno(projekt_key: str) -> dict[int, float]:
    """{troskovnik_stavka_id: EFEKTIVNO izvedeno}. Prioritet: ručni unos
    (izvedeno_rucno) > automatski zbroj povezanih materijala > legacy kolona."""
    linked = izvedeno_po_stavci_id(projekt_key)
    with db.session() as s:
        stavke = s.scalars(
            select(TroskovnikStavka).where(TroskovnikStavka.projekt_key == projekt_key)
        ).all()
        out: dict[int, float] = {}
        for st in stavke:
            if st.izvedeno_rucno is not None:
                out[st.id] = float(st.izvedeno_rucno)
            elif st.id in linked:
                out[st.id] = linked[st.id]
            else:
                out[st.id] = float(st.izvedeno or 0.0)
        return out


def izvedeno_po_sifri(
    projekt_key: str,
    period_od: str | None = None,
    period_do: str | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """Izvedene količine po ŠIFRI troškovničke stavke, iz EKSPLICITNIH izvora
    (ručni unos ili povezani materijali). Ručni unos ima prednost i tretira se
    kao kumulativ (bez datuma). Vrati (kumulativ {sifra: kol} do period_do,
    mjesecno {sifra: kol} u [period_od, period_do]). Prazni dictovi ako nema
    nijednog izvora — tada docgen pada na staru logiku po šifri artikla."""
    do_d = _to_date(period_do) if period_do else None
    od_d = _to_date(period_od) if period_od else None
    with db.session() as s:
        stavke = s.scalars(
            select(TroskovnikStavka).where(TroskovnikStavka.projekt_key == projekt_key)
        ).all()
        mats = s.execute(
            select(Materijal.troskovnik_stavka_id, Materijal.kolicina, Materijal.datum)
            .where(Materijal.projekt_key == projekt_key,
                   Materijal.troskovnik_stavka_id.is_not(None))
        ).all()

    lk_kum: dict[int, float] = {}
    lk_mj: dict[int, float] = {}
    for sid, kol, datum in mats:
        k = float(kol or 0)
        if do_d is None or datum <= do_d:
            lk_kum[sid] = lk_kum.get(sid, 0.0) + k
        if od_d and do_d and od_d <= datum <= do_d:
            lk_mj[sid] = lk_mj.get(sid, 0.0) + k

    kumulativ: dict[str, float] = {}
    mjesecno: dict[str, float] = {}
    for st in stavke:
        sifra = (st.sifra or "").strip()
        if not sifra or st.tip == "sekcija":
            continue
        if st.izvedeno_rucno is not None:
            r = float(st.izvedeno_rucno)
            kumulativ[sifra] = kumulativ.get(sifra, 0.0) + r
            if not od_d:  # bez perioda: mjesečno = kumulativ
                mjesecno[sifra] = mjesecno.get(sifra, 0.0) + r
        else:
            kv = lk_kum.get(st.id, 0.0)
            if kv:
                kumulativ[sifra] = kumulativ.get(sifra, 0.0) + kv
            mv = lk_mj.get(st.id, 0.0) if od_d else lk_kum.get(st.id, 0.0)
            if mv:
                mjesecno[sifra] = mjesecno.get(sifra, 0.0) + mv

    if not kumulativ:
        return {}, {}
    return kumulativ, mjesecno


def find_troskovnik_candidates(
    projekt_key: str, opis: str, limit: int = 8,
) -> list[dict[str, Any]]:
    """Token-pretragom nađi troškovničke stavke projekta koje najbolje odgovaraju
    opisu materijala (po opisu + ključnim riječima + sekciji). Heuristički
    prijedlog za povezivanje (jeftin, bez AI-ja)."""
    tokens = [
        t for t in re.split(r"[^0-9a-zžšđčćA-ZĐŠŽČĆ]+", (opis or "").lower())
        if len(t) >= 2 and t not in _STOP_TOKENI
    ]
    if not tokens:
        return []
    uniq = list(dict.fromkeys(tokens))
    with db.session() as s:
        stavke = s.scalars(
            select(TroskovnikStavka)
            .where(TroskovnikStavka.projekt_key == projekt_key,
                   TroskovnikStavka.tip != "sekcija")
        ).all()
        scored = []
        for st in stavke:
            hay = f"{st.opis} {st.kljucne_rijeci} {st.sekcija}".lower()
            score = sum(1 for t in uniq if t[:5] in hay)
            if score > 0:
                scored.append((score, st))
        scored.sort(key=lambda x: (-x[0], len(x[1].opis or "")))
        return [
            {"id": st.id, "sifra": st.sifra, "opis": st.opis, "score": score}
            for score, st in scored[:limit]
        ]


def count_katalog() -> dict[str, int]:
    with db.session() as s:
        return {
            "artikli": s.scalar(select(func.count()).select_from(Artikl)) or 0,
            "cjenici": s.scalar(select(func.count()).select_from(Cjenik)) or 0,
            "cjenik_stavke": s.scalar(select(func.count()).select_from(CjenikStavka)) or 0,
            "partneri": s.scalar(select(func.count()).select_from(Partner)) or 0,
        }
