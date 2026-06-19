"""Spajanje materijala javljenog s terena na artikle iz kataloga (cjenika).

Koristi se na ključ-u-ruke projektima (bez troškovnika): radnik javi materijal,
bot ga AI-potpomognuto poveže s artiklom iz kataloga (znamo proizvod + cijenu).
Nepoznato se i dalje bilježi (samo bez poveznice) → kasnije pregled u web panelu.

Katalog postoji samo na Postgres backendu; u sheets modu ova funkcija ne radi ništa.
"""
from __future__ import annotations

import logging

from config import DATA_BACKEND

log = logging.getLogger(__name__)


def poveži_materijale_s_katalogom(materijali: list[dict]) -> int:
    """Mutira listu materijala: matchanima postavi sifra_stavke + katalog_naziv,
    nepoznatima postavi treba_u_katalog=True. Vraća broj povezanih."""
    if not materijali or DATA_BACKEND != "postgres":
        return 0

    from services import claude_parser, db_backend

    kandidati = [db_backend.find_artikl_candidates(m.get("opis", "")) for m in materijali]
    if not any(kandidati):
        for m in materijali:
            m["treba_u_katalog"] = True
        return 0

    odabrani = claude_parser.match_materijali_katalog(materijali, kandidati)
    povezano = 0
    for m, art in zip(materijali, odabrani):
        if art:
            m["katalog_artikl_id"] = art["id"]
            m["katalog_naziv"] = art["naziv"]
            if art.get("sifra") and not m.get("sifra_stavke"):
                m["sifra_stavke"] = art["sifra"]
            m["treba_u_katalog"] = False
            povezano += 1
        else:
            m["treba_u_katalog"] = True
    log.info("Katalog: povezano %d/%d materijala", povezano, len(materijali))
    return povezano
