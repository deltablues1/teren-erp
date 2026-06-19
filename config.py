"""Centralni config - učitava .env varijable i izlaže ih ostatku aplikacije."""
from __future__ import annotations

import os
import secrets as _secrets
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
TEMPLATES_DIR = ROOT_DIR / "templates"
GENERATED_DIR = ROOT_DIR / "generated"
SECRETS_DIR = ROOT_DIR / "secrets"

for d in (DATA_DIR, GENERATED_DIR, SECRETS_DIR):
    d.mkdir(parents=True, exist_ok=True)

load_dotenv(ROOT_DIR / ".env")


def _required(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise RuntimeError(
            f"Varijabla {name} nije postavljena u .env. "
            f"Provjeri .env.example za primjer."
        )
    return val


def _optional(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip() or default


TELEGRAM_BOT_TOKEN = _required("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = _required("ANTHROPIC_API_KEY")
OPENAI_API_KEY = _required("OPENAI_API_KEY")

# Backend sloja podataka: "sheets" (Google Sheets) | "postgres" (PostgreSQL, Faza 1).
# Vidi services/repository.py.
DATA_BACKEND = _optional("DATA_BACKEND", "sheets")

GOOGLE_SERVICE_ACCOUNT_JSON = ROOT_DIR / _optional(
    "GOOGLE_SERVICE_ACCOUNT_JSON", "secrets/google-sa.json"
)
# Folder ID je obavezan samo kad se podaci stvarno vode u Google Sheets.
GOOGLE_SHEETS_FOLDER_ID = (
    _required("GOOGLE_SHEETS_FOLDER_ID")
    if DATA_BACKEND == "sheets"
    else _optional("GOOGLE_SHEETS_FOLDER_ID")
)

ADMIN_TELEGRAM_ID = int(_required("ADMIN_TELEGRAM_ID"))

OPENWEATHER_API_KEY = _optional("OPENWEATHER_API_KEY")

CLAUDE_MODEL = _optional("CLAUDE_MODEL", "claude-sonnet-4-6")
TIMEZONE = _optional("TIMEZONE", "Europe/Zagreb")

# Postgres connection string (potreban samo kad je DATA_BACKEND="postgres").
# Primjer: postgresql+psycopg://postgres:LOZINKA@localhost:5432/teren_bot
DATABASE_URL = _optional("DATABASE_URL")

# Web admin panel: lozinka za prijavu (prazno = bez prijave, samo za lokalni rad).
WEB_PASSWORD = _optional("WEB_PASSWORD")
# Tajni ključ za potpis kolačića sesije. Ako nije postavljen u .env, generira se
# nasumičan pri svakom startu (sesije se tada poništavaju restartom — postavi
# WEB_SECRET u .env ako te to smeta).
WEB_SECRET = _optional("WEB_SECRET") or _secrets.token_hex(32)

# Podaci firme za zaglavlje ponuda (sve opcionalno — prikazuje se što postoji).
FIRMA_NAZIV = _optional("FIRMA_NAZIV")
FIRMA_ADRESA = _optional("FIRMA_ADRESA")
FIRMA_OIB = _optional("FIRMA_OIB")
FIRMA_IBAN = _optional("FIRMA_IBAN")
FIRMA_TELEFON = _optional("FIRMA_TELEFON")
FIRMA_EMAIL = _optional("FIRMA_EMAIL")

PROJEKTI_FILE = DATA_DIR / "projekti.json"
RADNICI_FILE = DATA_DIR / "radnici.json"
SESSIONS_FILE = DATA_DIR / "sessions.json"


def is_admin(telegram_id: int) -> bool:
    return telegram_id == ADMIN_TELEGRAM_ID
