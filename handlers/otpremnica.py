"""Otpremnica sa slike: vision je već izvukao stavke (report.handle_photo),
ovdje je potvrda — radnik bira kamo zaprimiti robu, upis ide u skladište.

Tok: slika → procitaj_sliku (tip=otpremnica) → preview sa stavkama +
gumbi destinacije → svaka stavka postaje 'primka' transakcija u ledgeru.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import ADMIN_TELEGRAM_ID
from services import repository as repo, sessions, skladiste as skl

log = logging.getLogger(__name__)


MAX_GRADILISTA_GUMBA = 8


def _keyboard(pending_id: str, projekti: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    """Destinacije: skladište, SVA aktivna gradilišta (aktivni projekt prvi),
    radnik. Gradilišta se referenciraju indeksom (g0, g1...) jer callback_data
    ima limit od 64 bajta."""
    rows = [[InlineKeyboardButton("🏬 U skladište", callback_data=f"otpr:sklad:{pending_id}")]]
    for i, p in enumerate(projekti[:MAX_GRADILISTA_GUMBA]):
        rows.append([InlineKeyboardButton(
            f"🏗️ {p['naziv']}", callback_data=f"otpr:g{i}:{pending_id}",
        )])
    rows.append([InlineKeyboardButton("👷 Zaduži na mene", callback_data=f"otpr:radnik:{pending_id}")])
    rows.append([InlineKeyboardButton("❌ Odbaci", callback_data=f"otpr:cancel:{pending_id}")])
    return InlineKeyboardMarkup(rows)


def _format_preview(data: dict[str, Any]) -> str:
    lines = ["📄 OTPREMNICA"]
    if data.get("dobavljac"):
        lines.append(f"Dobavljač: {data['dobavljac']}")
    if data.get("broj_dokumenta"):
        lines.append(f"Dokument: {data['broj_dokumenta']}")
    if data.get("datum"):
        lines.append(f"Datum: {data['datum']}")
    lines.append("")
    lines.append("Stavke:")
    stavke = data.get("stavke") or []
    for st in stavke[:30]:
        kol = st.get("kolicina")
        kol_txt = f"{kol:g}" if isinstance(kol, (int, float)) else "?"
        lines.append(f"  • {st.get('opis', '')}: {kol_txt} {st.get('jm', '')}".rstrip())
    if len(stavke) > 30:
        lines.append(f"  … i još {len(stavke) - 30} stavki")
    lines.append("")
    lines.append("Kamo zaprimiti robu?")
    return "\n".join(lines)


async def zapocni_potvrdu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, rezultat: dict[str, Any],
) -> None:
    """Pokaži pročitanu otpremnicu i pitaj za destinaciju."""
    user = update.effective_user
    if not user or not update.message:
        return

    if not skl.ENABLED:
        await update.message.reply_text(
            "Pročitao sam otpremnicu, ali skladište nije aktivno na ovom "
            "backendu — javi voditelju."
        )
        return

    # SVA aktivna gradilišta kao opcije, aktivni projekt radnika prvi
    projekt_key = sessions.get_active_projekt(user.id)
    projekti = await asyncio.to_thread(repo.list_projekti)
    projekti.sort(key=lambda p: p["key"] != projekt_key)

    radnik = None
    if projekt_key:
        radnik = await asyncio.to_thread(repo.get_radnik, projekt_key, user.id)
    radnik_ime = (radnik or {}).get("Ime") or user.full_name or str(user.id)

    pending_id = sessions.save_pending(user.id, {
        "vrsta": "otpremnica",
        "telegram_id": user.id,
        "radnik_ime": radnik_ime,
        "projekti": [{"key": p["key"], "naziv": p["naziv"]} for p in projekti],
        "dobavljac": rezultat.get("dobavljac", ""),
        "broj_dokumenta": rezultat.get("broj_dokumenta", ""),
        "datum": rezultat.get("datum", ""),
        "stavke": rezultat.get("stavke") or [],
    })

    await update.message.reply_text(
        _format_preview(rezultat),
        reply_markup=_keyboard(pending_id, projekti),
    )


def _zaprimi(payload: dict[str, Any], na: tuple[str, str]) -> tuple[int, list[str]]:
    """Upiši sve stavke kao primke. Vraća (upisano, preskočene_bez_količine)."""
    dokument = payload.get("broj_dokumenta", "")
    dobavljac = payload.get("dobavljac", "")
    upisano = 0
    preskoceno: list[str] = []
    for st in payload.get("stavke") or []:
        opis = str(st.get("opis") or "").strip()
        if not opis:
            continue
        try:
            kol = float(st.get("kolicina"))
        except (TypeError, ValueError):
            kol = 0.0
        if kol <= 0:
            preskoceno.append(opis)
            continue
        skl.primka(
            opis, kol,
            dobavljac=dobavljac,
            na=na,
            jm=str(st.get("jm") or ""),
            dokument=dokument,
            napomena="otpremnica preko bota",
            created_by=payload.get("telegram_id", 0),
        )
        upisano += 1
    return upisano, preskoceno


async def cb_otpremnica(query, payload: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback 'otpr:{dest}:{pending_id}' — dest: sklad|gradil|radnik|cancel."""
    dest, _, pending_id = payload.partition(":")

    if dest == "cancel":
        sessions.pop_pending(pending_id)
        await query.edit_message_text(
            f"{query.message.text}\n\n❌ Odbačeno - ništa nije zaprimljeno."
        )
        return

    pend = sessions.pop_pending(pending_id)
    if not pend or pend.get("vrsta") != "otpremnica":
        if sessions.was_recently_consumed(pending_id):
            try:
                await query.answer("✅ Već je zaprimljeno!", show_alert=False)
            except Exception:
                pass
            return
        await query.edit_message_text(
            "⌛ Ova potvrda je istekla. Pošalji sliku ponovno."
        )
        return

    projekti = pend.get("projekti") or []
    if dest == "sklad":
        na = ("skladiste", "")
        gdje = "skladište"
    elif dest.startswith("g") and dest[1:].isdigit() and int(dest[1:]) < len(projekti):
        p = projekti[int(dest[1:])]
        na = ("gradiliste", p["key"])
        gdje = f"gradilište {p['naziv']}"
    elif dest == "radnik":
        na = ("radnik", str(pend["telegram_id"]))
        gdje = f"radnika {pend['radnik_ime']}"
    else:
        sessions.restore_pending(pending_id, pend)
        await query.answer("Nepoznata destinacija, pokušaj ponovno.", show_alert=True)
        return

    try:
        upisano, preskoceno = await asyncio.to_thread(_zaprimi, pend, na)
    except Exception as e:
        log.exception("Greška upisa otpremnice")
        sessions.restore_pending(pending_id, pend)
        await query.edit_message_text(
            f"{query.message.text}\n\n❌ Greška upisa: {e}\nPokušaj ponovno.",
            reply_markup=_keyboard(pending_id, pend.get("projekti") or []),
        )
        return

    poruka = f"✅ Zaprimljeno {upisano} stavki na {gdje}."
    if preskoceno:
        poruka += (
            f"\n⚠️ Bez količine (dodaj ručno u panelu): {', '.join(preskoceno[:5])}"
        )
    await query.edit_message_text(f"{query.message.text}\n\n{poruka}")

    # javi voditelju
    if pend["telegram_id"] != ADMIN_TELEGRAM_ID:
        try:
            await context.bot.send_message(
                ADMIN_TELEGRAM_ID,
                f"📄 {pend['radnik_ime']} zaprimio otpremnicu "
                f"{pend.get('broj_dokumenta') or '(bez broja)'} "
                f"od {pend.get('dobavljac') or '(nepoznat dobavljač)'} — "
                f"{upisano} stavki na {gdje}. Detalji: panel → Skladište.",
            )
        except Exception:
            log.warning("Ne mogu javiti voditelju za otpremnicu")
