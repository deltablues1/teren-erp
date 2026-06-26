"""Upiti za web panel — čitaju iz Postgresa (modeli iz services/models.py).

Vraćaju obične dictove (odvojene od sesije) da ih Jinja lako prikaže.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

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
    PutniNalog,
    Radnik,
    Situacija,
    SituacijaStavka,
    TroskovnikStavka,
    Vozilo,
)


def projekt_naziv(key: str) -> str:
    with db.session() as s:
        p = s.get(Projekt, key)
        return p.naziv if p else key


def _slugify(text: str) -> str:
    """Naziv → ključ projekta. Ista logika kao handlers/admin._slugify."""
    text = (text or "").lower().strip()
    for a, b in (("č", "c"), ("ć", "c"), ("š", "s"), ("ž", "z"), ("đ", "d")):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def create_projekt(
    naziv: str, *, adresa: str = "", investitor: str = "", izvodac: str = "",
    nadzorni: str = "", broj_dozvole: str = "",
) -> str:
    """Kreiraj projekt iz naziva (slug = key). Vrati key.
    Diže ValueError ako je naziv prazan ili projekt s tim ključem već postoji."""
    naziv = (naziv or "").strip()
    if not naziv:
        raise ValueError("Naziv projekta je obavezan.")
    key = _slugify(naziv)
    if not key:
        raise ValueError("Naziv mora sadržavati slova ili brojke.")
    from services import db_backend
    db_backend.create_projekt(
        key, naziv, adresa=adresa.strip(), investitor=investitor.strip(),
        izvodac=izvodac.strip(), nadzorni=nadzorni.strip(),
        broj_dozvole=broj_dozvole.strip(),
    )
    return key


def dashboard_counts() -> dict[str, int]:
    with db.session() as s:
        return {
            "projekti": s.scalar(select(func.count()).select_from(Projekt).where(Projekt.aktivan.is_(True))) or 0,
            "artikli": s.scalar(select(func.count()).select_from(Artikl)) or 0,
            "cjenici": s.scalar(select(func.count()).select_from(Cjenik)) or 0,
            "za_pregled": s.scalar(select(func.count()).select_from(Artikl).where(Artikl.treba_pregled.is_(True))) or 0,
        }


def _counts_by_projekt(s, model) -> dict[str, int]:
    """{projekt_key: broj redaka} u jednom GROUP BY upitu."""
    rows = s.execute(
        select(model.projekt_key, func.count()).group_by(model.projekt_key)
    ).all()
    return {key: n for key, n in rows}


def list_projekti() -> list[dict[str, Any]]:
    with db.session() as s:
        trosk = _counts_by_projekt(s, TroskovnikStavka)
        dnev = _counts_by_projekt(s, DnevnikUnos)
        mat = _counts_by_projekt(s, Materijal)
        out = []
        for p in s.scalars(select(Projekt).where(Projekt.aktivan.is_(True)).order_by(Projekt.naziv)).all():
            n_trosk = trosk.get(p.key, 0)
            n_dnev = dnev.get(p.key, 0)
            n_mat = mat.get(p.key, 0)
            out.append({
                "key": p.key,
                "naziv": p.naziv,
                "adresa": p.adresa,
                "investitor": p.investitor,
                "tip": "obračunski" if n_trosk else "ključ u ruke",
                "n_troskovnik": n_trosk,
                "n_dnevnik": n_dnev,
                "n_materijali": n_mat,
            })
        return out


def projekt_detail(key: str) -> dict[str, Any] | None:
    with db.session() as s:
        p = s.get(Projekt, key)
        if not p:
            return None
        n_trosk = s.scalar(select(func.count()).select_from(TroskovnikStavka).where(TroskovnikStavka.projekt_key == key)) or 0

        dnevnik = [
            {
                "datum": d.datum.strftime("%d.%m.%Y"),
                "radnik": d.radnik,
                "opis": d.opis,
                "lokacija": d.lokacija,
                "sati": d.sati,
                "vrijeme_rada": d.vrijeme_rada,
            }
            for d in s.scalars(
                select(DnevnikUnos).where(DnevnikUnos.projekt_key == key)
                .order_by(DnevnikUnos.datum.desc(), DnevnikUnos.id.desc())
            ).all()
        ]

        mats = s.scalars(
            select(Materijal).where(Materijal.projekt_key == key)
            .order_by(Materijal.datum.desc(), Materijal.id.desc())
        ).all()

        # predučitaj artikle i min. cijene u 2 upita (umjesto 2 upita PO materijalu)
        sifre = {m.sifra_stavke for m in mats if m.sifra_stavke}
        artikli_po_sifri: dict[str, Artikl] = {}
        cijene_po_artiklu: dict[int, float] = {}
        if sifre:
            for a in s.scalars(select(Artikl).where(Artikl.sifra.in_(sifre))).all():
                artikli_po_sifri.setdefault(a.sifra, a)
            ids = [a.id for a in artikli_po_sifri.values()]
            cijene_po_artiklu = {
                aid: c for aid, c in s.execute(
                    select(CjenikStavka.artikl_id, func.min(CjenikStavka.cijena))
                    .where(CjenikStavka.artikl_id.in_(ids), CjenikStavka.cijena.is_not(None))
                    .group_by(CjenikStavka.artikl_id)
                ).all()
            }

        # povezane troškovničke stavke — primarni izvor cijene materijala (ugovorna
        # cijena pozicije) i prikaz veze (šifra + opis) u stupcu tablice
        trosk_ids = {m.troskovnik_stavka_id for m in mats if m.troskovnik_stavka_id is not None}
        trosk_po_id: dict[int, TroskovnikStavka] = {}
        if trosk_ids:
            trosk_po_id = {
                st.id: st for st in s.scalars(
                    select(TroskovnikStavka).where(TroskovnikStavka.id.in_(trosk_ids))
                ).all()
            }

        n_nepovezani = sum(1 for m in mats if m.troskovnik_stavka_id is None)

        materijali = []
        ukupno_vrijednost = 0.0
        for m in mats:
            a = artikli_po_sifri.get(m.sifra_stavke) if m.sifra_stavke else None
            st = trosk_po_id.get(m.troskovnik_stavka_id) if m.troskovnik_stavka_id is not None else None
            # cijena: prvo iz povezane troškovničke stavke, pa fallback na katalog
            cijena = st.jedinicna_cijena if st is not None else None
            if cijena is None and a:
                cijena = cijene_po_artiklu.get(a.id)
            vrijednost = (cijena * m.kolicina) if (cijena is not None and m.kolicina) else None
            if vrijednost is not None:
                ukupno_vrijednost += vrijednost
            # oznaka pozicije troškovnika na koju je materijal povezan
            trosk_sifra = (st.sifra or "").strip() if st is not None else ""
            trosk_opis = (st.opis or "").strip() if st is not None else ""
            materijali.append({
                "datum": m.datum.strftime("%d.%m.%Y"),
                "radnik": m.radnik,
                "opis": m.opis,
                "kolicina": m.kolicina,
                "jm": m.jm,
                "povezano": st is not None,
                "trosk_sifra": trosk_sifra,
                "trosk_opis": trosk_opis[:50] + ("…" if len(trosk_opis) > 50 else ""),
                "cijena": round(cijena, 2) if cijena is not None else None,
                "vrijednost": round(vrijednost, 2) if vrijednost is not None else None,
            })

        return {
            "ukupno_vrijednost": round(ukupno_vrijednost, 2),
            "key": p.key,
            "naziv": p.naziv,
            "adresa": p.adresa,
            "investitor": p.investitor,
            "izvodac": p.izvodac,
            "nadzorni": p.nadzorni,
            "tip": "obračunski" if n_trosk else "ključ u ruke",
            "n_troskovnik": n_trosk,
            "n_nepovezani": n_nepovezani,
            "dnevnik": dnevnik,
            "materijali": materijali,
        }


def _rabat_faktor(rabat_posto: float | None) -> float:
    return 1.0 - (rabat_posto or 0.0) / 100.0


def situacije(projekt_key: str) -> list[dict[str, Any]]:
    """Situacije projekta s kumulativnom vrijednošću i iznosom svake situacije
    (kumulativ_ove − kumulativ_prethodne). Vrijednost = Σ kumulativ × cijena,
    umanjeno za ugovorni rabat projekta."""
    with db.session() as s:
        p = s.get(Projekt, projekt_key)
        faktor = _rabat_faktor(p.rabat_posto if p else 0.0)
        sits = s.scalars(
            select(Situacija).where(Situacija.projekt_key == projekt_key)
            .order_by(Situacija.broj)
        ).all()
        out = []
        prev_val = 0.0
        for si in sits:
            rows = s.execute(
                select(SituacijaStavka.kolicina_kumulativ, TroskovnikStavka.jedinicna_cijena)
                .outerjoin(TroskovnikStavka, TroskovnikStavka.id == SituacijaStavka.troskovnik_stavka_id)
                .where(SituacijaStavka.situacija_id == si.id)
            ).all()
            kum = sum((k or 0) * (c or 0) for k, c in rows) * faktor
            out.append({
                "broj": si.broj,
                "datum": si.datum.strftime("%d.%m.%Y") if si.datum else "",
                "status": si.status,
                "n_stavki": len(rows),
                "kumulativ_vrijednost": round(kum, 2),
                "iznos_situacije": round(kum - prev_val, 2),
            })
            prev_val = kum
        return out


def obracun_summary(projekt_key: str) -> dict[str, Any] | None:
    """Sažetak obračuna projekta: ugovorena i izvedena vrijednost (neto rabat),
    postotak izvedenosti, broj situacija. None ako projekt nema troškovnik."""
    with db.session() as s:
        p = s.get(Projekt, projekt_key)
        if not p:
            return None
        faktor = _rabat_faktor(p.rabat_posto)
        rows = s.execute(
            select(TroskovnikStavka.id, TroskovnikStavka.ugovorena_kolicina,
                   TroskovnikStavka.izvedeno, TroskovnikStavka.jedinicna_cijena)
            .where(TroskovnikStavka.projekt_key == projekt_key)
        ).all()
        if not rows:
            return None
        # izvedeno: efektivno po stavci (ručni unos > povezani materijali > legacy).
        from services import db_backend
        eff = db_backend.izvedeno_efektivno(projekt_key)
        ugovoreno = sum((u or 0) * (c or 0) for _id, u, _i, c in rows) * faktor
        izvedeno = sum(
            eff.get(_id, _i or 0) * (c or 0) for _id, _u, _i, c in rows
        ) * faktor
        n_sit = s.scalar(
            select(func.count()).select_from(Situacija)
            .where(Situacija.projekt_key == projekt_key)
        ) or 0
        postotak = round(izvedeno / ugovoreno * 100, 1) if ugovoreno else 0.0
        return {
            "ugovoreno": round(ugovoreno, 2),
            "izvedeno": round(izvedeno, 2),
            "preostalo": round(ugovoreno - izvedeno, 2),
            "postotak": postotak,
            "rabat_posto": p.rabat_posto or 0.0,
            "n_situacija": n_sit,
        }


def katalog_search(q: str = "", limit: int = 100, offset: int = 0, pregled: bool = False) -> tuple[list[dict[str, Any]], int]:
    q = (q or "").strip().lower()
    with db.session() as s:
        stmt = select(Artikl)
        cnt_stmt = select(func.count()).select_from(Artikl)
        if pregled:
            stmt = stmt.where(Artikl.treba_pregled.is_(True))
            cnt_stmt = cnt_stmt.where(Artikl.treba_pregled.is_(True))
        if q:
            like = f"%{q}%"
            cond = func.lower(Artikl.naziv).like(like) | func.lower(Artikl.zargon_aliasi).like(like) | func.lower(Artikl.sifra).like(like)
            stmt = stmt.where(cond)
            cnt_stmt = cnt_stmt.where(cond)
        total = s.scalar(cnt_stmt) or 0
        rows = s.scalars(stmt.order_by(Artikl.naziv).limit(limit).offset(offset)).all()
        artikli = [
            {
                "id": a.id, "sifra": a.sifra, "naziv": a.naziv, "jm": a.jm,
                "kategorija": a.kategorija, "zargon": a.zargon_aliasi,
                "treba_pregled": a.treba_pregled,
            }
            for a in rows
        ]
        return artikli, total


def list_cjenici() -> list[dict[str, Any]]:
    with db.session() as s:
        out = []
        for c in s.scalars(select(Cjenik).order_by(Cjenik.id)).all():
            partner = s.get(Partner, c.partner_id) if c.partner_id else None
            n = s.scalar(select(func.count()).select_from(CjenikStavka).where(CjenikStavka.cjenik_id == c.id)) or 0
            out.append({
                "id": c.id,
                "naziv": c.naziv,
                "partner": partner.naziv if partner else "—",
                "tip": c.tip,
                "datum": c.datum.strftime("%d.%m.%Y") if c.datum else "—",
                "valuta": c.valuta,
                "n_stavki": n,
            })
        return out


def artikl_detail(artikl_id: int) -> dict[str, Any] | None:
    with db.session() as s:
        a = s.get(Artikl, artikl_id)
        if not a:
            return None
        cijene = []
        rows = s.execute(
            select(CjenikStavka, Cjenik, Partner)
            .join(Cjenik, Cjenik.id == CjenikStavka.cjenik_id)
            .outerjoin(Partner, Partner.id == Cjenik.partner_id)
            .where(CjenikStavka.artikl_id == artikl_id)
        ).all()
        for cs, c, p in rows:
            cijene.append({
                "dobavljac": p.naziv if p else "—",
                "cjenik": c.naziv,
                "tip": c.tip,
                "sifra_dobavljaca": cs.sifra_dobavljaca,
                "cijena": cs.cijena,
                "valuta": c.valuta,
            })
        nabavne = [c["cijena"] for c in cijene if c["tip"] != "prodajni" and c["cijena"] is not None]
        prodajne = [c["cijena"] for c in cijene if c["tip"] == "prodajni" and c["cijena"] is not None]
        nabavna = min(nabavne) if nabavne else None
        prodajna = prodajne[0] if prodajne else None
        marza = None
        if nabavna and prodajna:
            marza = round((prodajna - nabavna) / nabavna * 100, 1)
        n_materijala = s.scalar(
            select(func.count()).select_from(Materijal)
            .where(Materijal.sifra_stavke == (a.sifra or ""))
        ) or 0 if a.sifra else 0
        return {
            "id": a.id, "sifra": a.sifra, "naziv": a.naziv, "jm": a.jm,
            "kategorija": a.kategorija, "zargon": a.zargon_aliasi,
            "proizvodjac": a.proizvodjac, "treba_pregled": a.treba_pregled,
            "napomena": a.napomena, "cijene": cijene,
            "nabavna": round(nabavna, 2) if nabavna is not None else None,
            "prodajna": round(prodajna, 2) if prodajna is not None else None,
            "marza": marza,
            "n_materijala": n_materijala,
        }


def update_artikl(artikl_id: int, *, naziv: str, jm: str, kategorija: str,
                  zargon: str, proizvodjac: str, treba_pregled: bool) -> bool:
    with db.session() as s:
        a = s.get(Artikl, artikl_id)
        if not a:
            return False
        a.naziv = (naziv or "").strip()[:512]
        a.jm = (jm or "").strip()[:40]
        a.kategorija = (kategorija or "").strip()[:120]
        a.zargon_aliasi = (zargon or "").strip()
        a.proizvodjac = (proizvodjac or "").strip()[:255]
        a.treba_pregled = treba_pregled
        return True


def _num(v):
    try:
        return float(str(v).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def _prodajni_cjenik_id(s) -> int:
    """ID singleton 'Moje cijene' prodajnog cjenika (kreira ga ako ne postoji)."""
    c = s.scalar(select(Cjenik).where(Cjenik.tip == "prodajni", Cjenik.naziv == "Moje cijene"))
    if not c:
        c = Cjenik(partner_id=None, naziv="Moje cijene", tip="prodajni", valuta="EUR")
        s.add(c)
        s.flush()
    return c.id


def set_prodajna_cijena(artikl_id: int, cijena) -> None:
    """Postavi/ažuriraj korisnikovu prodajnu cijenu artikla (u 'Moje cijene' cjeniku).
    Prazno briše prodajnu cijenu."""
    val = _num(cijena) if str(cijena).strip() != "" else None
    with db.session() as s:
        a = s.get(Artikl, artikl_id)
        if not a:
            return
        cid = _prodajni_cjenik_id(s)
        cs = s.scalar(select(CjenikStavka).where(
            CjenikStavka.cjenik_id == cid, CjenikStavka.artikl_id == artikl_id))
        if val is None:
            if cs:
                s.delete(cs)
            return
        if cs:
            cs.cijena = val
        else:
            s.add(CjenikStavka(cjenik_id=cid, artikl_id=artikl_id, naziv=a.naziv, cijena=val))


def unmatched_materijali() -> list[dict[str, Any]]:
    """Nepoznati materijali (bez katalog šifre I bez troškovnik veze), grupirani
    po (projekt_key, opis). Vraća projekt info i flag je li projekt obračunski."""
    with db.session() as s:
        rows = s.execute(
            select(Materijal.opis, Materijal.jm, Materijal.projekt_key, func.count())
            .where(
                (Materijal.sifra_stavke == "") | (Materijal.sifra_stavke.is_(None)),
                Materijal.troskovnik_stavka_id.is_(None),
            )
            .group_by(Materijal.opis, Materijal.jm, Materijal.projekt_key)
            .order_by(Materijal.projekt_key, func.count().desc())
        ).all()
        keys = {r[2] for r in rows if r[2]}
        trosk_po_projektu: dict[str, int] = {}
        nazivi: dict[str, str] = {}
        if keys:
            for p in s.scalars(select(Projekt).where(Projekt.key.in_(keys))).all():
                nazivi[p.key] = p.naziv
            for pk, cnt in s.execute(
                select(TroskovnikStavka.projekt_key, func.count())
                .where(TroskovnikStavka.projekt_key.in_(keys))
                .group_by(TroskovnikStavka.projekt_key)
            ).all():
                trosk_po_projektu[pk] = cnt

        return [
            {
                "opis": o,
                "jm": j or "",
                "broj": n,
                "projekt_key": pk,
                "projekt_naziv": nazivi.get(pk, pk),
                "has_troskovnik": trosk_po_projektu.get(pk, 0) > 0,
            }
            for o, j, pk, n in rows
        ]


def vrati_na_pregled_po_opisu(projekt_key: str, opis: str) -> int:
    """Razvezi materijal (po opisu + projektu) iz kataloga I iz troškovnika —
    vrati ga na /pregled da se može ručno povezati. Vraća broj ažuriranih."""
    with db.session() as s:
        n = 0
        for m in s.scalars(select(Materijal).where(
            Materijal.projekt_key == projekt_key,
            Materijal.opis == opis,
        )).all():
            m.sifra_stavke = ""
            m.troskovnik_stavka_id = None
            n += 1
        return n


def _link_opis(s, opis: str, artikl) -> int:
    """Poveži sve nepoznate materijale s tim opisom na artikl (preko šifre). Vrati broj."""
    if not artikl.sifra:
        artikl.sifra = f"K{artikl.id}"
    n = 0
    for m in s.scalars(select(Materijal).where(
        Materijal.opis == opis,
        (Materijal.sifra_stavke == "") | (Materijal.sifra_stavke.is_(None)),
    )).all():
        m.sifra_stavke = artikl.sifra
        n += 1
    return n


def ukloni_iz_kataloga(artikl_id: int) -> int:
    """Razveže sve materijale koji su vezani na taj artikl (briše sifra_stavke).
    Vraća broj razvezanih materijala. Artikl sam ostaje (možda ima cijene)."""
    with db.session() as s:
        a = s.get(Artikl, artikl_id)
        if not a or not a.sifra:
            return 0
        sifra = a.sifra
        n = 0
        for m in s.scalars(
            select(Materijal).where(Materijal.sifra_stavke == sifra)
        ).all():
            m.sifra_stavke = ""
            n += 1
        return n


def obrisi_artikl(artikl_id: int) -> bool:
    """Obriši artikl iz kataloga (i sve njegove CjenikStavka). Vraća True ako
    je obrisan. Materijali koji su bili vezani na njega dobivaju sifra_stavke=''
    i troskovnik_stavka_id=NULL — pojavljuju se na /pregled za ručno povezivanje."""
    with db.session() as s:
        a = s.get(Artikl, artikl_id)
        if not a:
            return False
        sifra = a.sifra or ""
        # razveži materijale: makni i katalog šifru i troskovnik vezu
        if sifra:
            for m in s.scalars(
                select(Materijal).where(Materijal.sifra_stavke == sifra)
            ).all():
                m.sifra_stavke = ""
                m.troskovnik_stavka_id = None
        # obrisi cjenike stavke tog artikla
        for cs in s.scalars(
            select(CjenikStavka).where(CjenikStavka.artikl_id == artikl_id)
        ).all():
            s.delete(cs)
        s.delete(a)
        return True


def dodaj_u_katalog(opis: str, jm: str = "") -> int:
    """Kreiraj novi artikl iz opisa nepoznatog materijala i poveži te materijale."""
    with db.session() as s:
        a = Artikl(naziv=(opis or "").strip()[:512], jm=(jm or "").strip()[:40],
                   zargon_aliasi=(opis or "").strip(), treba_pregled=False)
        s.add(a)
        s.flush()
        a.sifra = f"K{a.id}"
        _link_opis(s, opis, a)
        return a.id


def spoji_na_artikl(opis: str, artikl_id: int, uci_zargon: bool = True) -> bool:
    """Poveži nepoznati materijal (po opisu) na postojeći artikl; po želji nauči žargon."""
    with db.session() as s:
        a = s.get(Artikl, artikl_id)
        if not a:
            return False
        _link_opis(s, opis, a)
        if uci_zargon and opis:
            toks = [t.strip() for t in (a.zargon_aliasi or "").split(",") if t.strip()]
            if opis.strip().lower() not in [t.lower() for t in toks]:
                toks.append(opis.strip())
                a.zargon_aliasi = ", ".join(toks)
        return True


# =============================================================================
# Povezivanje materijala s troškovničkim stavkama (za knjigu/obračun)
# =============================================================================
def _trosk_label(st: TroskovnikStavka) -> str:
    opis = (st.opis or "").strip()
    skraceno = opis[:60] + ("…" if len(opis) > 60 else "")
    return f"{st.sifra} · {skraceno}" if st.sifra else (skraceno or f"#{st.id}")


def troskovnik_izbor(projekt_key: str) -> list[dict[str, Any]]:
    """Stavke troškovnika projekta za <select> (bez sekcijskih naslova)."""
    with db.session() as s:
        rows = s.scalars(
            select(TroskovnikStavka)
            .where(TroskovnikStavka.projekt_key == projekt_key,
                   TroskovnikStavka.tip != "sekcija")
            .order_by(TroskovnikStavka.redoslijed, TroskovnikStavka.id)
        ).all()
        return [{"id": st.id, "label": _trosk_label(st)} for st in rows]


def materijali_za_povezivanje(projekt_key: str) -> list[dict[str, Any]]:
    """Materijali projekta grupirani po opisu, s trenutnom vezom i heurističkim
    prijedlogom troškovničke stavke. Nepovezani prvi."""
    from services import db_backend
    with db.session() as s:
        mats = s.scalars(
            select(Materijal).where(Materijal.projekt_key == projekt_key)
        ).all()
        labele = {
            st.id: _trosk_label(st)
            for st in s.scalars(select(TroskovnikStavka).where(
                TroskovnikStavka.projekt_key == projekt_key)).all()
        }

    grupe: dict[str, dict[str, Any]] = {}
    for m in mats:
        opis = (m.opis or "").strip()
        g = grupe.setdefault(opis, {
            "opis": opis, "jm": m.jm or "", "broj": 0, "kolicina": 0.0, "ids": set(),
        })
        g["broj"] += 1
        g["kolicina"] += m.kolicina or 0
        g["ids"].add(m.troskovnik_stavka_id)
        if not g["jm"] and m.jm:
            g["jm"] = m.jm

    out = []
    for g in grupe.values():
        if not g["opis"]:
            continue
        non_null = {i for i in g["ids"] if i is not None}
        trenutni_id = next(iter(non_null)) if len(non_null) == 1 and None not in g["ids"] else None
        djelomicno = bool(non_null) and trenutni_id is None
        prijedlog_id = None
        if trenutni_id is None:
            kand = db_backend.find_troskovnik_candidates(projekt_key, g["opis"], limit=1)
            prijedlog_id = kand[0]["id"] if kand else None
        out.append({
            "opis": g["opis"],
            "jm": g["jm"],
            "broj": g["broj"],
            "kolicina": round(g["kolicina"], 2),
            "trenutni_id": trenutni_id,
            "trenutni_label": labele.get(trenutni_id, ""),
            "djelomicno": djelomicno,
            "prijedlog_id": prijedlog_id,
            "povezano": trenutni_id is not None,
        })
    # nepovezani (i djelomični) prvi, pa po broju pojavljivanja
    out.sort(key=lambda r: (r["povezano"], -r["broj"]))
    return out


def troskovnik_pregled(projekt_key: str) -> dict[str, Any]:
    """Sve stavke troškovnika projekta (s id-em, iznosom i izvedenim) + zbrojevi.
    'izvedeno' je efektivno (ručno > auto), uz 'auto_izvedeno' i 'izvedeno_rucno'
    za uredivu ćeliju."""
    from services import db_backend
    auto = db_backend.izvedeno_po_stavci_id(projekt_key)
    with db.session() as s:
        rows = s.scalars(
            select(TroskovnikStavka)
            .where(TroskovnikStavka.projekt_key == projekt_key)
            .order_by(TroskovnikStavka.redoslijed, TroskovnikStavka.id)
        ).all()
        stavke = []
        uk_ugovoreno = uk_izvedeno = 0.0
        for st in rows:
            q = st.ugovorena_kolicina or 0.0
            c = st.jedinicna_cijena or 0.0
            auto_izv = auto.get(st.id, 0.0)
            if st.izvedeno_rucno is not None:
                izvedeno = float(st.izvedeno_rucno)
            elif st.id in auto:
                izvedeno = auto_izv
            else:
                izvedeno = float(st.izvedeno or 0.0)
            iznos = q * c
            iznos_izv = izvedeno * c
            if st.tip != "sekcija":
                uk_ugovoreno += iznos
                uk_izvedeno += iznos_izv
            stavke.append({
                "id": st.id,
                "sifra": st.sifra,
                "sekcija": st.sekcija,
                "pozicija": st.pozicija,
                "opis": st.opis,
                "jm": st.jm,
                "ugovorena": st.ugovorena_kolicina,
                "cijena": st.jedinicna_cijena,
                "tip": st.tip,
                "iznos": round(iznos, 2),
                "izvedeno": round(izvedeno, 2),
                "auto_izvedeno": round(auto_izv, 2),
                "izvedeno_rucno": st.izvedeno_rucno,
                "rucno": st.izvedeno_rucno is not None,
                "iznos_izvedeno": round(iznos_izv, 2),
                "je_sekcija": st.tip == "sekcija",
            })
        return {
            "stavke": stavke,
            "uk_ugovoreno": round(uk_ugovoreno, 2),
            "uk_izvedeno": round(uk_izvedeno, 2),
        }


def update_troskovnik_stavka(
    stavka_id: int, *, sifra: str, opis: str, jm: str,
    ugovorena: str, cijena: str, izvedeno_rucno: str = "",
) -> str | None:
    """Ažuriraj stavku troškovnika. izvedeno_rucno: prazno = NULL (auto izvedeno),
    broj = ručni override. Vrati projekt_key (za redirect) ili None."""
    with db.session() as s:
        st = s.get(TroskovnikStavka, stavka_id)
        if not st:
            return None
        st.sifra = (sifra or "").strip()[:120]
        st.opis = (opis or "").strip()
        st.jm = (jm or "").strip()[:40]
        st.ugovorena_kolicina = _num(ugovorena)
        st.jedinicna_cijena = _num(cijena)
        st.izvedeno_rucno = _num(izvedeno_rucno) if str(izvedeno_rucno).strip() != "" else None
        return st.projekt_key


def dodaj_troskovnik_stavka(
    projekt_key: str, *, sifra: str, opis: str, jm: str,
    ugovorena: str, cijena: str,
) -> bool:
    """Dodaj novu stavku na kraj troškovnika. Prazan opis = ništa."""
    opis = (opis or "").strip()
    if not opis:
        return False
    with db.session() as s:
        maxr = s.scalar(
            select(func.max(TroskovnikStavka.redoslijed))
            .where(TroskovnikStavka.projekt_key == projekt_key)
        ) or 0
        s.add(TroskovnikStavka(
            projekt_key=projekt_key, redoslijed=maxr + 1,
            sifra=(sifra or "").strip()[:120], opis=opis, jm=(jm or "").strip()[:40],
            ugovorena_kolicina=_num(ugovorena), jedinicna_cijena=_num(cijena),
            tip="stavka",
        ))
        return True


def obrisi_troskovnik_stavka(stavka_id: int) -> str | None:
    """Obriši stavku troškovnika. Vrati projekt_key ili None.
    Povezani materijali ostaju (FK je SET NULL)."""
    with db.session() as s:
        st = s.get(TroskovnikStavka, stavka_id)
        if not st:
            return None
        key = st.projekt_key
        s.delete(st)
        return key


def postavi_troskovnik_vezu(
    projekt_key: str, opis: str, troskovnik_stavka_id: int | None,
) -> int:
    """Poveži sve materijale tog opisa na stavku troškovnika (ili None = odveži).
    Vrati broj ažuriranih redaka."""
    opis = (opis or "").strip()
    with db.session() as s:
        if troskovnik_stavka_id is not None:
            st = s.get(TroskovnikStavka, troskovnik_stavka_id)
            if not st or st.projekt_key != projekt_key:
                raise ValueError("Odabrana stavka ne pripada ovom projektu.")
        n = 0
        for m in s.scalars(select(Materijal).where(
            Materijal.projekt_key == projekt_key, Materijal.opis == opis,
        )).all():
            m.troskovnik_stavka_id = troskovnik_stavka_id
            n += 1
        return n


def ai_predlozi_veze(projekt_key: str) -> dict[str, int | None]:
    """LLM batch-matching: za svaki nepovezani materijal predloži troškovničku
    stavku. Vraća {opis: troskovnik_stavka_id | None}."""
    import json
    import anthropic
    from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

    with db.session() as s:
        mats = s.execute(
            select(Materijal.opis, Materijal.jm).distinct()
            .where(
                Materijal.projekt_key == projekt_key,
                Materijal.troskovnik_stavka_id.is_(None),
            )
        ).all()
        if not mats:
            return {}
        stavke = s.scalars(
            select(TroskovnikStavka)
            .where(TroskovnikStavka.projekt_key == projekt_key,
                   TroskovnikStavka.tip != "sekcija")
            .order_by(TroskovnikStavka.redoslijed)
        ).all()
        if not stavke:
            return {}
        trosk_lines = "\n".join(
            f"ID:{st.id} [{st.sifra or '-'}] {st.opis} ({st.jm or '-'})"
            for st in stavke
        )
        mat_lines = "\n".join(
            f"- {m.opis} ({m.jm or 'JM?'})"
            for m in mats
        )
        valid_ids = {st.id for st in stavke}

    prompt = (
        "Ti si stručnjak za građevinski obračun elektroinstalacija.\n\n"
        "MATERIJALI S TERENA (javio radnik):\n"
        f"{mat_lines}\n\n"
        "STAVKE TROŠKOVNIKA:\n"
        f"{trosk_lines}\n\n"
        "Za svaki materijal s terena navedi ID troškovničke stavke koja NAJVJEROJATNIJE "
        "odgovara (isti materijal, ista vrsta). Ako nema jasne veze, vrati null.\n\n"
        "Odgovori ISKLJUČIVO JSON objektom (ključ = točan opis materijala, vrijednost = ID ili null):\n"
        '{"opis materijala": ID_ili_null, ...}'
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    if "```" in text:
        text = text.split("```")[1].lstrip("json\n").rstrip("`").strip()
    mapping = json.loads(text)
    result: dict[str, int | None] = {}
    for opis, val in mapping.items():
        if val is None:
            result[opis] = None
        else:
            try:
                sid = int(val)
                result[opis] = sid if sid in valid_ids else None
            except (TypeError, ValueError):
                result[opis] = None
    return result


def obracun_po_stavkama(projekt_key: str) -> dict[str, Any]:
    """Za /obracun stranicu: troškovničke stavke s izvedenim materijalima.
    Svaka stavka nosi per-day breakdown i agregat. Samo stavke s izvedenim > 0
    ili s barem jednim materijalnim zapisom."""
    from services import db_backend

    auto = db_backend.izvedeno_po_stavci_id(projekt_key)

    with db.session() as s:
        stavke_db = s.scalars(
            select(TroskovnikStavka)
            .where(TroskovnikStavka.projekt_key == projekt_key)
            .order_by(TroskovnikStavka.redoslijed, TroskovnikStavka.id)
        ).all()

        mats_db = s.scalars(
            select(Materijal)
            .where(
                Materijal.projekt_key == projekt_key,
                Materijal.troskovnik_stavka_id.is_not(None),
            )
            .order_by(
                Materijal.troskovnik_stavka_id,
                Materijal.datum.desc(),
                Materijal.id.desc(),
            )
        ).all()

        mats_by: dict[int, list[dict]] = {}
        for m in mats_db:
            mats_by.setdefault(m.troskovnik_stavka_id, []).append({
                "datum": m.datum.strftime("%d.%m.%Y") if m.datum else "—",
                "radnik": m.radnik or "—",
                "opis": m.opis or "",
                "kolicina": float(m.kolicina or 0),
                "jm": m.jm or "",
                "strujni_krug": m.strujni_krug or "",
            })

        out: list[dict] = []
        uk_ugovoreno = uk_izvedeno = 0.0

        for st in stavke_db:
            if st.tip == "sekcija":
                continue
            mats = mats_by.get(st.id, [])
            auto_q = auto.get(st.id, 0.0)

            if st.izvedeno_rucno is not None:
                izvedeno = float(st.izvedeno_rucno)
            elif st.id in auto:
                izvedeno = auto_q
            else:
                izvedeno = float(st.izvedeno or 0.0)

            if izvedeno == 0.0 and not mats:
                continue

            q = float(st.ugovorena_kolicina or 0)
            c = float(st.jedinicna_cijena or 0)
            uk_ugovoreno += q * c
            uk_izvedeno += izvedeno * c

            out.append({
                "id": st.id,
                "sifra": st.sifra or "",
                "opis": st.opis or "",
                "jm": st.jm or "",
                "ugovorena": q,
                "cijena": c,
                "auto_izvedeno": round(auto_q, 3),
                "izvedeno_rucno": st.izvedeno_rucno,
                "rucno": st.izvedeno_rucno is not None,
                "izvedeno": round(izvedeno, 3),
                "iznos_ugovoreno": round(q * c, 2),
                "iznos_izvedeno": round(izvedeno * c, 2),
                "postotak": round(izvedeno / q * 100, 1) if q else None,
                "materijali": mats,
                "n_mat": len(mats),
            })

        p = s.get(Projekt, projekt_key)
        postotak = round(uk_izvedeno / uk_ugovoreno * 100, 1) if uk_ugovoreno else 0.0
        return {
            "stavke": out,
            "uk_ugovoreno": round(uk_ugovoreno, 2),
            "uk_izvedeno": round(uk_izvedeno, 2),
            "postotak": postotak,
            "naziv": p.naziv if p else projekt_key,
            "key": projekt_key,
        }


def set_izvedeno_rucno(stavka_id: int, vrijednost: str) -> str | None:
    """Postavi ručni override izvedene količine (prazno = NULL = auto).
    Vrati projekt_key ili None ako stavka nije pronađena."""
    with db.session() as s:
        st = s.get(TroskovnikStavka, stavka_id)
        if not st:
            return None
        st.izvedeno_rucno = (
            _num(vrijednost) if (vrijednost or "").strip() else None
        )
        return st.projekt_key


# =============================================================================
# Teren web — radnici
# =============================================================================

def get_radnik_by_pin(pin_hash: str) -> dict[str, Any] | None:
    """Pronađi aktivnog radnika po SHA256 hashu PIN-a. None ako ne postoji."""
    with db.session() as s:
        r = s.scalar(
            select(Radnik).where(
                Radnik.pin_hash == pin_hash,
                Radnik.aktivan.is_(True),
            )
        )
        if not r:
            return None
        return {"telegram_id": r.telegram_id, "ime": r.ime, "kvalifikacija": r.kvalifikacija}


def get_projekti_za_radnika(telegram_id: int) -> list[dict[str, Any]]:
    """Projekti na kojima je radnik dodijeljen. Ako nije ni na jednom,
    vraća sve aktivne projekte (fallback za admina/testiranje)."""
    with db.session() as s:
        rows = s.execute(
            select(Projekt)
            .join(ProjektRadnik, ProjektRadnik.projekt_key == Projekt.key)
            .where(
                ProjektRadnik.telegram_id == telegram_id,
                Projekt.aktivan.is_(True),
            )
            .order_by(Projekt.naziv)
        ).scalars().all()
        if rows:
            return [{"key": p.key, "naziv": p.naziv, "adresa": p.adresa} for p in rows]
        # fallback: vrati sve aktivne projekte
        svi = s.scalars(
            select(Projekt).where(Projekt.aktivan.is_(True)).order_by(Projekt.naziv)
        ).all()
        return [{"key": p.key, "naziv": p.naziv, "adresa": p.adresa} for p in svi]


def get_zaliha_radnika(telegram_id: int) -> list[dict[str, Any]]:
    """Trenutno stanje materijala zaduženih na ovog radnika (iz skladišta)."""
    from services import skladiste as skl
    if not skl.ENABLED:
        return []
    return skl.stanje("radnik", str(telegram_id))


def list_radnici_za_pin() -> list[dict[str, Any]]:
    """Svi aktivni radnici za prikaz u admin PIN managementu."""
    with db.session() as s:
        rows = s.scalars(
            select(Radnik).where(Radnik.aktivan.is_(True)).order_by(Radnik.ime)
        ).all()
        return [
            {
                "telegram_id": r.telegram_id,
                "ime": r.ime,
                "kvalifikacija": r.kvalifikacija,
                "ima_pin": bool(r.pin_hash),
            }
            for r in rows
        ]


def postavi_pin(telegram_id: int, pin_hash: str | None) -> bool:
    """Postavi (ili ukloni ako pin_hash=None) PIN za radnika. Vrati True ako OK."""
    with db.session() as s:
        r = s.get(Radnik, telegram_id)
        if not r:
            return False
        r.pin_hash = pin_hash
        return True


def dodaj_radnika(telegram_id: int, ime: str, kvalifikacija: str = "") -> bool:
    """Dodaj novog radnika. Vrati False ako telegram_id već postoji."""
    with db.session() as s:
        if s.get(Radnik, telegram_id):
            return False
        s.add(Radnik(
            telegram_id=telegram_id,
            ime=(ime or "").strip()[:255],
            kvalifikacija=(kvalifikacija or "").strip()[:255],
            aktivan=True,
        ))
        return True


def get_radnik_projekti(telegram_id: int) -> list[str]:
    """Ključevi projekata na kojima je radnik dodijeljen."""
    with db.session() as s:
        rows = s.scalars(
            select(ProjektRadnik.projekt_key)
            .where(ProjektRadnik.telegram_id == telegram_id)
        ).all()
        return list(rows)


def dodaj_projekt_radnika(telegram_id: int, projekt_key: str) -> bool:
    """Dodijeli radnika projektu. Vrati False ako veza već postoji."""
    with db.session() as s:
        existing = s.scalar(
            select(ProjektRadnik).where(
                ProjektRadnik.telegram_id == telegram_id,
                ProjektRadnik.projekt_key == projekt_key,
            )
        )
        if existing:
            return False
        s.add(ProjektRadnik(telegram_id=telegram_id, projekt_key=projekt_key))
        return True


def ukloni_projekt_radnika(telegram_id: int, projekt_key: str) -> bool:
    """Ukloni radnika s projekta."""
    with db.session() as s:
        pr = s.scalar(
            select(ProjektRadnik).where(
                ProjektRadnik.telegram_id == telegram_id,
                ProjektRadnik.projekt_key == projekt_key,
            )
        )
        if not pr:
            return False
        s.delete(pr)
        return True


def set_push_subscription(telegram_id: int, sub_json: str | None) -> None:
    with db.session() as s:
        r = s.get(Radnik, telegram_id)
        if r:
            r.push_subscription = sub_json


# =============================================================================
# Teren web — satnica
# =============================================================================

def get_satnica_radnika(telegram_id: int) -> dict[str, Any]:
    """Sati rada radnika grupirani po datumu, zadnjih 60 dana."""
    today = date.today()
    # Ponedjeljak ovog tjedna
    monday = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    cutoff = today - timedelta(days=60)

    with db.session() as s:
        rows = s.execute(
            select(
                DnevnikUnos.datum,
                func.sum(DnevnikUnos.sati).label("sati"),
                func.count(DnevnikUnos.id).label("n"),
                func.max(DnevnikUnos.projekt_key).label("projekt_key"),
            )
            .where(
                DnevnikUnos.telegram_id == telegram_id,
                DnevnikUnos.datum >= cutoff,
                DnevnikUnos.sati.isnot(None),
            )
            .group_by(DnevnikUnos.datum)
            .order_by(DnevnikUnos.datum.desc())
        ).all()

    dani = [
        {
            "datum": r.datum.strftime("%d.%m.%Y") if hasattr(r.datum, "strftime") else str(r.datum),
            "datum_iso": r.datum.isoformat() if hasattr(r.datum, "isoformat") else str(r.datum),
            "sati": round(float(r.sati or 0), 2),
            "n": r.n,
            "projekt_key": r.projekt_key or "",
        }
        for r in rows
    ]

    def _sum(fn):
        return round(sum(d["sati"] for d in dani if fn(d["datum_iso"])), 2)

    danas = _sum(lambda d: d == today.isoformat())
    tjedan = _sum(lambda d: d >= monday.isoformat())
    mjesec = _sum(lambda d: d >= month_start.isoformat())

    return {
        "danas": danas,
        "tjedan": tjedan,
        "mjesec": mjesec,
        "dani": dani,
    }


# =============================================================================
# Teren web — vozila + putni nalozi
# =============================================================================

def list_vozila() -> list[dict[str, Any]]:
    with db.session() as s:
        rows = s.scalars(
            select(Vozilo).where(Vozilo.aktivno.is_(True)).order_by(Vozilo.naziv)
        ).all()
        return [
            {"id": v.id, "naziv": v.naziv, "registracija": v.registracija, "km_stanje": v.km_stanje}
            for v in rows
        ]


def get_vozilo(vozilo_id: int) -> dict[str, Any] | None:
    with db.session() as s:
        v = s.get(Vozilo, vozilo_id)
        if not v:
            return None
        return {"id": v.id, "naziv": v.naziv, "registracija": v.registracija,
                "km_stanje": v.km_stanje, "aktivno": v.aktivno}


def create_vozilo(naziv: str, registracija: str, km_pocetni: float = 0.0) -> dict[str, Any]:
    with db.session() as s:
        v = Vozilo(naziv=naziv.strip(), registracija=registracija.strip().upper(),
                   km_stanje=km_pocetni)
        s.add(v)
        s.flush()
        return {"id": v.id, "naziv": v.naziv, "registracija": v.registracija}


def update_vozilo(vozilo_id: int, naziv: str | None = None,
                  registracija: str | None = None, aktivno: bool | None = None) -> bool:
    with db.session() as s:
        v = s.get(Vozilo, vozilo_id)
        if not v:
            return False
        if naziv is not None:
            v.naziv = naziv.strip()
        if registracija is not None:
            v.registracija = registracija.strip().upper()
        if aktivno is not None:
            v.aktivno = aktivno
        return True


def save_putni_nalog(
    radnik_id: int,
    vozilo_id: int,
    datum: date,
    polaziste: str,
    odrediste: str,
    km_start: float,
    km_kraj: float,
    projekt_key: str | None = None,
    gorivo_l: float | None = None,
    gorivo_eur: float | None = None,
    napomena: str | None = None,
) -> int:
    with db.session() as s:
        n = PutniNalog(
            radnik_telegram_id=radnik_id,
            vozilo_id=vozilo_id,
            datum=datum,
            projekt_key=projekt_key,
            polaziste=polaziste,
            odrediste=odrediste,
            km_start=km_start,
            km_kraj=km_kraj,
            gorivo_l=gorivo_l,
            gorivo_eur=gorivo_eur,
            napomena=napomena,
        )
        s.add(n)
        # Ažuriraj km_stanje vozila
        v = s.get(Vozilo, vozilo_id)
        if v:
            v.km_stanje = km_kraj
        s.flush()
        return n.id


def list_putni_nalozi(
    radnik_id: int | None = None,
    vozilo_id: int | None = None,
    od: date | None = None,
    do: date | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    with db.session() as s:
        q = (
            select(PutniNalog, Vozilo.naziv, Vozilo.registracija, Radnik.ime)
            .join(Vozilo, Vozilo.id == PutniNalog.vozilo_id)
            .join(Radnik, Radnik.telegram_id == PutniNalog.radnik_telegram_id)
            .order_by(PutniNalog.datum.desc(), PutniNalog.created_at.desc())
        )
        if radnik_id is not None:
            q = q.where(PutniNalog.radnik_telegram_id == radnik_id)
        if vozilo_id is not None:
            q = q.where(PutniNalog.vozilo_id == vozilo_id)
        if od:
            q = q.where(PutniNalog.datum >= od)
        if do:
            q = q.where(PutniNalog.datum <= do)
        q = q.limit(limit)
        rows = s.execute(q).all()
        return [
            {
                "id": n.id,
                "datum": n.datum.strftime("%d.%m.%Y") if n.datum else "",
                "datum_iso": n.datum.isoformat() if n.datum else "",
                "radnik_telegram_id": n.radnik_telegram_id,
                "radnik_ime": ime,
                "vozilo_id": n.vozilo_id,
                "vozilo_naziv": naziv,
                "vozilo_reg": registracija,
                "polaziste": n.polaziste,
                "odrediste": n.odrediste,
                "km_start": n.km_start,
                "km_kraj": n.km_kraj,
                "km_prijedeno": round(n.km_kraj - n.km_start, 1),
                "gorivo_l": n.gorivo_l,
                "gorivo_eur": n.gorivo_eur,
                "projekt_key": n.projekt_key or "",
                "napomena": n.napomena or "",
            }
            for n, naziv, registracija, ime in rows
        ]


def export_putni_nalozi_excel(od: date | None = None, do: date | None = None):
    """Vrati BytesIO s Excel tablicom putnih naloga za računovodstvo."""
    import io
    try:
        import openpyxl
    except ImportError:
        return None

    nalozi = list_putni_nalozi(od=od, do=do, limit=5000)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Putni nalozi"
    zaglavlje = ["Datum", "Radnik", "Vozilo", "Registracija",
                 "Polazište", "Odredište", "Km start", "Km kraj",
                 "Km prijeđeno", "Gorivo (L)", "Gorivo (EUR)", "Projekt", "Napomena"]
    ws.append(zaglavlje)
    for n in nalozi:
        ws.append([
            n["datum"], n["radnik_ime"], n["vozilo_naziv"], n["vozilo_reg"],
            n["polaziste"], n["odrediste"], n["km_start"], n["km_kraj"],
            n["km_prijedeno"], n["gorivo_l"], n["gorivo_eur"],
            n["projekt_key"], n["napomena"],
        ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def list_sva_vozila_admin() -> list[dict[str, Any]]:
    """Sva vozila (uključujući neaktivna) za admin pregled."""
    with db.session() as s:
        rows = s.scalars(select(Vozilo).order_by(Vozilo.naziv)).all()
        return [
            {"id": v.id, "naziv": v.naziv, "registracija": v.registracija,
             "km_stanje": v.km_stanje, "aktivno": v.aktivno}
            for v in rows
        ]


def list_radnici_detalji() -> list[dict[str, Any]]:
    """Svi aktivni radnici s popisom projekata i PIN statusom."""
    with db.session() as s:
        radnici = s.scalars(
            select(Radnik).where(Radnik.aktivan.is_(True)).order_by(Radnik.ime)
        ).all()
        projekti_po_r: dict[int, list[str]] = {}
        pr_rows = s.execute(
            select(ProjektRadnik.telegram_id, ProjektRadnik.projekt_key,
                   Projekt.naziv)
            .join(Projekt, Projekt.key == ProjektRadnik.projekt_key)
            .where(Projekt.aktivan.is_(True))
        ).all()
        for tid, pkey, pnaziv in pr_rows:
            projekti_po_r.setdefault(tid, []).append({"key": pkey, "naziv": pnaziv})
        svi_projekti = s.scalars(
            select(Projekt).where(Projekt.aktivan.is_(True)).order_by(Projekt.naziv)
        ).all()
        return [
            {
                "telegram_id": r.telegram_id,
                "ime": r.ime,
                "kvalifikacija": r.kvalifikacija,
                "ima_pin": bool(r.pin_hash),
                "projekti": projekti_po_r.get(r.telegram_id, []),
            }
            for r in radnici
        ], [{"key": p.key, "naziv": p.naziv} for p in svi_projekti]
