"""Handlere za /start, /id i odabir aktivnog projekta."""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import is_admin
from services import repository as repo, sessions

log = logging.getLogger(__name__)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pozdravna poruka + dugmad za odabir projekta."""
    user = update.effective_user
    if not user:
        return

    projekti = repo.list_projekti()
    if not projekti:
        if is_admin(user.id):
            await update.message.reply_text(
                "Bok! Trenutno nema aktivnih projekata.\n"
                "Kreiraj prvi sa /noviprojekt"
            )
        else:
            await update.message.reply_text(
                "Bok! Trenutno nema aktivnih projekata. "
                "Javi voditelju da ih kreira."
            )
        return

    keyboard = [
        [InlineKeyboardButton(p["naziv"], callback_data=f"setprojekt:{p['key']}")]
        for p in projekti
    ]
    await update.message.reply_text(
        f"Bok {user.first_name}! Odaberi na kojem projektu danas radiš:\n\n"
        f"ℹ️ Provjera skladišta: /zaliha kabel · zaduženja na tebe: /zaduzenja",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Vrati korisniku njegov Telegram ID (za dodavanje u tim)."""
    user = update.effective_user
    if not user:
        return
    await update.message.reply_text(
        f"Tvoj Telegram ID:\n`{user.id}`\n\n"
        f"Pošalji ovaj broj voditelju da te doda u tim.",
        parse_mode="Markdown",
    )


async def cmd_odjava(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Odjavi se s trenutnog projekta."""
    user = update.effective_user
    if not user:
        return
    sessions.clear_active_projekt(user.id)
    await update.message.reply_text(
        "Odjavljen si. Pošalji /start za odabir drugog projekta."
    )


async def cmd_projekt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prikaži trenutni projekt + opciju promjene."""
    user = update.effective_user
    if not user:
        return
    aktivni = sessions.get_active_projekt(user.id)
    if aktivni:
        p = repo.get_projekt(aktivni)
        naziv = p["naziv"] if p else aktivni
        await update.message.reply_text(
            f"Trenutni projekt: *{naziv}*\n\n"
            f"Za promjenu pošalji /start, za odjavu /odjava.",
            parse_mode="Markdown",
        )
    else:
        await cmd_start(update, context)
