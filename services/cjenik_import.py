"""Uvoz cjenika dobavljača (Excel) u katalog.

Za razliku od troškovnika (neuredan, AI-asistiran), cjenici dobavljača su uredne
tablice s jasnim stupcima → deterministička ekstrakcija (točne cijene, bez tokena).

Heuristika:
  - header redak = prvi redak koji sadrži 'naziv' i 'cijena'
  - redak sa cijenom + nazivom = STAVKA
  - redak bez cijene ali s tekstom = naziv SEKCIJE/kategorije (npr. 'MIKRO RASPRŠIVAČI')

Rezultat je lista dictova spremnih za db_backend.import_cjenik().
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from services import excel_reader

log = logging.getLogger(__name__)

_client = Anthropic(api_key=ANTHROPIC_API_KEY)


def _strip_dia(s: str) -> str:
    """Makni hrvatske dijakritike za robustnu usporedbu zaglavlja."""
    repl = {"š": "s", "đ": "d", "č": "c", "ć": "c", "ž": "z",
            "Š": "s", "Đ": "d", "Č": "c", "Ć": "c", "Ž": "z"}
    return "".join(repl.get(ch, ch) for ch in s).lower()


# header keyword → logički naziv stupca
_HEADER_MAP = [
    ("naziv", "naziv"),
    ("cijena", "cijena"),
    ("sifra", "sifra"),
    ("katalo", "katbroj"),
    ("rab", "rabat"),
]


def _detect_columns(rows: list[list[Any]]) -> tuple[int, dict[str, int]]:
    """Vrati (indeks_header_retka, {logički_stupac: indeks}). Skenira prvih 20 redaka."""
    for idx, row in enumerate(rows[:20]):
        norm = [_strip_dia(str(c)) for c in row]
        joined = " ".join(norm)
        if "naziv" in joined and "cijena" in joined:
            cols: dict[str, int] = {}
            for ci, cell in enumerate(norm):
                for kw, logical in _HEADER_MAP:
                    if kw in cell and logical not in cols:
                        cols[logical] = ci
            if "naziv" in cols and "cijena" in cols:
                return idx, cols
    raise ValueError(
        "Ne mogu pronaći header redak (treba sadržavati 'naziv' i 'cijena'). "
        "Provjeri da je ovo cjenik s tabličnim stupcima."
    )


def _cell(row: list[Any], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    v = row[idx]
    return "" if v is None else str(v).strip()


def _num(raw: str) -> float | None:
    if not raw:
        return None
    s = raw.replace(" ", "").replace("€", "").replace("kn", "")
    # hrvatski decimalni zarez: "1.234,56" → "1234.56"
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_excel(path: str | Path) -> list[dict[str, Any]]:
    """Parsiraj Excel cjenik u listu stavki."""
    rows = excel_reader.read_rows(path)
    header_idx, cols = _detect_columns(rows)
    log.info("Header u retku %d, stupci: %s", header_idx + 1, cols)

    stavke: list[dict[str, Any]] = []
    kategorija = ""
    for row in rows[header_idx + 1:]:
        if not any(str(c).strip() for c in row):
            continue
        naziv = _cell(row, cols.get("naziv"))
        cijena = _num(_cell(row, cols.get("cijena")))

        if naziv and cijena is not None:
            stavke.append({
                "sifra_dobavljaca": _cell(row, cols.get("sifra")),
                "naziv": naziv,
                "jm": "",  # cjenici dobavljača često nemaju JM stupac
                "cijena": cijena,
                "rabat": _num(_cell(row, cols.get("rabat"))),
                "kategorija": kategorija,
                "proizvodjac": "",
                "zargon": "",
            })
        elif not naziv and cijena is None:
            # redak bez naziva i cijene → vjerojatno naslov sekcije; uzmi prvi tekst
            tekst = next((str(c).strip() for c in row if str(c).strip()), "")
            # ignoriraj čisto numeričke/kratke šifre kao kategoriju
            if tekst and not tekst.replace(".", "").replace("-", "").isdigit() and len(tekst) > 2:
                kategorija = tekst

    log.info("Parsirano %d stavki cjenika iz %s", len(stavke), Path(path).name)
    return stavke


# ============================ PDF (AI ekstrakcija) ===========================

_PDF_TOOL = {
    "name": "izvuci_cjenik",
    "description": (
        "Izvuci stavke cjenika dobavljača iz isječka teksta. Vrati SAMO retke "
        "koji imaju cijenu (€ iznos)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "stavke": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sifra_dobavljaca": {
                            "type": "string",
                            "description": "Šifra/koda artikla (prvi token retka, npr. 'PGJ-04', '462078SP').",
                        },
                        "naziv": {
                            "type": "string",
                            "description": "Opis proizvoda bez šifre, cijene i pakiranja.",
                        },
                        "kategorija": {
                            "type": "string",
                            "description": (
                                "Naziv obitelji/grupe proizvoda (npr. 'Pop-up Rotor PGJ'). "
                                "Obiteljski redak je onaj sa šifrom ALI bez cijene — "
                                "koristi ga kao kategoriju za stavke ispod."
                            ),
                        },
                        "cijena": {
                            "type": ["number", "null"],
                            "description": "Cijena u eurima (decimalni zarez '19,28' → 19.28).",
                        },
                    },
                    "required": ["sifra_dobavljaca", "naziv", "kategorija", "cijena"],
                },
            },
        },
        "required": ["stavke"],
    },
}

_PDF_SYSTEM = (
    "Ti izvlačiš stavke iz cjenika dobavljača Zeleni Elementi (Hunter, oprema za "
    "navodnjavanje). Format retka: ŠIFRA  OPIS  CIJENA  PAKIRANJE. Cijena ima "
    "decimalni zarez (npr. 19,28). ZADNJI broj u retku je pakiranje (NE cijena).\n\n"
    "PRAVILA:\n"
    "1. Izvuci SAMO retke koji imaju cijenu.\n"
    "2. Redak sa šifrom ALI bez cijene = naziv obitelji proizvoda → to je "
    "kategorija za stavke koje slijede, NE zasebna stavka.\n"
    "3. Preskoči: indeks/sadržaj, zaglavlja stranica (adresa, 'Koda Kategorija "
    "Opis € Pak.'), footere ('može promijeniti cijene'), prazne retke.\n"
    "4. Talijanski opisi (npr. 'Turbina raggio regolabile') su DUPLIKATI "
    "prethodne stavke — preskoči ih.\n"
    "5. Ako se opis prelio u sljedeći redak (redak bez šifre i bez cijene), "
    "spoji ga u opis prethodne stavke.\n"
    "Uvijek pozovi alat izvuci_cjenik."
)


def _extract_pdf_chunk(text: str, prethodna_kat: str) -> tuple[list[dict[str, Any]], str]:
    user = text
    if prethodna_kat:
        user = f"(Zadnja kategorija prije isječka: '{prethodna_kat}')\n\n{text}"
    resp = _client.messages.create(
        model=CLAUDE_MODEL,
        # gust isječak (do 55 stavki) može probiti 4000 → izlaz se TIHO odsiječe
        # i stavke se izgube. 8000 daje rezervu (i ostaje ispod non-stream limita).
        max_tokens=8000,
        temperature=0,
        system=_PDF_SYSTEM,
        tools=[_PDF_TOOL],
        tool_choice={"type": "tool", "name": "izvuci_cjenik"},
        messages=[{"role": "user", "content": user}],
    )
    if resp.stop_reason == "max_tokens":
        log.warning(
            "cjenik chunk je dosegao max_tokens — stavke iz ovog isječka mogu "
            "biti nepotpune. Smanji CHUNK ili povećaj max_tokens."
        )
    out: list[dict[str, Any]] = []
    kat = prethodna_kat
    for block in resp.content:
        if block.type == "tool_use" and block.name == "izvuci_cjenik":
            for s in block.input.get("stavke", []):
                cijena = s.get("cijena")
                if cijena is None:
                    continue
                kat = str(s.get("kategorija") or kat).strip()
                out.append({
                    "sifra_dobavljaca": str(s.get("sifra_dobavljaca", "")).strip(),
                    "naziv": str(s.get("naziv", "")).strip(),
                    "jm": "",
                    "cijena": float(cijena),
                    "rabat": None,
                    "kategorija": kat,
                    "proizvodjac": "",
                    "zargon": "",
                })
    return out, kat


def parse_pdf(path: str | Path) -> list[dict[str, Any]]:
    """Izvuci stavke iz PDF cjenika (AI, po isječcima teksta)."""
    import pdfplumber

    lines: list[str] = []
    with pdfplumber.open(path) as pdf:
        for pg in pdf.pages:
            txt = pg.extract_text() or ""
            for ln in txt.splitlines():
                ln = ln.strip()
                if ln:
                    lines.append(ln)
    log.info("PDF %s: %d redaka teksta", Path(path).name, len(lines))

    CHUNK = 55
    stavke: list[dict[str, Any]] = []
    kat = ""
    i = 0
    while i < len(lines):
        chunk = "\n".join(lines[i:i + CHUNK])
        s, kat = _extract_pdf_chunk(chunk, kat)
        stavke.extend(s)
        i += CHUNK
    log.info("PDF parsirano %d stavki", len(stavke))
    return stavke


# ================================ dispatch ===================================

def parse_file(path: str | Path) -> list[dict[str, Any]]:
    """Parsiraj cjenik bilo kojeg podržanog formata (.pdf → AI, .xls/.xlsx → deterministički)."""
    if Path(path).suffix.lower() == ".pdf":
        return parse_pdf(path)
    return parse_excel(path)


def import_file(
    path: str | Path,
    *,
    dobavljac: str,
    naziv_cjenika: str = "",
    tip: str = "nabavni",
    datum=None,
    valuta: str = "EUR",
) -> dict[str, Any]:
    """Parsiraj cjenik (Excel ili PDF) i upiši u katalog. Vrati sažetak."""
    from services import db_backend

    stavke = parse_file(path)
    if not stavke:
        raise ValueError("Nijedna stavka nije izvučena — provjeri format cjenika.")
    naziv_cjenika = naziv_cjenika or f"{dobavljac} {Path(path).stem}"[:200]
    return db_backend.import_cjenik(
        dobavljac=dobavljac,
        naziv_cjenika=naziv_cjenika,
        tip=tip,
        datum=datum,
        valuta=valuta,
        stavke=stavke,
    )


# Kompatibilnost: stari naziv
import_excel = import_file
