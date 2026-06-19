"""CLI: Kreira novi projektni Google Sheets izvan bota.

Korištenje:
    python scripts/setup_sheets.py "Naziv projekta" [--adresa "..."] [--investitor "..."]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import sheets  # noqa: E402


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[čć]", "c", text)
    text = re.sub(r"[š]", "s", text)
    text = re.sub(r"[ž]", "z", text)
    text = re.sub(r"[đ]", "d", text)
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description="Kreiraj novi projekt + Sheets")
    parser.add_argument("naziv", help="Naziv projekta (npr. 'Vinkovci 5')")
    parser.add_argument("--adresa", default="")
    parser.add_argument("--investitor", default="")
    parser.add_argument("--izvodac", default="")
    parser.add_argument("--nadzorni", default="")
    parser.add_argument("--dozvola", default="")
    parser.add_argument("--key", default=None, help="Slug, default izveden iz naziva")
    args = parser.parse_args()

    key = args.key or _slugify(args.naziv)
    projekt = sheets.create_projekt(
        key=key,
        naziv=args.naziv,
        adresa=args.adresa,
        investitor=args.investitor,
        izvodac=args.izvodac,
        nadzorni=args.nadzorni,
        broj_dozvole=args.dozvola,
    )
    print(f"✅ Projekt '{key}' kreiran")
    print(f"   Sheets URL: {projekt['spreadsheet_url']}")


if __name__ == "__main__":
    main()
