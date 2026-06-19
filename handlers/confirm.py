"""Callback handler za inline gumbe (Točno/Ispravi/Odbaci/odabir projekta)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import ADMIN_TELEGRAM_ID
from services import repository as repo, sessions

log = logging.getLogger(__name__)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    action, _, payload = query.data.partition(":")

    if action == "setprojekt":
        await _set_projekt(query, payload)
    elif action == "confirm":
        await _confirm(query, payload, context)
    elif action == "edit":
        await _edit(query, payload, context)
    elif action == "cancel":
        await _cancel(query, payload)
    elif action == "ztgotovo":
        from handlers import zadaci as zadaci_ui
        await zadaci_ui.cb_gotovo(query, payload, context)
    elif action == "zodgodi":
        from handlers import zadaci as zadaci_ui
        await zadaci_ui.cb_odgodi(query, payload, context)
    elif action == "otpr":
        from handlers import otpremnica
        await otpremnica.cb_otpremnica(query, payload, context)
    else:
        log.warning("Nepoznat callback action: %s", action)


def _confirm_keyboard(pending_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Točno", callback_data=f"confirm:{pending_id}"),
        InlineKeyboardButton("✏️ Ispravi", callback_data=f"edit:{pending_id}"),
        InlineKeyboardButton("❌ Odbaci", callback_data=f"cancel:{pending_id}"),
    ]])


async def _edit_with_suffix(query, suffix: str, reply_markup=None) -> None:
    """Dodaj sufiks postojećoj poruci. Prvo pokuša Markdown; ako poruka sadrži
    znakove koji ruše legacy Markdown parser, padne na običan tekst."""
    try:
        await query.edit_message_text(
            f"{query.message.text_markdown}\n\n{suffix}",
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )
    except Exception:
        plain = suffix.replace("*", "").replace("_", "")
        await query.edit_message_text(
            f"{query.message.text}\n\n{plain}",
            reply_markup=reply_markup,
        )


async def _set_projekt(query, projekt_key: str) -> None:
    projekt = repo.get_projekt(projekt_key)
    if not projekt:
        await query.edit_message_text(f"Projekt '{projekt_key}' ne postoji više.")
        return
    sessions.set_active_projekt(query.from_user.id, projekt_key)
    await query.edit_message_text(
        f"✅ Aktivan projekt: *{projekt['naziv']}*\n\n"
        f"Sad mi šalji glasovne ili tekstualne poruke o tome što si radio "
        f"i koji materijal si koristio.",
        parse_mode="Markdown",
    )

    # pokaži otvorene zadatke za taj projekt (samo postgres backend)
    try:
        from handlers import zadaci as zadaci_ui
        await zadaci_ui.posalji_zadatke(
            query.get_bot(), query.from_user.id, projekt_key,
            query.from_user.id, projekt["naziv"],
        )
    except Exception:
        log.exception("Greška slanja zadataka nakon odabira projekta")


def _write_izvjestaj(payload: dict[str, Any]) -> None:
    """Sinkroni upis cijelog izvještaja (zove se preko asyncio.to_thread)."""
    parsed = payload["parsed"]

    materijali = []
    for m in parsed.get("materijali", []):
        try:
            kolicina = float(m.get("kolicina") or 0)
        except (TypeError, ValueError):
            kolicina = 0.0
        materijali.append({
            "radnik": payload["radnik_ime"],
            "telegram_id": payload["telegram_id"],
            "sifra": str(m.get("sifra_stavke") or ""),
            "opis": str(m.get("opis") or ""),
            "kolicina": kolicina,
            "jm": str(m.get("jm") or ""),
            "lokacija": parsed.get("lokacija", ""),
            # krug po stavci, pa opći krug izvještaja kao fallback
            "strujni_krug": str(m.get("strujni_krug") or parsed.get("strujni_krug") or ""),
        })

    # potreba za materijalom ide u 'problemi' kolonu (sekcija Posebne napomene
    # u dnevniku) s prefiksom — bez promjene sheme
    problemi = list(parsed.get("problemi") or [])
    for t in parsed.get("potreban_materijal") or []:
        problemi.append(f"Potreban materijal: {t}")

    repo.append_izvjestaj(
        payload["projekt_key"],
        dnevnik={
            "radnik": payload["radnik_ime"],
            "telegram_id": payload["telegram_id"],
            "opis": parsed.get("opis_rada", ""),
            "lokacija": parsed.get("lokacija", ""),
            "strujni_krug": parsed.get("strujni_krug", "") or "",
            "sirova": payload["sirova_poruka"],
            "msg_id": payload["msg_id"],
            "datum_rada": parsed.get("datum_rada", "") or "",
            "vrijeme_rada": parsed.get("vrijeme_rada", "") or "",
            "sati": parsed.get("sati"),
            "radnici_spomenuti": parsed.get("radnici_spomenuti") or [],
            "problemi": problemi,
            "confidence": parsed.get("confidence", ""),
        },
        materijali=materijali,
    )


async def _obavijesti_voditelja(context, payload: dict[str, Any]) -> None:
    """Nakon potvrde: ako izvještaj sadrži probleme ili potrebu za materijalom,
    odmah javi voditelju (ne čeka generiranje dnevnika)."""
    parsed = payload["parsed"]
    problemi = parsed.get("problemi") or []
    potreban = parsed.get("potreban_materijal") or []
    if not problemi and not potreban:
        return
    if payload["telegram_id"] == ADMIN_TELEGRAM_ID:
        return  # voditelj je sam sebi javio

    lines = [f"📣 {payload['radnik_ime']} ({payload['projekt_key']}):"]
    for p in problemi:
        lines.append(f"⚠️ Problem: {p}")
    for t in potreban:
        lines.append(f"🚚 Treba materijal: {t}")
    try:
        await context.bot.send_message(ADMIN_TELEGRAM_ID, "\n".join(lines))
    except Exception:
        log.warning("Ne mogu poslati obavijest voditelju")


async def _confirm(query, pending_id: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    # pop PRIJE upisa sprječava dupli upis kod dva brza klika;
    # kod greške upisa pending se vraća (restore) pa korisnik može ponoviti
    payload = sessions.pop_pending(pending_id)
    if not payload:
        # razlikuj nedavno potvrđen (double-click) od stvarno isteklog
        if sessions.was_recently_consumed(pending_id):
            try:
                await query.answer("✅ Već je zapisano, sve OK!", show_alert=False)
            except Exception:
                pass
            return
        await query.edit_message_text(
            "⌛ Ova potvrda je istekla. Pošalji poruku ponovno."
        )
        return

    try:
        await asyncio.to_thread(_write_izvjestaj, payload)
    except Exception as e:
        log.exception("Greška upisa izvještaja")
        sessions.restore_pending(pending_id, payload)
        await _edit_with_suffix(
            query,
            f"❌ Greška upisa: {e}\nPritisni ✅ Točno za novi pokušaj.",
            reply_markup=_confirm_keyboard(pending_id),
        )
        return

    # ugrađeni materijal skini sa zaduženja gradilišta (best-effort —
    # izvještaj je već zapisan, greška skladišta ga ne smije poništiti)
    skinuto = 0
    materijali_parsed = payload["parsed"].get("materijali") or []
    try:
        from services import skladiste as skl
        if skl.ENABLED and materijali_parsed:
            skinuto, _ = await asyncio.to_thread(
                skl.potrosnja_iz_izvjestaja,
                payload["projekt_key"],
                materijali_parsed,
                payload["telegram_id"],
            )
    except Exception:
        log.exception("Greška skidanja potrošnje sa zaduženja")

    suffix = "✅ *Zapisano*"
    if skinuto:
        suffix += f"\n📦 Skinuto sa zaduženja gradilišta: {skinuto} stavki."
    await _edit_with_suffix(query, suffix)
    await _obavijesti_voditelja(context, payload)


async def _edit(query, pending_id: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    payload = sessions.get_pending(pending_id)
    if not payload:
        await query.edit_message_text(
            "⌛ Ova potvrda je istekla. Pošalji poruku ponovno."
        )
        return
    sessions.set_editing(query.from_user.id, pending_id)
    await _edit_with_suffix(
        query,
        "✏️ Pošalji ispravljenu verziju kao novu poruku "
        "(npr. „bilo je 35m, ne 40\").",
    )


async def _cancel(query, pending_id: str) -> None:
    sessions.pop_pending(pending_id)
    await _edit_with_suffix(query, "❌ *Odbačeno - nije zapisano.*")
