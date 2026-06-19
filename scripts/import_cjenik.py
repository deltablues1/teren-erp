"""CLI: uvoz Excel cjenika dobavljača u katalog.

Korištenje:
    py scripts/import_cjenik.py --dobavljac "Inaqua" --datum 2026-02-02 "primjeri/cjenici/IN-AQUA_CJENIK_2026_BLANK_€.xlsx"

Preduvjet: DATABASE_URL u .env (Postgres baza + tablice; pokreni init_db.py ako nisi).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import cjenik_import  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Uvoz cjenika dobavljača u katalog (.xls/.xlsx/.pdf)")
    p.add_argument("xlsx", help="Putanja do cjenika (.xls/.xlsx/.pdf)")
    p.add_argument("--dobavljac", required=True, help="Naziv dobavljača (npr. Inaqua)")
    p.add_argument("--naziv", default="", help="Naziv cjenika (default: dobavljač + ime datoteke)")
    p.add_argument("--datum", default="", help="Datum cjenika YYYY-MM-DD (opcionalno)")
    p.add_argument("--tip", default="nabavni", choices=["nabavni", "prodajni"])
    p.add_argument("--valuta", default="EUR")
    args = p.parse_args()

    path = Path(args.xlsx)
    if not path.exists():
        print(f"Datoteka ne postoji: {path}")
        sys.exit(1)

    datum = None
    if args.datum:
        try:
            datum = datetime.strptime(args.datum, "%Y-%m-%d").date()
        except ValueError:
            print(f"Neispravan datum '{args.datum}', očekujem YYYY-MM-DD.")
            sys.exit(1)

    print(f"Uvozim cjenik '{args.dobavljac}' iz {path.name} ...")
    res = cjenik_import.import_file(
        path,
        dobavljac=args.dobavljac,
        naziv_cjenika=args.naziv,
        tip=args.tip,
        datum=datum,
        valuta=args.valuta,
    )
    print(
        f"✅ Gotovo. Cjenik #{res['cjenik_id']} ({res['partner']}): "
        f"{res['stavki']} stavki — {res['novi_artikli']} novih artikala, "
        f"{res['povezani_postojeci']} povezano s postojećima."
    )


if __name__ == "__main__":
    main()
