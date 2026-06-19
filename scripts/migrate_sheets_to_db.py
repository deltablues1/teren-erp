"""CLI: jednokratni prijenos podataka iz Google Sheets u PostgreSQL.

Čita SVE projekte iz data/projekti.json i njihove Sheets tabove
(Troskovnik, Radnici, Dnevnik, Materijali, Vrijeme), te ih upisuje u Postgres
preko services/db_backend.py. Idempotentno: ponovno pokretanje briše postojeće
unose projekta i upisuje ih iznova (ne duplicira).

Preduvjet:
  - DATABASE_URL u .env (Postgres baza kreirana)
  - Google Sheets pristup i dalje radi (secrets/google-sa.json)

Korištenje:
    py scripts/init_db.py            # prvo kreiraj tablice
    py scripts/migrate_sheets_to_db.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# Windows konzola je cp1250 — prebaci na UTF-8 da ✅/→/č/š ne pucaju u ispisu.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete  # noqa: E402

from services import db, db_backend, sheets  # noqa: E402
from services.models import (  # noqa: E402
    DnevnikUnos,
    Materijal,
    Projekt,
    Vrijeme,
)

TROSK_KEYS = [
    "Šifra", "Sekcija", "Pozicija", "Opis stavke", "JM",
    "Ugovorena količina", "Jedinična cijena", "Tip",
    "Ključne riječi", "Izvedeno", "Razlika",
]


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _upsert_projekt(key: str, meta: dict) -> None:
    """Upiši/ažuriraj projekt red s punim metapodacima iz projekti.json."""
    kreiran = datetime.now()
    raw = meta.get("kreiran")
    if raw:
        try:
            kreiran = datetime.fromisoformat(raw)
        except ValueError:
            pass
    with db.session() as s:
        p = s.get(Projekt, key)
        if not p:
            p = Projekt(key=key)
            s.add(p)
        p.naziv = meta.get("naziv", "")
        p.adresa = meta.get("adresa", "")
        p.investitor = meta.get("investitor", "")
        p.izvodac = meta.get("izvodac", "")
        p.nadzorni = meta.get("nadzorni", "")
        p.broj_dozvole = meta.get("broj_dozvole", "")
        p.spreadsheet_id = meta.get("spreadsheet_id", "")
        p.spreadsheet_url = meta.get("spreadsheet_url", "")
        p.kreiran = kreiran
        p.aktivan = bool(meta.get("aktivan", True))


def _clear_projekt_data(key: str) -> None:
    """Obriši dnevnik/materijale/vrijeme projekta (za idempotentnu re-migraciju).
    Troškovnik briše replace_troskovnik, radnici se upsertaju."""
    with db.session() as s:
        s.execute(delete(DnevnikUnos).where(DnevnikUnos.projekt_key == key))
        s.execute(delete(Materijal).where(Materijal.projekt_key == key))
        s.execute(delete(Vrijeme).where(Vrijeme.projekt_key == key))


def _migrate_troskovnik(key: str) -> int:
    records = sheets.get_troskovnik(key)
    rows = [[rec.get(k, "") for k in TROSK_KEYS] for rec in records]
    return db_backend.replace_troskovnik(key, rows)


def _migrate_radnici(key: str) -> int:
    radnici = sheets.list_radnici(key)
    n = 0
    for r in radnici:
        tid = r.get("Telegram_ID")
        if tid in (None, ""):
            continue
        db_backend.upsert_radnik(
            key, int(tid), r.get("Ime", ""), r.get("Kvalifikacija", "")
        )
        n += 1
    return n


def _migrate_dnevnik(key: str) -> int:
    ss = sheets.get_spreadsheet(key)
    records = ss.worksheet(sheets.SHEET_DNEVNIK).get_all_records()
    n = 0
    for r in records:
        if not str(r.get("Datum", "")).strip():
            continue
        dt = None
        raw = str(r.get("Upisano_at", "")).strip()
        if raw:
            try:
                dt = datetime.fromisoformat(raw)
            except ValueError:
                dt = None
        sati_raw = r.get("Sati")
        sati = None if sati_raw in (None, "") else _f(sati_raw)
        radnici_sp = [x.strip() for x in str(r.get("Radnici_spomenuti", "")).split(",") if x.strip()]
        problemi = [x.strip() for x in str(r.get("Problemi", "")).split("|") if x.strip()]
        msg_raw = r.get("Telegram_msg_id")
        tid_raw = r.get("Telegram_ID")
        db_backend.append_dnevnik(
            key,
            radnik=str(r.get("Radnik", "")),
            telegram_id=int(tid_raw) if str(tid_raw).strip().lstrip("-").isdigit() else 0,
            opis=str(r.get("Opis rada", "")),
            lokacija=str(r.get("Lokacija", "")),
            sirova=str(r.get("Sirova_poruka", "")),
            msg_id=int(msg_raw) if str(msg_raw).strip().lstrip("-").isdigit() else 0,
            datum_rada=str(r.get("Datum", "")),
            vrijeme_rada=str(r.get("Vrijeme_rada", "")),
            sati=sati,
            radnici_spomenuti=radnici_sp,
            problemi=problemi,
            confidence=str(r.get("Confidence", "")),
            dt=dt,
        )
        n += 1
    return n


def _migrate_materijali(key: str) -> int:
    ss = sheets.get_spreadsheet(key)
    records = ss.worksheet(sheets.SHEET_MATERIJALI).get_all_records()
    n = 0
    for r in records:
        datum = str(r.get("Datum", "")).strip()
        if not datum:
            continue
        vrijeme = str(r.get("Vrijeme", "")).strip() or "00:00"
        dt = None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(f"{datum} {vrijeme}".strip(), fmt if "%H" in fmt else "%Y-%m-%d")
                break
            except ValueError:
                continue
        if dt is None:
            continue
        tid_raw = r.get("Telegram_ID")
        db_backend.append_materijal(
            key,
            radnik=str(r.get("Radnik", "")),
            telegram_id=int(tid_raw) if str(tid_raw).strip().lstrip("-").isdigit() else 0,
            sifra=str(r.get("Šifra_stavke", "")),
            opis=str(r.get("Opis", "")),
            kolicina=_f(r.get("Količina")),
            jm=str(r.get("JM", "")),
            lokacija=str(r.get("Lokacija", "")),
            napomena=str(r.get("Napomena", "")),
            dt=dt,
        )
        n += 1
    return n


def _migrate_vrijeme(key: str) -> int:
    ss = sheets.get_spreadsheet(key)
    records = ss.worksheet(sheets.SHEET_VRIJEME).get_all_records()
    n = 0
    for r in records:
        datum = str(r.get("Datum", "")).strip()
        if not datum:
            continue
        db_backend.append_weather(
            key,
            datum=datum,
            min_temp=_f(r.get("Min_temp")),
            max_temp=_f(r.get("Max_temp")),
            oborine=_f(r.get("Oborine_mm")),
            opis=str(r.get("Vrijeme_opis", "")),
        )
        n += 1
    return n


def main() -> None:
    db.init_db()
    projekti = sheets._load_projekti()
    if not projekti:
        print("Nema projekata u data/projekti.json — nema što migrirati.")
        return

    print(f"Migriram {len(projekti)} projekt(a) iz Sheets u Postgres...\n")
    for key, meta in projekti.items():
        print(f"→ Projekt: {key} ({meta.get('naziv', '')})")
        _upsert_projekt(key, meta)
        _clear_projekt_data(key)
        try:
            t = _migrate_troskovnik(key)
            print(f"   troškovnik: {t} stavki")
        except Exception as e:
            print(f"   troškovnik: GREŠKA {e}")
        try:
            rad = _migrate_radnici(key)
            print(f"   radnici: {rad}")
        except Exception as e:
            print(f"   radnici: GREŠKA {e}")
        try:
            d = _migrate_dnevnik(key)
            print(f"   dnevnik: {d} unosa")
        except Exception as e:
            print(f"   dnevnik: GREŠKA {e}")
        try:
            m = _migrate_materijali(key)
            print(f"   materijali: {m} unosa")
        except Exception as e:
            print(f"   materijali: GREŠKA {e}")
        try:
            v = _migrate_vrijeme(key)
            print(f"   vrijeme: {v} dana")
        except Exception as e:
            print(f"   vrijeme: GREŠKA {e}")
        print()

    print("✅ Migracija gotova. Postavi DATA_BACKEND=postgres u .env i pokreni bota.")


if __name__ == "__main__":
    main()
