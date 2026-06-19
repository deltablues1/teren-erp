"""Handlere za poruke radnika - tekst i glas."""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from config import ADMIN_TELEGRAM_ID, is_admin
from services import (
    claude_parser, katalog, repository as repo, sessions, transcription,
    zadaci as zadaci_srv,
)

log = logging.getLogger(__name__)

# Whisper API odbija datoteke veće od 25 MB
MAX_VOICE_BYTES = 24 * 1024 * 1024


def _esc(value: object) -> str:
    """Escapaj korisnički/AI tekst za legacy Markdown (inače _ * [ ruše poruku)."""
    return escape_markdown(str(value), version=1)


def _format_preview(parsed: claude_parser.ParsedReport, prijepis: str) -> str:
    lines = [f"📝 _„{_esc(prijepis)}\"_", ""]
    lines.append(f"*Rad:* {_esc(parsed.opis_rada) or '—'}")
    if parsed.lokacija:
        lines.append(f"*Lokacija:* {_esc(parsed.lokacija)}")
    if parsed.datum_rada:
        lines.append(f"*Datum:* {_esc(parsed.datum_rada)}")
    if parsed.vrijeme_rada or parsed.sati is not None:
        time_parts = []
        if parsed.vrijeme_rada:
            time_parts.append(_esc(parsed.vrijeme_rada))
        if parsed.sati is not None:
            time_parts.append(f"{parsed.sati}h")
        lines.append(f"*Sati:* {' · '.join(time_parts)}")
    if parsed.radnici_spomenuti:
        lines.append(f"*S njim radili:* {_esc(', '.join(parsed.radnici_spomenuti))}")
    if parsed.materijali:
        lines.append("*Materijali:*")
        for m in parsed.materijali:
            stavka = (
                f"  • {_esc(m.get('opis', ''))}: "
                f"{_esc(m.get('kolicina', ''))} {_esc(m.get('jm', ''))}"
            )
            if m.get("katalog_naziv"):
                stavka += f" → 📦 {_esc(m['katalog_naziv'])}"
            elif m.get("sifra_stavke"):
                stavka += f" (šifra: {_esc(m['sifra_stavke'])})"
            elif m.get("treba_u_katalog"):
                stavka += " (⚠️ nije u katalogu)"
            lines.append(stavka)
    if parsed.problemi:
        lines.append("*Problemi/napomene:*")
        for p in parsed.problemi:
            lines.append(f"  • {_esc(p)}")
    if parsed.potreban_materijal:
        lines.append("*🚚 Treba materijal (javljam voditelju):*")
        for t in parsed.potreban_materijal:
            lines.append(f"  • {_esc(t)}")
    if parsed.nedostaje:
        lines.append(f"*Nedostaje:* {_esc(', '.join(parsed.nedostaje))}")
    if parsed.has_low_confidence():
        lines.append("")
        lines.append(f"⚠️ {_esc(parsed.pojasnjenje_potrebno) or 'Provjeri podatke!'}")
        lines.append("_(odgovori na ovu poruku i dopunit ću izvještaj)_")
    if not parsed.problemi and not parsed.potreban_materijal:
        lines.append("")
        lines.append(
            "ℹ️ _Ima li problema ili ti treba materijal? "
            "Odgovori na ovu poruku (reply) pa dodam._"
        )
    return "\n".join(lines)


def _confirm_keyboard(pending_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Točno", callback_data=f"confirm:{pending_id}"),
        InlineKeyboardButton("✏️ Ispravi", callback_data=f"edit:{pending_id}"),
        InlineKeyboardButton("❌ Odbaci", callback_data=f"cancel:{pending_id}"),
    ]])


async def _process_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    tekst: str, prijepis_za_prikaz: str | None = None,
) -> None:
    user = update.effective_user
    if not user or not update.message:
        return

    if not is_admin(user.id) and not await asyncio.to_thread(repo.is_known_worker, user.id):
        await update.message.reply_text(
            "Nisi na listi radnika. Pošalji /id voditelju da te doda u tim."
        )
        return

    projekt_key = sessions.get_active_projekt(user.id)
    if not projekt_key:
        await update.message.reply_text(
            "Prvo odaberi projekt sa /start."
        )
        return

    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        troskovnik = await asyncio.to_thread(repo.get_troskovnik, projekt_key)
    except Exception as e:
        log.exception("Greška dohvaćanja troškovnika")
        await update.message.reply_text(
            f"Ne mogu dohvatiti troškovnik: {e}"
        )
        return

    # ako je korisnik kliknuo "Ispravi", stari unos postaje kontekst novom parsu
    prethodni_kontekst = sessions.pop_editing_context(user.id) or ""

    try:
        parsed = await asyncio.to_thread(
            claude_parser.parse_report,
            tekst,
            troskovnik=troskovnik,
            prethodni_kontekst=prethodni_kontekst,
        )
    except Exception as e:
        log.exception("Greška Claude parsinga")
        await update.message.reply_text(
            f"Greška kod razumijevanja poruke: {e}\n"
            "Pokušaj ponovno ili pošalji drugačiju formulaciju."
        )
        return

    # Ključ-u-ruke projekt (bez troškovnika) → poveži materijale s katalogom (cjenici)
    if not troskovnik and parsed.materijali:
        await update.message.chat.send_action(ChatAction.TYPING)
        try:
            await asyncio.to_thread(
                katalog.poveži_materijale_s_katalogom, parsed.materijali
            )
        except Exception:
            log.exception("Greška spajanja materijala s katalogom")

    radnik = await asyncio.to_thread(repo.get_radnik, projekt_key, user.id)
    radnik_ime = radnik.get("Ime") if radnik else (user.full_name or "Nepoznato")

    pending_id = sessions.save_pending(user.id, {
        "projekt_key": projekt_key,
        "telegram_id": user.id,
        "radnik_ime": radnik_ime,
        "sirova_poruka": tekst,
        "msg_id": update.message.message_id,
        "parsed": parsed.to_dict(),
    })

    prijepis = prijepis_za_prikaz or tekst
    preview_msg = await update.message.reply_text(
        _format_preview(parsed, prijepis),
        parse_mode="Markdown",
        reply_markup=_confirm_keyboard(pending_id),
    )
    # reply na preview poruku = ispravak/dopuna (bez klikanja na ✏️)
    sessions.link_preview(pending_id, update.message.chat_id, preview_msg.message_id)


async def _handle_zadatak_reply(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    """Ako je poruka reply na poruku zadatka → spremi kao komentar zadatka
    (umjesto parsiranja kao izvještaj). Vraća True ako je obrađeno."""
    msg = update.message
    user = update.effective_user
    if not msg or not user or not msg.reply_to_message:
        return False

    z = await asyncio.to_thread(
        zadaci_srv.zadatak_za_reply, msg.chat_id, msg.reply_to_message.message_id
    )
    if not z:
        return False

    ime = await asyncio.to_thread(zadaci_srv.ime_radnika, user.id)
    ime = ime or user.full_name or str(user.id)
    await asyncio.to_thread(
        zadaci_srv.dodaj_komentar, z["id"], user.id, ime, msg.text
    )
    await msg.reply_text("💬 Zabilježeno uz zadatak — voditelj će vidjeti.")

    if user.id != ADMIN_TELEGRAM_ID:
        try:
            await context.bot.send_message(
                ADMIN_TELEGRAM_ID,
                f"💬 {ime} odgovara na zadatak ({z['projekt_key']}):\n"
                f"»{msg.text}«\n\nZadatak: {z['tekst']}",
            )
        except Exception:
            log.warning("Ne mogu javiti adminu komentar zadatka %s", z["id"])
    return True


def _check_preview_reply(update: Update) -> None:
    """Ako je poruka reply na botov preview → tretiraj kao ✏️ Ispravi:
    stari pending postaje kontekst novom parsiranju."""
    msg = update.message
    user = update.effective_user
    if not msg or not user or not msg.reply_to_message:
        return
    pid = sessions.pending_for_preview(msg.chat_id, msg.reply_to_message.message_id)
    if pid:
        sessions.set_editing(user.id, pid)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    if await _handle_zadatak_reply(update, context):
        return
    _check_preview_reply(update)
    await _process_text(update, context, update.message.text)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Slika rukom pisanog opisa rada → vision transkripcija → isti tok kao tekst.
    Caption uz sliku se koristi kao radnikov opis što je na papiru."""
    user = update.effective_user
    if not user or not update.message or not update.message.photo:
        return

    caption = (update.message.caption or "").strip()
    status_msg = await update.message.reply_text("🖼️ Primio sliku, čitam što piše…")
    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        # photo[-1] = najveća rezolucija; Telegram fotke su uvijek JPEG
        file = await update.message.photo[-1].get_file()
        image_bytes = bytes(await file.download_as_bytearray())
        rezultat = await asyncio.to_thread(
            claude_parser.procitaj_sliku, image_bytes, "image/jpeg", caption
        )
    except Exception as e:
        log.exception("Greška čitanja slike")
        await update.message.reply_text(f"Ne mogu pročitati sliku: {e}")
        return
    finally:
        try:
            await status_msg.delete()
        except Exception:
            pass

    # otpremnica → poseban tok (zaprimanje u skladište)
    if rezultat["tip"] == "otpremnica" and rezultat["stavke"]:
        from handlers import otpremnica
        await otpremnica.zapocni_potvrdu(update, context, rezultat)
        return

    prijepis = rezultat["prijepis"]
    if not prijepis.strip():
        await update.message.reply_text(
            "Nisam uspio pročitati tekst sa slike. Probaj oštriju/bližu fotku, "
            "ili mi pošalji glasovnu/tekst."
        )
        return

    # caption je radnikov kontekst — ide u parse zajedno s prijepisom
    tekst = f"{caption}\n\n(Prijepis s papira:)\n{prijepis}" if caption else prijepis
    await _process_text(update, context, tekst, prijepis_za_prikaz=prijepis)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.message:
        return

    voice = update.message.voice or update.message.audio
    if not voice:
        return

    if voice.file_size and voice.file_size > MAX_VOICE_BYTES:
        await update.message.reply_text(
            "⚠️ Glasovna je preduga/prevelika za transkripciju (max ~25 MB). "
            "Podijeli je na više kraćih poruka."
        )
        return

    status_msg = await update.message.reply_text("🎤 Primio glasovnu, transkribiram…")
    await update.message.chat.send_action(ChatAction.TYPING)

    file = await voice.get_file()
    with tempfile.NamedTemporaryFile(
        suffix=".ogg", delete=False, prefix="tg_voice_"
    ) as tmp:
        tmp_path = Path(tmp.name)
    try:
        await file.download_to_drive(custom_path=tmp_path)
        try:
            prijepis = await asyncio.to_thread(transcription.transcribe, tmp_path)
        except Exception as e:
            log.exception("Greška Whisper transkripcije")
            await update.message.reply_text(
                f"Ne mogu transkribirati glasovnu: {e}"
            )
            return
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

    try:
        await status_msg.delete()
    except Exception:
        pass

    if not prijepis.strip():
        await update.message.reply_text(
            "Nisam ništa razumio iz glasovne. Pokušaj ponovno."
        )
        return

    # i glasovni odgovor na preview poruku je dopuna izvještaja
    _check_preview_reply(update)
    await _process_text(update, context, prijepis, prijepis_za_prikaz=prijepis)
