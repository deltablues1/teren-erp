"""AI-asistirani uvoz troškovnika.

Sirovi Excel (bilo koje strukture) → Claude izvlači strukturirane stavke.
Robusno na razlike u formatu (sekcije/pozicije/podstavke, OPĆI UVJETI, kompleti)."""
from __future__ import annotations

import logging
import os
import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from anthropic import Anthropic
from dotenv import load_dotenv

from services import excel_reader

log = logging.getLogger(__name__)

# Namjerno čitamo ključ direktno (ne preko config-a) da se uvoz troškovnika
# može testirati samo s ANTHROPIC_API_KEY, bez Telegram/Google konfiguracije.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6").strip() or "claude-sonnet-4-6"

if not _API_KEY:
    raise RuntimeError(
        "ANTHROPIC_API_KEY nije postavljen u .env — potreban za uvoz troškovnika."
    )

_client = Anthropic(api_key=_API_KEY)

# Sonnet 4.6 podržava do 64K izlaznih tokena, ali ovaj uvoz radi sinkrono (bez
# streaminga), pa max_tokens držimo na 16000 (sigurno ispod SDK HTTP timeouta).
# Ranije je bio 8000 — premalen: JSON s puno stavki bi se TIHO odsjekao na pola
# (stop_reason="max_tokens") i chunk bi vratio 0 stavki. CHUNK_ROWS je smanjen
# da izlaz jednog chunka ostane s rezervom ispod max_tokens.
# Mjereno: gust list (~76 stavki) na 80 redaka troši ~13.6K izlaznih tokena.
# CHUNK_ROWS=60 drži izlaz na ~10K — udobna rezerva ispod MAX_TOKENS=16000.
MAX_TOKENS = 16000
CHUNK_ROWS = 60
CHUNK_OVERLAP = 6


@dataclass
class Stavka:
    sifra: str
    sekcija: str
    pozicija: str
    opis: str
    jm: str
    kolicina: float | None
    cijena: float | None
    tip: str                      # "stavka" | "komplet"
    kljucne_rijeci: list[str] = field(default_factory=list)

    def as_row(self) -> list[Any]:
        return [
            self.sifra,
            self.sekcija,
            self.pozicija,
            self.opis,
            self.jm,
            "" if self.kolicina is None else self.kolicina,
            "" if self.cijena is None else self.cijena,
            self.tip,
            ", ".join(self.kljucne_rijeci),
            0,   # Izvedeno
            "",  # Razlika
        ]


EXTRACT_TOOL = {
    "name": "izvuci_stavke",
    "description": (
        "Izvuci obračunske stavke iz isječka troškovnika elektroinstalacija. "
        "Vrati SAMO stvarne stavke s jedinicom mjere i (planiranom) količinom. "
        "Preskoči: opće uvjete, napomene, separatore (---), prazne retke, "
        "i 'st. N' / 'Ukupno' zbrojne retke. Sekcije i pozicije koristi za "
        "kontekst (sekcija/pozicija polja), ali ih NE vraćaj kao zasebne stavke."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "stavke": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sifra": {
                            "type": "string",
                            "description": (
                                "Šifra iz troškovnika u izvornom formatu: "
                                "'4.2.1.1' (numerička hijerarhija), '2.A.1' "
                                "(slovni indeks), '+GRO' (nazivni kod "
                                "razdjelnika). OBAVEZNO sub-numeriranje za "
                                "različite podstavke iste pozicije. Kompleti "
                                "dobivaju samo kod pozicije. BEZ završne točke. "
                                "NIKAD 'R<broj>'. NIKAD restart [1] za sub-"
                                "troškovnik (4.9 + 3.1 → '4.9.3.1'). Bolje "
                                "PRAZNO nego pogrešno."
                            ),
                        },
                        "sekcija": {
                            "type": "string",
                            "description": (
                                "Naziv sekcije/grupe (npr. 'ENERGETSKI RAZVOD', "
                                "'RASVJETA', 'RAZVODNI ORMARI')."
                            ),
                        },
                        "pozicija": {
                            "type": "string",
                            "description": (
                                "Naslov pozicije pod kojom je stavka (npr. 'Dobava "
                                "i polaganje energetskih kabela reda 1kV'). Prazno "
                                "ako stavka sama je pozicija."
                            ),
                        },
                        "opis": {
                            "type": "string",
                            "description": (
                                "Opis stavke (npr. 'NYM-J 3x2,5 mm2', 'stropna "
                                "nadgradna svjetiljka', 'Razdjelnik +GRO')."
                            ),
                        },
                        "jm": {
                            "type": "string",
                            "description": (
                                "Jedinica mjere normalizirano: 'm' (dužni metar, "
                                "uklj. m1/m'), 'kom', 'kpl' (komplet/kompl), 'kg', "
                                "'m2', 'm3'. Zadrži original ako ne odgovara."
                            ),
                        },
                        "kolicina": {
                            "type": ["number", "null"],
                            "description": (
                                "Ugovorena/planirana količina. Null ako nije navedena."
                            ),
                        },
                        "cijena": {
                            "type": ["number", "null"],
                            "description": "Jedinična cijena. Null ako nije navedena.",
                        },
                        "tip": {
                            "type": "string",
                            "enum": ["stavka", "komplet"],
                            "description": (
                                "'komplet' ako je cijela pozicija obračunata kao "
                                "1 kpl sklop (npr. razdjelnik sa svim komponentama). "
                                "'stavka' za mjerljivu pojedinačnu stavku (kabel, "
                                "svjetiljka, cijev)."
                            ),
                        },
                        "kljucne_rijeci": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Hrvatski žargon koji radnik stvarno govori. "
                                "ZABRANJENI engleski izrazi (downlight, junction "
                                "box, pendant itd.). Dozvoljene su tehničke oznake "
                                "tipa 'NYM-J 3x2,5'. Primjeri: 'NYM-J 3x2,5'→"
                                "['petica','3x2.5','3x2,5','peticа']; "
                                "'instalacijska cijev'→['gibljiva cijev','rebrasta',"
                                "'crijevo','pakir']; 'doza'→['kutija','razvodna "
                                "kutija','spojna kutija']. 2-5 izraza."
                            ),
                        },
                    },
                    "required": [
                        "sifra", "sekcija", "pozicija", "opis", "jm",
                        "kolicina", "cijena", "tip", "kljucne_rijeci",
                    ],
                },
            },
        },
        "required": ["stavke"],
    },
}

SYSTEM = (
    "Ti si stručnjak za hrvatske elektroinstalaterske troškovnike. "
    "Iz sirovih redaka Excel troškovnika izvlačiš strukturirane obračunske "
    "stavke. Razumiješ hijerarhiju: sekcija (4.2 ENERGETSKI RAZVOD) → pozicija "
    "(1. Dobava i polaganje...) → podstavke (- NYM-J 3x2,5 mm2, m, 200). "
    "Razumiješ da se 'st. N' i 'Ukupno' retci zbrajaju i NISU stavke. "
    "Razumiješ da razdjelnici/ormari imaju puno komponenti ali se obračunavaju "
    "kao 1 komplet. Uvijek pozoveš alat izvuci_stavke.\n\n"
    "PRAVILA ZA ŠIFRE (slijedi izvorni troškovnik):\n"
    "1. ZADRŽI ORIGINALNI FORMAT iz troškovnika kad god je vidljiv. Hrvatski "
    "troškovnici koriste razne sheme — sve su valjane ako su prepoznatljive:\n"
    "   a) Hijerarhija s točkama: '4.2.1.1', '13.33', '4.6.7.3'\n"
    "   b) Slovni indeks unutar pozicije: '2.A.1', '2.B.3', '4.A'\n"
    "   c) Nazivni kod razdjelnika (počinje s '+'): '+GRO', '+RO-DV', '+R0-1'\n"
    "2. OBAVEZNO sub-numeriranje: ako pozicija ima više podstavki, svaka "
    "dobiva svoj sufiks po redu (4.6.1.1, 4.6.1.2... ili 2.A.1, 2.A.2...). "
    "Različite podstavke NE smiju imati istu šifru.\n"
    "3. Iznimka: ako je cijela pozicija jedan komplet (razdjelnik PMO, "
    "antenski sustav itd.), dobiva SAMO kod pozicije — komponente kompleta "
    "su dio opisa, ne zasebne stavke.\n"
    "4. NIKADA ne koristi 'R<broj>' (npr. R375) — to je redak Excela, ne šifra. "
    "Ako redni broj nije vidljiv, izvedi ga iz sekcije + pozicije + indeksa.\n"
    "5. NIKADA ne resetiraj numeriranje na [1], [2] unutar sekcije 4.X — daj "
    "puni prefiks sekcije (sekcija 4.9, sub 3.1 → '4.9.3.1').\n"
    "6. NE izmišljaj sufikse poput 'sub1' ili 'novo' — koristi numerički ili "
    "slovni sufiks koji je naveden u izvoru (.1, .A, .B).\n"
    "7. Ako apsolutno NE možeš odrediti šifru, ostavi PRAZNO — bolje prazno "
    "nego pogrešno.\n\n"
    "STROGA PRAVILA ZA ŽARGON: koristi SAMO hrvatske izraze koje hrvatski "
    "električar stvarno govori na terenu (petica, trojka, doza, modular, "
    "rebrasta, gibljiva, pakir, sapi, hauba, hilti, …). NIKAD ne stavljaj "
    "engleske izraze (downlight, junction box, pendant, wall recessed, "
    "surface mounted, fire stop, RJ45, gateway). Tehničke oznake materijala "
    "kao 'NYM-J 3x2,5' su dozvoljene jer su to identifikatori, ne engleski."
)


def _rows_to_text(rows: list[list[Any]], start: int) -> str:
    lines = []
    for i, row in enumerate(rows):
        cells = []
        for c, val in enumerate(row):
            if val != "" and val is not None:
                col = string.ascii_uppercase[c] if c < 26 else f"C{c}"
                cells.append(f"{col}={val}")
        if cells:
            lines.append(f"R{start + i}: " + " | ".join(cells))
    return "\n".join(lines)


def _extract_chunk(text: str, prethodna_sekcija: str) -> list[Stavka]:
    user = text
    if prethodna_sekcija:
        user = (
            f"(Kontekst: zadnja aktivna sekcija prije ovog isječka bila je "
            f"'{prethodna_sekcija}'. Ako isječak ne navodi novu sekciju, "
            f"koristi ovu.)\n\n{text}"
        )
    resp = _client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        temperature=0,
        system=SYSTEM,
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "izvuci_stavke"},
        messages=[{"role": "user", "content": user}],
    )
    if resp.stop_reason == "max_tokens":
        # Izlaz odsječen na max_tokens → JSON stavki je nepotpun i daje manje
        # (ili 0) stavki. Glasno upozori da se ne ponovi tihi gubitak podataka.
        log.warning(
            "Chunk je dosegao max_tokens (%d) — izlaz odsječen, stavke iz ovog "
            "isječka mogu biti nepotpune. Smanji CHUNK_ROWS ili povećaj MAX_TOKENS.",
            MAX_TOKENS,
        )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "izvuci_stavke":
            out = []
            for s in block.input.get("stavke", []):
                out.append(Stavka(
                    sifra=str(s.get("sifra", "")).strip(),
                    sekcija=str(s.get("sekcija", "")).strip(),
                    pozicija=str(s.get("pozicija", "")).strip(),
                    opis=str(s.get("opis", "")).strip(),
                    jm=str(s.get("jm", "")).strip(),
                    kolicina=_num(s.get("kolicina")),
                    cijena=_num(s.get("cijena")),
                    tip=str(s.get("tip", "stavka")).strip() or "stavka",
                    kljucne_rijeci=[str(k).strip() for k in s.get("kljucne_rijeci", []) if str(k).strip()],
                ))
            return out
    return []


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dedup(stavke: list[Stavka]) -> list[Stavka]:
    """Ukloni duplikate nastale zbog preklapanja chunkova."""
    seen: set[tuple] = set()
    out = []
    for s in stavke:
        key = (s.sifra, s.opis, s.jm, s.kolicina)
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


# Šifra može biti:
#   - hijerarhija s točkama (4, 4.2, 4.2.1.1, 13.33)
#   - sa slovnim indeksom unutar pozicije (2.A.1, 4.B, 1.a.3)
#   - nazivni kod razdjelnika (+GRO, +RO-DV, +R0-1, +R-0.1)
_VALID_SIFRA = re.compile(
    r"^("
    r"\+[A-Za-z][A-Za-z0-9_\-.]*"      # +GRO, +RO-DV, +R0-1, +R-0.1
    r"|"
    r"\d+(\.[A-Za-z0-9]+)*"            # 4.2.1.1, 2.A.1, 13.33, 4.A
    r")$"
)


def _post_process(stavke: list[Stavka]) -> list[Stavka]:
    """Normalizira šifre, označava nevažeće s '?-' prefiksom (da se ručno isprave),
    i razrješava duplikate sufiksom .v2, .v3..."""
    invalid = 0
    collisions = 0

    # 1. Normalizacija: skini whitespace i završne točke ("4.8.1." → "4.8.1")
    for s in stavke:
        s.sifra = s.sifra.strip().rstrip(".").strip()

    # 2. Označi nevažeće šifre vidljivim prefiksom za ručni pregled u Sheets-u
    for s in stavke:
        if not s.sifra or not _VALID_SIFRA.match(s.sifra):
            invalid += 1
            original = s.sifra or "?"
            s.sifra = f"?-{original}"

    # 3. Razrješi kolizije sufiksom .v2, .v3
    seen: set[str] = set()
    for s in stavke:
        if s.sifra in seen:
            collisions += 1
            base = s.sifra
            n = 2
            while f"{base}.v{n}" in seen:
                n += 1
            s.sifra = f"{base}.v{n}"
        seen.add(s.sifra)

    if invalid or collisions:
        log.info(
            "Post-process: %d nevažećih šifri (prefiks '?-') i %d kolizija "
            "(sufiks '.vN'). Provjeri u Sheets-u.",
            invalid, collisions,
        )
    return stavke


# Callback(done_chunks, total_chunks) — za izvještavanje napretka (pozadinski uvoz).
ProgressFn = Callable[[int, int], None]


def _chunk_count(n_rows: int) -> int:
    """Koliko chunkova obrađuje extract_stavke za dani broj nepraznih redaka."""
    if n_rows <= 0:
        return 0
    step = CHUNK_ROWS - CHUNK_OVERLAP
    return (n_rows - 1) // step + 1


def extract_stavke(path: str | Path, progress: ProgressFn | None = None) -> list[Stavka]:
    """Pročitaj Excel i izvuci sve obračunske stavke preko Claude-a.

    Čita SVE listove (troškovnici su često razdijeljeni po sekcijama u zasebne
    listove). Ako je `progress` zadan, zove se s (obrađeni_chunkovi,
    ukupno_chunkova) prije obrade i nakon svakog chunka — za prikaz napretka."""
    sheets = excel_reader.read_sheets(path)
    log.info(
        "Pročitano %d listova iz %s: %s",
        len(sheets), path, ", ".join(naziv for naziv, _ in sheets),
    )

    # nepraznti redci po listu (smanji tokene) + ukupan broj chunkova za napredak
    per_sheet = [
        (naziv, [(i, r) for i, r in enumerate(rows) if any(c != "" for c in r)])
        for naziv, rows in sheets
    ]
    total = sum(_chunk_count(len(idx)) for _, idx in per_sheet)
    if progress:
        progress(0, total)

    all_stavke: list[Stavka] = []
    done = 0
    for naziv, indexed in per_sheet:
        # Naziv lista je jaka naznaka sekcije (npr. '3.D.VATRODOJAVA').
        zadnja_sekcija = naziv
        i = 0
        while i < len(indexed):
            window = indexed[i:i + CHUNK_ROWS]
            start_idx = window[0][0]
            text = _rows_to_text([r for _, r in window], start_idx)
            chunk_stavke = _extract_chunk(text, zadnja_sekcija)
            if chunk_stavke:
                zadnja_sekcija = chunk_stavke[-1].sekcija or zadnja_sekcija
                all_stavke.extend(chunk_stavke)
            i += CHUNK_ROWS - CHUNK_OVERLAP
            done += 1
            if progress:
                progress(done, total)

    return _post_process(_dedup(all_stavke))


def import_to_sheets(
    projekt_key: str, path: str | Path, progress: ProgressFn | None = None
) -> int:
    """Izvuci stavke iz Excela i upiši ih u Troskovnik list projekta.
    Zamjenjuje postojeći sadržaj. Vraća broj upisanih stavki."""
    from services import repository as repo
    stavke = extract_stavke(path, progress=progress)
    rows = [s.as_row() for s in stavke]
    repo.replace_troskovnik(projekt_key, rows)
    return len(rows)
