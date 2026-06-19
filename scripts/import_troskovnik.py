"""CLI: AI uvoz Excel troškovnika u Sheets postojećeg projekta.

Korištenje:
    python scripts/import_troskovnik.py projekt_key "put/do/troskovnika.xls"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import sheets, troskovnik_import  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="AI uvoz troškovnika u projekt")
    parser.add_argument("projekt_key", help="Slug projekta (vidi data/projekti.json)")
    parser.add_argument("xlsx", help="Putanja do .xls/.xlsx datoteke")
    args = parser.parse_args()

    if not sheets.get_projekt(args.projekt_key):
        print(f"Projekt '{args.projekt_key}' ne postoji.")
        sys.exit(1)

    path = Path(args.xlsx)
    if not path.exists():
        print(f"File ne postoji: {path}")
        sys.exit(1)

    print(f"Izvlačim i upisujem stavke iz {path.name} (30-90s)...")
    n = troskovnik_import.import_to_sheets(args.projekt_key, path)
    print(f"Upisano {n} stavki u troškovnik projekta '{args.projekt_key}'.")


if __name__ == "__main__":
    main()
