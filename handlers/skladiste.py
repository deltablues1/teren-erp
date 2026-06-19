"""Bot komande vezane uz skladište (/zaduzenja, /zaliha)."""
from __future__ import annotations

import asyncio
import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from config import is_admin
from services import skladiste as skl

log = logging.getLogger(__name__)

# Riječi-punila koje treba ignorirati u upitu "/zaliha ima li kabela na skladištu".
_ZALIHA_STOP = {
    "ima", "imamo", "imali", "li", "na", "je", "da", "koliko", "ostalo", "još",
    "jos", "skladistu", "skladištu", "skladiste", "skladište", "sa", "za", "u",
    "kom", "komada", "metar", "metara", "trenutno", "li", "ima",
}


def _zaliha_tokeni(pojam: str) -> list[str]:
    """Smisleni tokeni iz upita (bez punila, min. 3 znaka)."""
    return [
        t for t in re.split(r"[^0-9a-zžšđčćA-ZĐŠŽČĆ]+", pojam.lower())
        if len(t) >= 3 and t not in _ZALIHA_STOP
    ]


def _zaliha_score(opis: str, tokeni: list[str]) -> int:
    """Koliko tokena (po prefiksu, da 'kabela' pogodi 'Kabel') ima u opisu."""
    low = opis.lower()
    return sum(1 for t in tokeni if t[:5] in low)


async def cmd_zaduzenja(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Radnik: što je trenutno zaduženo na njega. Admin: sažetak svih zaduženja."""
    user = update.effective_user
    if not user or not update.message:
        return

    if is_admin(user.id):
        pregled = await asyncio.to_thread(skl.zaduzenja_pregled)
        if not pregled["radnici"] and not pregled["gradilista"]:
            await update.message.reply_text(
                "Nema aktivnih zaduženja. Detalji i unos: web panel → Skladište."
            )
            return
        lines = ["📦 Trenutna zaduženja:"]
        for z in pregled["radnici"]:
            lines.append(f"\n👷 {z['naziv']}:")
            for st in z["stavke"]:
                lines.append(f"  • {st['opis']}: {st['kolicina']} {st['jm']}")
        for z in pregled["gradilista"]:
            lines.append(f"\n🏗️ {z['naziv']}:")
            for st in z["stavke"]:
                lines.append(f"  • {st['opis']}: {st['kolicina']} {st['jm']}")
        lines.append("\nUnos/izmjene: web panel → Skladište.")
        await update.message.reply_text("\n".join(lines))
        return

    stavke = await asyncio.to_thread(skl.stanje, "radnik", str(user.id))
    if not stavke:
        await update.message.reply_text("Trenutno nemaš ništa zaduženo. 👍")
        return
    lines = ["📦 Zaduženo na tebe:"]
    for st in stavke:
        lines.append(f"  • {st['opis']}: {st['kolicina']} {st['jm']}")
    lines.append("\nAko si nešto vratio ili predao dalje, javi voditelju.")
    await update.message.reply_text("\n".join(lines))


async def cmd_zaliha(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Radnik/admin: provjeri stanje centralnog skladišta.

    /zaliha               → cijelo stanje skladišta
    /zaliha kabel         → samo stavke koje odgovaraju pojmu
    /zaliha ima li utičnica na skladištu → punila se ignoriraju
    """
    if not update.effective_user or not update.message:
        return

    pojam = " ".join(context.args or []).strip()
    stavke = await asyncio.to_thread(skl.stanje, "skladiste", "")

    if pojam:
        tokeni = _zaliha_tokeni(pojam)
        if tokeni:
            scored = [(s, _zaliha_score(s["opis"], tokeni)) for s in stavke]
            stavke = [s for s, sc in sorted(scored, key=lambda x: -x[1]) if sc > 0]

    if not stavke:
        if pojam:
            await update.message.reply_text(
                f"Na skladištu trenutno nema „{pojam}”. "
                f"Ako treba naručiti, javi voditelju."
            )
        else:
            await update.message.reply_text("Skladište je trenutno prazno.")
        return

    naslov = f"📦 Skladište — „{pojam}”:" if pojam else "📦 Stanje skladišta:"
    lines = [naslov]
    for st in stavke[:40]:
        lines.append(f"  • {st['opis']}: {st['kolicina']} {st['jm']}")
    if len(stavke) > 40:
        lines.append(f"  … i još {len(stavke) - 40}. Suzi pretragu (npr. /zaliha kabel).")
    await update.message.reply_text("\n".join(lines))
