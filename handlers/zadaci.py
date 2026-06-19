"""Bot UI za zadatke: prikaz radniku, /zadaci, gumbi Gotovo/Odgodi.

Zadatke kreira voditelj u web panelu; bot ih radniku prikazuje pri odabiru
projekta, na /zadaci i kao push (šalje panel). Odgovor radnika = Telegram
reply na poruku zadatka (hvata ga handlers/report.py).
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import ADMIN_TELEGRAM_ID, is_admin
from services import repository as repo, sessions, zadaci as zsrv

log = logging.getLogger(__name__)


def _keyboard(zadatak_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Gotovo", callback_data=f"ztgotovo:{zadatak_id}"),
        InlineKeyboardButton("⏰ Odgodi sutra", callback_data=f"zodgodi:{zadatak_id}"),
    ]])


def format_zadatak(z: dict, projekt_naziv: str = "") -> str:
    """Tekst poruke zadatka — namjerno bez Markdowna (sadržaj piše voditelj)."""
    lines = [f"📋 ZADATAK{f' — {projekt_naziv}' if projekt_naziv else ''}", "", z["tekst"]]
    if z.get("rok"):
        lines.append("")
        lines.append(f"📅 Rok: {z['rok']}")
    lines.append("")
    lines.append("Odgovori na OVU poruku (reply) ako imaš pitanje ili napomenu.")
    return "\n".join(lines)


def keyboard_dict(zadatak_id: int) -> dict:
    """Isti gumbi kao _keyboard, ali kao dict za HTTP API (web panel push)."""
    return {"inline_keyboard": [[
        {"text": "✅ Gotovo", "callback_data": f"ztgotovo:{zadatak_id}"},
        {"text": "⏰ Odgodi sutra", "callback_data": f"zodgodi:{zadatak_id}"},
    ]]}


async def posalji_zadatke(
    bot: Bot, chat_id: int, projekt_key: str, telegram_id: int,
    projekt_naziv: str = "",
) -> int:
    """Pošalji radniku sve otvorene zadatke projekta (svaki kao zasebnu poruku,
    da reply mapira na točan zadatak). Vraća broj poslanih."""
    zadaci = await asyncio.to_thread(
        zsrv.list_otvoreni, projekt_key, telegram_id
    )
    for z in zadaci:
        msg = await bot.send_message(
            chat_id,
            format_zadatak(z, projekt_naziv),
            reply_markup=_keyboard(z["id"]),
        )
        await asyncio.to_thread(
            zsrv.zabiljezi_poruku, z["id"], chat_id, msg.message_id
        )
    return len(zadaci)


async def posalji_podsjetnike(bot: Bot, include_rok: bool = True) -> int:
    """Pošalji podsjetnike za dospjele odgođene zadatke (+ one s rokom).
    Vraća broj zadataka za koje je podsjetnik poslan."""
    zadaci = await asyncio.to_thread(zsrv.zadaci_za_podsjetnik, include_rok)
    if not zadaci:
        return 0

    nazivi: dict[str, str] = {}
    poslano = 0
    for z in zadaci:
        key = z["projekt_key"]
        if key not in nazivi:
            p = await asyncio.to_thread(repo.get_projekt, key)
            nazivi[key] = p["naziv"] if p else key

        tekst = "🔔 PODSJETNIK\n\n" + format_zadatak(z, nazivi[key])
        primatelji = await asyncio.to_thread(zsrv.primatelji, key, z["telegram_id"])
        for r in primatelji:
            try:
                msg = await bot.send_message(
                    r["telegram_id"], tekst, reply_markup=_keyboard(z["id"]),
                )
                await asyncio.to_thread(
                    zsrv.zabiljezi_poruku, z["id"], r["telegram_id"], msg.message_id
                )
            except Exception:
                log.warning(
                    "Podsjetnik nije isporučen radniku %s (zadatak %s)",
                    r["telegram_id"], z["id"],
                )
        await asyncio.to_thread(zsrv.ocisti_snooze, z["id"])
        poslano += 1
    return poslano


async def cmd_zadaci(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Otvoreni zadaci aktivnog projekta (radnik: njegovi; admin: svi)."""
    user = update.effective_user
    if not user or not update.message:
        return

    projekt_key = sessions.get_active_projekt(user.id)
    if not projekt_key:
        await update.message.reply_text("Prvo odaberi projekt sa /start.")
        return

    tid = None if is_admin(user.id) else user.id
    # admin vidi i odgođene (include_snoozed), radnik samo aktivne
    zadaci = await asyncio.to_thread(
        zsrv.list_otvoreni, projekt_key, tid, is_admin(user.id)
    )
    if not zadaci:
        await update.message.reply_text("Nema otvorenih zadataka 👍")
        return

    projekt = repo.get_projekt(projekt_key)
    naziv = projekt["naziv"] if projekt else projekt_key
    for z in zadaci:
        msg = await update.message.reply_text(
            format_zadatak(z, naziv),
            reply_markup=_keyboard(z["id"]),
        )
        await asyncio.to_thread(
            zsrv.zabiljezi_poruku, z["id"], update.message.chat_id, msg.message_id
        )


async def cb_gotovo(query, payload: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        zadatak_id = int(payload)
    except ValueError:
        return
    z = await asyncio.to_thread(zsrv.oznaci_gotovo, zadatak_id, query.from_user.id)
    if not z:
        await query.edit_message_text(
            f"{query.message.text}\n\n☑️ (zadatak je već riješen ili obrisan)"
        )
        return
    await query.edit_message_text(f"{query.message.text}\n\n✅ GOTOVO")

    # javi voditelju (osim ako je voditelj sam kliknuo)
    if query.from_user.id != ADMIN_TELEGRAM_ID:
        ime = await asyncio.to_thread(zsrv.ime_radnika, query.from_user.id)
        ime = ime or query.from_user.full_name or str(query.from_user.id)
        try:
            await context.bot.send_message(
                ADMIN_TELEGRAM_ID,
                f"✅ {ime} je završio zadatak ({z['projekt_key']}):\n{z['tekst']}",
            )
        except Exception:
            log.warning("Ne mogu javiti adminu za gotov zadatak %s", zadatak_id)


async def cb_odgodi(query, payload: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        zadatak_id = int(payload)
    except ValueError:
        return
    z = await asyncio.to_thread(zsrv.odgodi, zadatak_id)
    if not z:
        await query.edit_message_text(
            f"{query.message.text}\n\n☑️ (zadatak je već riješen ili obrisan)"
        )
        return
    await query.edit_message_text(
        f"{query.message.text}\n\n⏰ Odgođeno do sutra ({zsrv.SNOOZE_DO_SATI:02d}:00) — "
        f"vidjet ćeš ga opet kod odabira projekta ili na /zadaci."
    )
