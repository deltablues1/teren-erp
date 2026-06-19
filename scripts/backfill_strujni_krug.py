"""Jednokratni backfill: popuni `strujni_krug` na postojećim zapisima.

- Dnevnik: regex iz sirove poruke (svi spomenuti krugovi).
- Materijali: re-parse sirove poruke novim parserom (AI) → pridruži krug po
  (datum, JM, količina) na postojeće retke koji još nemaju krug. NE stvara nove
  retke; samo ažurira prazne.

Pokreni:  py -m scripts.backfill_strujni_krug [projekt_key]
"""
from __future__ import annotations

import re
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Windows konzola je cp1250

from sqlalchemy import select

from services import claude_parser, db
from services.models import DnevnikUnos, Materijal

# Krug = broj s opcionalnim slovom i/ili decimalom (9.1, 5a, 2f). Više krugova
# se spaja s "i"/"I"/"and" (npr. "5a I 5b"); zarez/novi red je TERMINATOR, ne
# spojnica — inače se "5b,\n150 cm" pogrešno proširi na mjeru 150.
_KRUG_RE = re.compile(
    r"krug\w*\s+([0-9]+[a-z]?(?:\.[0-9]+[a-z]?)?"
    r"(?:\s+(?:i|I|and)\s+[0-9]+[a-z]?(?:\.[0-9]+[a-z]?)?)*)",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r"\s+(?:i|I|and)\s+")


def izvuci_krugove(tekst: str) -> list[str]:
    """Svi strujni krugovi spomenuti u tekstu, redoslijedom, bez duplikata."""
    out: list[str] = []
    for m in _KRUG_RE.finditer(tekst or ""):
        for tok in _SPLIT_RE.split(m.group(1)):
            tok = tok.strip()
            if tok and tok not in out:
                out.append(tok)
    return out


def backfill(projekt_key: str | None = None) -> None:
    with db.session() as s:
        stmt = select(DnevnikUnos)
        if projekt_key:
            stmt = stmt.where(DnevnikUnos.projekt_key == projekt_key)
        dnevnici = s.scalars(stmt).all()

        dnevnik_n = 0
        materijal_n = 0
        for d in dnevnici:
            krugovi = izvuci_krugove(d.sirova)
            if krugovi and not d.strujni_krug:
                d.strujni_krug = ", ".join(krugovi)[:120]
                dnevnik_n += 1

            # re-parse za pridruživanje kruga pojedinim materijalima
            if not d.sirova:
                continue
            try:
                parsed = claude_parser.parse_report(d.sirova)
            except Exception as e:  # noqa: BLE001
                print(f"  ! parse pao za dnevnik #{d.id}: {e}")
                continue

            # kandidati: materijali tog projekta+datuma bez kruga
            mats = s.scalars(select(Materijal).where(
                Materijal.projekt_key == d.projekt_key,
                Materijal.datum == d.datum,
            )).all()
            for pm in parsed.materijali:
                krug = (pm.get("strujni_krug") or parsed.strujni_krug or "").strip()
                if not krug:
                    continue
                pk = round(float(pm.get("kolicina") or 0), 2)
                pjm = str(pm.get("jm") or "").strip().lower()
                for m in mats:
                    if (m.strujni_krug or "").strip():
                        continue
                    if round(m.kolicina or 0, 2) == pk and (m.jm or "").strip().lower() == pjm:
                        m.strujni_krug = krug[:60]
                        materijal_n += 1
                        break

        print(f"Dnevnik ažuriran: {dnevnik_n} | Materijali ažurirani: {materijal_n}")


if __name__ == "__main__":
    key = sys.argv[1] if len(sys.argv) > 1 else None
    backfill(key)
