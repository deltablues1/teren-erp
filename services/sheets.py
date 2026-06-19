"""Google Sheets wrapper - 1 spreadsheet po projektu, 5 listova."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import WorksheetNotFound

from config import (
    GOOGLE_SERVICE_ACCOUNT_JSON,
    GOOGLE_SHEETS_FOLDER_ID,
    PROJEKTI_FILE,
)

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_TROSKOVNIK = "Troskovnik"
SHEET_DNEVNIK = "Dnevnik"
SHEET_MATERIJALI = "Materijali"
SHEET_RADNICI = "Radnici"
SHEET_VRIJEME = "Vrijeme"

HEADERS = {
    SHEET_TROSKOVNIK: [
        "Šifra", "Sekcija", "Pozicija", "Opis stavke", "JM",
        "Ugovorena količina", "Jedinična cijena", "Tip",
        "Ključne riječi", "Izvedeno", "Razlika",
    ],
    SHEET_DNEVNIK: [
        "Datum", "Upisano_at", "Radnik", "Telegram_ID",
        "Opis rada", "Lokacija", "Vrijeme_rada", "Sati",
        "Radnici_spomenuti", "Problemi", "Sirova_poruka",
        "Confidence", "Telegram_msg_id",
    ],
    SHEET_MATERIJALI: [
        "Datum", "Vrijeme", "Radnik", "Telegram_ID",
        "Šifra_stavke", "Opis", "Količina", "JM", "Lokacija", "Napomena",
    ],
    SHEET_RADNICI: [
        "Telegram_ID", "Ime", "Kvalifikacija", "Aktivan",
    ],
    SHEET_VRIJEME: [
        "Datum", "Min_temp", "Max_temp", "Oborine_mm", "Vrijeme_opis",
    ],
}


_client: gspread.Client | None = None


def get_client() -> gspread.Client:
    """Lazy-init gspread klijent s service account autentikacijom."""
    global _client
    if _client is None:
        if not Path(GOOGLE_SERVICE_ACCOUNT_JSON).exists():
            raise RuntimeError(
                f"Google service account JSON nije pronađen na "
                f"{GOOGLE_SERVICE_ACCOUNT_JSON}. Pogledaj README za upute."
            )
        creds = Credentials.from_service_account_file(
            str(GOOGLE_SERVICE_ACCOUNT_JSON), scopes=SCOPES
        )
        _client = gspread.authorize(creds)
    return _client


def _load_projekti() -> dict[str, dict[str, Any]]:
    if not PROJEKTI_FILE.exists():
        return {}
    return json.loads(PROJEKTI_FILE.read_text(encoding="utf-8"))


def _save_projekti(projekti: dict[str, dict[str, Any]]) -> None:
    PROJEKTI_FILE.write_text(
        json.dumps(projekti, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def list_projekti() -> list[dict[str, Any]]:
    """Lista svih projekata (iz lokalnog cachea)."""
    return [
        {"key": k, **v} for k, v in _load_projekti().items()
        if v.get("aktivan", True)
    ]


def get_projekt(key: str) -> dict[str, Any] | None:
    return _load_projekti().get(key)


def get_spreadsheet(projekt_key: str) -> gspread.Spreadsheet:
    """Otvori projektni spreadsheet po ključu."""
    projekt = get_projekt(projekt_key)
    if not projekt:
        raise ValueError(f"Projekt '{projekt_key}' ne postoji.")
    return get_client().open_by_key(projekt["spreadsheet_id"])


def create_projekt(
    key: str, naziv: str, adresa: str = "", investitor: str = "",
    izvodac: str = "", nadzorni: str = "", broj_dozvole: str = "",
) -> dict[str, Any]:
    """Kreira novi projektni spreadsheet u zadanoj Drive folder
    i upisuje ga u lokalni registar."""
    projekti = _load_projekti()
    if key in projekti:
        raise ValueError(f"Projekt '{key}' već postoji.")

    client = get_client()
    spreadsheet = client.create(
        f"Teren - {naziv}", folder_id=GOOGLE_SHEETS_FOLDER_ID
    )

    default_ws = spreadsheet.sheet1
    default_ws.update_title(SHEET_TROSKOVNIK)
    default_ws.append_row(HEADERS[SHEET_TROSKOVNIK])

    for name in (SHEET_DNEVNIK, SHEET_MATERIJALI, SHEET_RADNICI, SHEET_VRIJEME):
        ws = spreadsheet.add_worksheet(title=name, rows=1000, cols=20)
        ws.append_row(HEADERS[name])

    projekti[key] = {
        "naziv": naziv,
        "adresa": adresa,
        "investitor": investitor,
        "izvodac": izvodac,
        "nadzorni": nadzorni,
        "broj_dozvole": broj_dozvole,
        "spreadsheet_id": spreadsheet.id,
        "spreadsheet_url": spreadsheet.url,
        "kreiran": datetime.now().isoformat(),
        "aktivan": True,
    }
    _save_projekti(projekti)
    log.info("Kreiran projekt %s, spreadsheet %s", key, spreadsheet.url)
    return projekti[key]


def get_troskovnik(projekt_key: str) -> list[dict[str, Any]]:
    """Vrati sve stavke troškovnika za projekt."""
    ss = get_spreadsheet(projekt_key)
    ws = ss.worksheet(SHEET_TROSKOVNIK)
    return ws.get_all_records()


def _next_empty_row(ws) -> int:
    """Vrati indeks prvog praznog reda (1-based) gledajući stupac A.
    Robusnije od gspread.append_row koji zbuni s rijetkim/praznim ćelijama."""
    col_a = ws.col_values(1)
    return len(col_a) + 1


def append_dnevnik(
    projekt_key: str, *, radnik: str, telegram_id: int,
    opis: str, lokacija: str, sirova: str, msg_id: int,
    datum_rada: str = "", vrijeme_rada: str = "",
    sati: float | None = None,
    radnici_spomenuti: list[str] | None = None,
    problemi: list[str] | None = None,
    confidence: str = "",
    dt: datetime | None = None,
) -> None:
    dt = dt or datetime.now()
    datum = datum_rada or dt.strftime("%Y-%m-%d")
    ss = get_spreadsheet(projekt_key)
    ws = ss.worksheet(SHEET_DNEVNIK)
    row = [
        datum,
        dt.isoformat(timespec="seconds"),
        radnik,
        str(telegram_id),
        opis,
        lokacija,
        vrijeme_rada,
        "" if sati is None else sati,
        ", ".join(radnici_spomenuti or []),
        " | ".join(problemi or []),
        sirova,
        confidence,
        str(msg_id),
    ]
    r = _next_empty_row(ws)
    ws.update(
        values=[row],
        range_name=f"A{r}:M{r}",
        value_input_option="USER_ENTERED",
    )


def append_materijal(
    projekt_key: str, *, radnik: str, telegram_id: int,
    sifra: str, opis: str, kolicina: float, jm: str,
    lokacija: str = "", napomena: str = "", dt: datetime | None = None,
) -> None:
    dt = dt or datetime.now()
    ss = get_spreadsheet(projekt_key)
    ws = ss.worksheet(SHEET_MATERIJALI)
    row = [
        dt.strftime("%Y-%m-%d"),
        dt.strftime("%H:%M"),
        radnik,
        str(telegram_id),
        sifra,
        opis,
        kolicina,
        jm,
        lokacija,
        napomena,
    ]
    r = _next_empty_row(ws)
    ws.update(
        values=[row],
        range_name=f"A{r}:J{r}",
        value_input_option="USER_ENTERED",
    )


def append_weather(
    projekt_key: str, *, datum: str, min_temp: float, max_temp: float,
    oborine: float, opis: str,
) -> None:
    ss = get_spreadsheet(projekt_key)
    ws = ss.worksheet(SHEET_VRIJEME)
    existing = ws.get_all_records()
    if any(row.get("Datum") == datum for row in existing):
        return
    ws.append_row([datum, min_temp, max_temp, oborine, opis],
                  value_input_option="USER_ENTERED")


def get_dnevnik_za_datum(projekt_key: str, datum: str) -> list[dict[str, Any]]:
    ss = get_spreadsheet(projekt_key)
    ws = ss.worksheet(SHEET_DNEVNIK)
    return [r for r in ws.get_all_records() if r.get("Datum") == datum]


def get_materijali_za_datum(projekt_key: str, datum: str) -> list[dict[str, Any]]:
    ss = get_spreadsheet(projekt_key)
    ws = ss.worksheet(SHEET_MATERIJALI)
    return [r for r in ws.get_all_records() if r.get("Datum") == datum]


def get_materijali_period(
    projekt_key: str,
    od: str | None = None,
    do: str | None = None,
) -> list[dict[str, Any]]:
    """Vrati sve materijale u rasponu datuma (YYYY-MM-DD, inkluzivno).
    od=None znači od početka; do=None znači do kraja."""
    ss = get_spreadsheet(projekt_key)
    ws = ss.worksheet(SHEET_MATERIJALI)
    out = []
    for r in ws.get_all_records():
        d = str(r.get("Datum", "")).strip()
        if not d:
            continue
        if od and d < od:
            continue
        if do and d > do:
            continue
        out.append(r)
    return out


def get_weather_za_datum(projekt_key: str, datum: str) -> dict[str, Any] | None:
    ss = get_spreadsheet(projekt_key)
    ws = ss.worksheet(SHEET_VRIJEME)
    for row in ws.get_all_records():
        if row.get("Datum") == datum:
            return row
    return None


def list_radnici(projekt_key: str) -> list[dict[str, Any]]:
    ss = get_spreadsheet(projekt_key)
    try:
        ws = ss.worksheet(SHEET_RADNICI)
    except WorksheetNotFound:
        return []
    return [r for r in ws.get_all_records() if str(r.get("Aktivan")).lower() in ("da", "true", "1", "")]


def is_known_worker(telegram_id: int) -> bool:
    """Provjeri je li telegram_id u Radnici listi bilo kojeg aktivnog projekta."""
    for p in list_projekti():
        try:
            if get_radnik(p["key"], telegram_id):
                return True
        except Exception:
            continue
    return False


def upsert_radnik(
    projekt_key: str, telegram_id: int, ime: str,
    kvalifikacija: str = "",
) -> None:
    ss = get_spreadsheet(projekt_key)
    ws = ss.worksheet(SHEET_RADNICI)
    records = ws.get_all_records()
    for i, r in enumerate(records, start=2):
        if str(r.get("Telegram_ID")) == str(telegram_id):
            ws.update(f"A{i}:D{i}", [[
                str(telegram_id), ime, kvalifikacija, "Da",
            ]])
            return
    ws.append_row([str(telegram_id), ime, kvalifikacija, "Da"],
                  value_input_option="USER_ENTERED")


def get_radnik(projekt_key: str, telegram_id: int) -> dict[str, Any] | None:
    for r in list_radnici(projekt_key):
        if str(r.get("Telegram_ID")) == str(telegram_id):
            return r
    return None


def replace_troskovnik(projekt_key: str, rows: list[list[Any]]) -> int:
    """Zamijeni cijeli sadržaj Troskovnik lista (osim headera) s novim retcima.
    Svaki redak mora odgovarati HEADERS[SHEET_TROSKOVNIK]."""
    ss = get_spreadsheet(projekt_key)
    ws = ss.worksheet(SHEET_TROSKOVNIK)
    ws.clear()
    ws.append_row(HEADERS[SHEET_TROSKOVNIK])
    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")
    return len(rows)
