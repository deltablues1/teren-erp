"""Sloj podataka (repozitorij) — apstrakcija nad konkretnim backendom.

Poslovna logika (handleri, docgen) zove OVAJ modul, a NE `sheets` izravno.
Time promjena backenda (Faza 1: PostgreSQL umjesto Google Sheets) ne dira
handlere — mijenja se samo implementacija iza ovog sučelja.

Backend se bira preko `config.DATA_BACKEND`:
  - "sheets"   → Google Sheets (trenutno, `services/sheets.py`)
  - "postgres" → PostgreSQL (Faza 1, još ne postoji)

Funkcije ispod su "port" (ugovor) koji svaki backend mora ispuniti. Potpisi
moraju ostati stabilni — to je cijela svrha ovog sloja.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from config import DATA_BACKEND

# --- odabir backenda ---------------------------------------------------------
if DATA_BACKEND == "sheets":
    from services import sheets as _backend
elif DATA_BACKEND == "postgres":
    from services import db_backend as _backend
else:
    raise RuntimeError(
        f"Nepoznat DATA_BACKEND: {DATA_BACKEND!r}. Dozvoljeno: 'sheets', 'postgres'."
    )


# --- projekti ----------------------------------------------------------------
def list_projekti() -> list[dict[str, Any]]:
    return _backend.list_projekti()


def get_projekt(key: str) -> dict[str, Any] | None:
    return _backend.get_projekt(key)


def create_projekt(
    key: str,
    naziv: str,
    adresa: str = "",
    investitor: str = "",
    izvodac: str = "",
    nadzorni: str = "",
    broj_dozvole: str = "",
) -> dict[str, Any]:
    return _backend.create_projekt(
        key,
        naziv,
        adresa=adresa,
        investitor=investitor,
        izvodac=izvodac,
        nadzorni=nadzorni,
        broj_dozvole=broj_dozvole,
    )


# --- troškovnik --------------------------------------------------------------
def get_troskovnik(projekt_key: str) -> list[dict[str, Any]]:
    return _backend.get_troskovnik(projekt_key)


def replace_troskovnik(projekt_key: str, rows: list[list[Any]]) -> int:
    return _backend.replace_troskovnik(projekt_key, rows)


# --- dnevnik -----------------------------------------------------------------
def append_dnevnik(
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
    dt: datetime | None = None,
) -> None:
    _backend.append_dnevnik(
        projekt_key,
        radnik=radnik,
        telegram_id=telegram_id,
        opis=opis,
        lokacija=lokacija,
        sirova=sirova,
        msg_id=msg_id,
        datum_rada=datum_rada,
        vrijeme_rada=vrijeme_rada,
        sati=sati,
        radnici_spomenuti=radnici_spomenuti,
        problemi=problemi,
        confidence=confidence,
        dt=dt,
    )


def append_izvjestaj(
    projekt_key: str,
    *,
    dnevnik: dict[str, Any],
    materijali: list[dict[str, Any]],
) -> None:
    """Upiši cijeli potvrđeni izvještaj (dnevnik + materijali) odjednom.

    Postgres backend to radi u jednoj transakciji (sve-ili-ništa);
    Sheets backend nema transakcije pa upisuje sekvencijalno.
    """
    if hasattr(_backend, "append_izvjestaj"):
        _backend.append_izvjestaj(projekt_key, dnevnik=dnevnik, materijali=materijali)
        return
    _backend.append_dnevnik(projekt_key, **dnevnik)
    for m in materijali:
        _backend.append_materijal(projekt_key, **m)


def get_dnevnik_za_datum(projekt_key: str, datum: str) -> list[dict[str, Any]]:
    return _backend.get_dnevnik_za_datum(projekt_key, datum)


def get_dnevnik_period(
    projekt_key: str,
    od: str | None = None,
    do: str | None = None,
) -> list[dict[str, Any]]:
    """Dnevnik unosi u rasponu [od, do]. Sheets backend nema range → fallback
    na filtriranje svih (rijetko se koristi, samo za izvoz/generiranje)."""
    if hasattr(_backend, "get_dnevnik_period"):
        return _backend.get_dnevnik_period(projekt_key, od=od, do=do)
    raise NotImplementedError("get_dnevnik_period nije dostupan na ovom backendu.")


# --- materijali --------------------------------------------------------------
def append_materijal(
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
    dt: datetime | None = None,
) -> None:
    _backend.append_materijal(
        projekt_key,
        radnik=radnik,
        telegram_id=telegram_id,
        sifra=sifra,
        opis=opis,
        kolicina=kolicina,
        jm=jm,
        lokacija=lokacija,
        napomena=napomena,
        dt=dt,
    )


def get_materijali_za_datum(projekt_key: str, datum: str) -> list[dict[str, Any]]:
    return _backend.get_materijali_za_datum(projekt_key, datum)


def get_materijali_period(
    projekt_key: str,
    od: str | None = None,
    do: str | None = None,
) -> list[dict[str, Any]]:
    return _backend.get_materijali_period(projekt_key, od=od, do=do)


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
    _backend.append_weather(
        projekt_key,
        datum=datum,
        min_temp=min_temp,
        max_temp=max_temp,
        oborine=oborine,
        opis=opis,
    )


def get_weather_za_datum(projekt_key: str, datum: str) -> dict[str, Any] | None:
    return _backend.get_weather_za_datum(projekt_key, datum)


# --- radnici -----------------------------------------------------------------
def list_radnici(projekt_key: str) -> list[dict[str, Any]]:
    return _backend.list_radnici(projekt_key)


def is_known_worker(telegram_id: int) -> bool:
    return _backend.is_known_worker(telegram_id)


def upsert_radnik(
    projekt_key: str,
    telegram_id: int,
    ime: str,
    kvalifikacija: str = "",
) -> None:
    _backend.upsert_radnik(projekt_key, telegram_id, ime, kvalifikacija)


def get_radnik(projekt_key: str, telegram_id: int) -> dict[str, Any] | None:
    return _backend.get_radnik(projekt_key, telegram_id)
