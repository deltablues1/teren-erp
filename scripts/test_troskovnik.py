"""DRY-RUN test AI uvoza troškovnika - NE upisuje u Sheets.

Izvuče stavke iz Excela i ispiše ih u datoteku da provjeriš kvalitetu
prije nego ih stvarno uvezeš. Treba samo ANTHROPIC_API_KEY u .env.

Korištenje:
    python scripts/test_troskovnik.py "putanja/do/troskovnika.xls"
    python scripts/test_troskovnik.py "putanja/do/troskovnika.xls" --out rezultat.txt
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

# Windows konzola je cp1250 - prebaci ispis na UTF-8 da kvačice rade
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import troskovnik_import  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="DRY-RUN uvoz troškovnika")
    parser.add_argument("xlsx", help="Putanja do .xls/.xlsx")
    parser.add_argument("--out", default="troskovnik_rezultat.txt")
    args = parser.parse_args()

    path = Path(args.xlsx)
    if not path.exists():
        # fallback: tretiraj arg kao podstring i traži u primjeri/ folderima
        needle = args.xlsx.lower()
        roots = [
            Path("primjeri"), Path("../primjeri"),
            Path(__file__).resolve().parent.parent.parent / "primjeri",
        ]
        found = None
        for root in roots:
            if root.exists():
                for f in root.iterdir():
                    if (f.suffix.lower() in (".xls", ".xlsx")
                            and needle in f.name.lower()):
                        found = f
                        break
            if found:
                break
        if found:
            path = found
            print(f"Pronađeno: {path.name}")
        else:
            print(f"File ne postoji i nije nađen po '{args.xlsx}'")
            sys.exit(1)

    print(f"Izvlačim stavke iz {path.name} (ovo može potrajati 30-90s)...")
    stavke = troskovnik_import.extract_stavke(path)

    out = io.open(args.out, "w", encoding="utf-8")
    out.write(f"IZVUČENO {len(stavke)} STAVKI iz {path.name}\n")
    out.write("=" * 80 + "\n\n")

    trenutna_sekcija = None
    for s in stavke:
        if s.sekcija != trenutna_sekcija:
            trenutna_sekcija = s.sekcija
            out.write(f"\n### SEKCIJA: {s.sekcija}\n")
        kol = f"{s.kolicina:g}" if s.kolicina is not None else "—"
        cij = f"{s.cijena:g}" if s.cijena is not None else "—"
        out.write(
            f"  [{s.sifra or '?'}] ({s.tip}) {s.opis}\n"
            f"      JM={s.jm} | količina={kol} | cijena={cij}\n"
        )
        if s.pozicija:
            out.write(f"      pozicija: {s.pozicija}\n")
        if s.kljucne_rijeci:
            out.write(f"      žargon: {', '.join(s.kljucne_rijeci)}\n")
    out.close()

    # statistika
    kompleti = sum(1 for s in stavke if s.tip == "komplet")
    mjerljive = sum(1 for s in stavke if s.tip == "stavka")
    sekcije = len({s.sekcija for s in stavke})
    print(f"\nGotovo. {len(stavke)} stavki ({mjerljive} mjerljivih, "
          f"{kompleti} kompleta) u {sekcije} sekcija.")
    print(f"Rezultat zapisan u: {args.out}")
    print("Otvori tu datoteku i provjeri jesu li stavke točno izvučene.")


if __name__ == "__main__":
    main()
