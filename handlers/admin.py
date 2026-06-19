"""Komande dostupne samo voditelju (admin)."""
from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters

from config import is_admin
from services import docgen, repository as repo, sessions, troskovnik_import, weather

log = logging.getLogger(__name__)

# Conversation states
NP_NAZIV, NP_ADRESA, NP_INVESTITOR, NP_IZVODAC, NP_NADZORNI, NP_DOZVOLA, NP_TROSKOVNIK = range(7)
DR_TID, DR_IME, DR_KVAL = range(7, 10)
UT_FILE = 10


def _admin_only(update: Update) -> bool:
    return bool(update.effective_user and is_admin(update.effective_user.id))


def _otvori_link(projekt: dict | None) -> str:
    """Markdown red s linkom na Sheets ako projekt ima spreadsheet; inače napomena.
    (Postgres projekti nemaju Google Sheet — podaci su u bazi.)"""
    url = (projekt or {}).get("spreadsheet_url", "")
    if url:
        return f"🔗 [Otvori u Google Sheets]({url})"
    return "📂 Podaci se vode u bazi."


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[čć]", "c", text)
    text = re.sub(r"[š]", "s", text)
    text = re.sub(r"[ž]", "z", text)
    text = re.sub(r"[đ]", "d", text)
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


# ----------------------------- /noviprojekt -----------------------------

async def np_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _admin_only(update):
        await update.message.reply_text("Samo admin može kreirati projekte.")
        return ConversationHandler.END
    context.user_data["np"] = {}
    await update.message.reply_text(
        "Kreiramo novi projekt.\n\nNaziv projekta? (npr. „Vinkovci 5\")"
    )
    return NP_NAZIV


async def np_naziv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["np"]["naziv"] = update.message.text.strip()
    await update.message.reply_text("Adresa gradilišta?")
    return NP_ADRESA


async def np_adresa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["np"]["adresa"] = update.message.text.strip()
    await update.message.reply_text("Investitor?")
    return NP_INVESTITOR


async def np_investitor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["np"]["investitor"] = update.message.text.strip()
    await update.message.reply_text("Izvođač? (tvoja firma)")
    return NP_IZVODAC


async def np_izvodac(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["np"]["izvodac"] = update.message.text.strip()
    await update.message.reply_text("Nadzorni inženjer?")
    return NP_NADZORNI


async def np_nadzorni(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["np"]["nadzorni"] = update.message.text.strip()
    await update.message.reply_text("Broj građevinske dozvole? (ili - ako nemaš)")
    return NP_DOZVOLA


async def np_dozvola(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["np"]["broj_dozvole"] = update.message.text.strip()
    await update.message.reply_text(
        "Pošalji Excel troškovnik (.xls ili .xlsx) kao prilog. "
        "AI će sam pročitati stavke, šifre, JM i količine — ne moraš ništa formatirati.\n\n"
        "Ako želiš preskočiti troškovnik za sada, pošalji /preskoci."
    )
    return NP_TROSKOVNIK


async def _np_create_projekt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, with_troskovnik: bool,
) -> int:
    np = context.user_data.get("np", {})
    naziv = np.get("naziv", "Projekt")
    key = _slugify(naziv)

    try:
        projekt = repo.create_projekt(
            key=key,
            naziv=naziv,
            adresa=np.get("adresa", ""),
            investitor=np.get("investitor", ""),
            izvodac=np.get("izvodac", ""),
            nadzorni=np.get("nadzorni", ""),
            broj_dozvole=np.get("broj_dozvole", ""),
        )
    except Exception as e:
        log.exception("Greška kreiranja projekta")
        await update.message.reply_text(f"Greška: {e}")
        context.user_data.pop("np", None)
        return ConversationHandler.END

    if with_troskovnik and update.message.document:
        file = await update.message.document.get_file()
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            await file.download_to_drive(custom_path=tmp_path)
            n = await asyncio.to_thread(_import_xlsx, key, tmp_path)
            await update.message.reply_text(
                f"✅ Projekt *{naziv}* kreiran!\n"
                f"📊 Troškovnik: {n} stavki učitano.\n"
                f"{_otvori_link(projekt)}",
                parse_mode="Markdown",
            )
        finally:
            tmp_path.unlink(missing_ok=True)
    else:
        await update.message.reply_text(
            f"✅ Projekt *{naziv}* kreiran (ključ u ruke, bez troškovnika).\n"
            f"{_otvori_link(projekt)}\n\n"
            f"Materijal koji radnici jave prepoznaje se iz kataloga (cjenika). "
            f"Po želji kasnije dodaj troškovnik sa /uvezi_troskovnik.",
            parse_mode="Markdown",
        )
    context.user_data.pop("np", None)
    return ConversationHandler.END


async def np_troskovnik(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _np_create_projekt(update, context, with_troskovnik=True)


async def np_preskoci(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Preskoči troškovnik korak."""
    return await _np_create_projekt(update, context, with_troskovnik=False)


async def np_otkazi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("np", None)
    await update.message.reply_text("Otkazano.")
    return ConversationHandler.END


# ----------------------------- /dodaj_radnika ----------------------------

async def dr_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _admin_only(update):
        await update.message.reply_text("Samo admin može dodavati radnike.")
        return ConversationHandler.END

    projekti = repo.list_projekti()
    if not projekti:
        await update.message.reply_text("Prvo kreiraj projekt sa /noviprojekt.")
        return ConversationHandler.END

    context.user_data["dr"] = {"projekti": [p["key"] for p in projekti]}
    await update.message.reply_text(
        "Neka radnik tebi pošalje /id u privatnu konverzaciju s botom.\n\n"
        "Unesi njegov Telegram ID (samo broj):"
    )
    return DR_TID


async def dr_tid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        tid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("ID mora biti broj. Pokušaj ponovno.")
        return DR_TID
    context.user_data["dr"]["tid"] = tid
    await update.message.reply_text("Ime i prezime radnika?")
    return DR_IME


async def dr_ime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["dr"]["ime"] = update.message.text.strip()
    await update.message.reply_text(
        "Kvalifikacija? (npr. „VKV električar\", „KV električar\", „Pomoćni radnik\")"
    )
    return DR_KVAL


def _dodaj_radnika_u_projekte(
    projekti: list[str], tid: int, ime: str, kval: str,
) -> list[str]:
    """Sinkroni upis radnika u sve projekte (zove se preko to_thread)."""
    dodano_u: list[str] = []
    for projekt_key in projekti:
        try:
            repo.upsert_radnik(projekt_key, tid, ime, kval)
        except Exception as e:
            log.warning("Ne mogu dodati radnika u %s: %s", projekt_key, e)
            continue
        p = repo.get_projekt(projekt_key)
        dodano_u.append(p["naziv"] if p else projekt_key)
    return dodano_u


async def dr_kval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    dr = context.user_data["dr"]
    kval = update.message.text.strip()

    dodano_u = await asyncio.to_thread(
        _dodaj_radnika_u_projekte, dr["projekti"], dr["tid"], dr["ime"], kval,
    )

    await update.message.reply_text(
        f"✅ {dr['ime']} (ID {dr['tid']}) dodan kao *{kval}* "
        f"u projekte: {', '.join(dodano_u)}",
        parse_mode="Markdown",
    )
    context.user_data.pop("dr", None)
    return ConversationHandler.END


# ----------------------------- /projekti --------------------------------

async def cmd_projekti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_only(update):
        return
    projekti = repo.list_projekti()
    if not projekti:
        await update.message.reply_text("Nema aktivnih projekata.")
        return
    lines = ["*Aktivni projekti:*"]
    for p in projekti:
        url = p.get("spreadsheet_url", "")
        link = f"  [Sheets]({url})" if url else "  _(podaci u bazi)_"
        lines.append(f"\n• *{p['naziv']}* (`{p['key']}`)\n{link}")
    await update.message.reply_text(
        "\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True,
    )


# ----------------------------- /dnevnik --------------------------------

def _grad_iz_adrese(adresa: str) -> str:
    """'Ilica 5, 10000 Zagreb' → 'Zagreb'. Grad je ZADNJI dio adrese (ne prvi —
    prvi je ulica), bez poštanskog broja."""
    dio = adresa.split(",")[-1].strip()
    return re.sub(r"^\d{4,5}\s*", "", dio).strip()


def _zabiljezi_vrijeme(projekt_key: str, datum: str) -> None:
    """Dohvati i upiši vremenske prilike za projekt (sinkrono, za to_thread)."""
    projekt = repo.get_projekt(projekt_key)
    if not (projekt and weather.is_available() and projekt.get("adresa")):
        return
    grad = _grad_iz_adrese(projekt["adresa"])
    if not grad:
        return
    w = weather.get_current_weather(grad)
    if w:
        repo.append_weather(
            projekt_key,
            datum=datum,
            min_temp=w["min_temp"],
            max_temp=w["max_temp"],
            oborine=w["oborine_mm"],
            opis=w["opis"],
        )


async def cmd_dnevnik(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_only(update):
        return

    args = context.args or []
    datum = args[0] if args else datetime.now().strftime("%Y-%m-%d")
    projekt_key = args[1] if len(args) > 1 else None

    if not projekt_key:
        aktivni = sessions.get_active_projekt(update.effective_user.id)
        if aktivni:
            projekt_key = aktivni
        else:
            projekti = repo.list_projekti()
            if not projekti:
                await update.message.reply_text(
                    "Nema aktivnih projekata. Kreiraj prvi sa /noviprojekt."
                )
                return
            if len(projekti) == 1:
                projekt_key = projekti[0]["key"]
            else:
                await update.message.reply_text(
                    "Koristi: /dnevnik YYYY-MM-DD projekt_key\n"
                    f"Aktivni projekti: {', '.join(p['key'] for p in projekti)}"
                )
                return

    await update.message.reply_text(
        f"⏳ Generiram dnevnik za {datum} ({projekt_key})..."
    )

    try:
        await asyncio.to_thread(_zabiljezi_vrijeme, projekt_key, datum)
        docx_path = await asyncio.to_thread(docgen.generate_dnevnik, projekt_key, datum)
    except Exception as e:
        log.exception("Greška generiranja dnevnika")
        await update.message.reply_text(f"❌ Greška: {e}")
        return

    with docx_path.open("rb") as f:
        await update.message.reply_document(
            document=f, filename=docx_path.name,
            caption=f"📄 Građevinski dnevnik {datum}",
        )

    pdf_path = await asyncio.to_thread(docgen.to_pdf, docx_path)
    if pdf_path and pdf_path.exists():
        with pdf_path.open("rb") as f:
            await update.message.reply_document(
                document=f, filename=pdf_path.name,
            )
    else:
        await update.message.reply_text(
            "ℹ️ PDF nije generiran (treba MS Word ili LibreOffice). "
            ".docx je dovoljan."
        )


# ----------------------------- /knjiga ---------------------------------

async def cmd_knjiga(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generira građevinsku knjigu / obračunsku situaciju.

    Formati:
      /knjiga                       - kumulativ za jedini projekt
      /knjiga projekt_key           - kumulativ za projekt
      /knjiga 1 2026-05             - 1. SITUACIJA za svibanj 2026 (jedini projekt)
      /knjiga 1 2026-05 projekt_key - 1. SITUACIJA za konkretan projekt
    """
    if not _admin_only(update):
        return

    args = list(context.args or [])
    situacija_broj: int | None = None
    mjesec: str | None = None
    projekt_key: str | None = None

    # parsiraj: ako prvi arg broj → situacija, sljedeći YYYY-MM → mjesec
    if args and args[0].isdigit():
        situacija_broj = int(args.pop(0))
    if args and re.match(r"^\d{4}-\d{2}$", args[0]):
        mjesec = args.pop(0)
    if args:
        projekt_key = args[0]

    if not projekt_key:
        projekti = repo.list_projekti()
        if not projekti:
            await update.message.reply_text(
                "Nema aktivnih projekata. Kreiraj prvi sa /noviprojekt."
            )
            return
        if len(projekti) == 1:
            projekt_key = projekti[0]["key"]
        else:
            await update.message.reply_text(
                "Koristi: /knjiga [br_situacije] [YYYY-MM] [projekt_key]\n"
                f"Aktivni projekti: {', '.join(p['key'] for p in projekti)}\n\n"
                "Primjeri:\n"
                "  /knjiga                  → kumulativ\n"
                "  /knjiga 1 2026-05        → 1. situacija za svibanj 2026\n"
            )
            return

    sit_text = f"{situacija_broj}. SITUACIJA " if situacija_broj else "kumulativ"
    mj_text = f"({mjesec})" if mjesec else ""
    await update.message.reply_text(
        f"⏳ Generiram knjigu — {sit_text}{mj_text} za {projekt_key}..."
    )
    try:
        path = await asyncio.to_thread(
            docgen.generate_knjiga, projekt_key, situacija_broj, mjesec
        )
    except Exception as e:
        log.exception("Greška generiranja knjige")
        await update.message.reply_text(f"❌ Greška: {e}")
        return

    caption = "📘 Građevinska knjiga"
    if situacija_broj:
        caption += f" — {situacija_broj}. situacija"
    if mjesec:
        caption += f" ({mjesec})"

    with path.open("rb") as f:
        await update.message.reply_document(
            document=f, filename=path.name, caption=caption,
        )


# ----------------------------- /uvezi_troskovnik --------------------------

async def ut_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Početak /uvezi_troskovnik — odredi projekt, pita za file kao sljedeću poruku."""
    if not _admin_only(update):
        await update.message.reply_text("Samo admin može uvoziti troškovnike.")
        return ConversationHandler.END

    args = context.args or []
    projekt_key = args[0] if args else None

    if not projekt_key:
        projekti = repo.list_projekti()
        if len(projekti) == 1:
            projekt_key = projekti[0]["key"]
        else:
            keys = ", ".join(p["key"] for p in projekti) or "(nema)"
            await update.message.reply_text(
                f"Koristi: /uvezi_troskovnik <projekt_key>\nAktivni: {keys}"
            )
            return ConversationHandler.END

    if not repo.get_projekt(projekt_key):
        await update.message.reply_text(f"Projekt '{projekt_key}' ne postoji.")
        return ConversationHandler.END

    context.user_data["ut_projekt"] = projekt_key
    await update.message.reply_text(
        f"📎 OK, projekt: *{projekt_key}*\n\n"
        f"Sad mi pošalji .xls ili .xlsx troškovnik kao **sljedeću poruku** "
        f"(samo priloži file, bez ikakvog teksta). AI će ga parsirati.\n\n"
        f"Za odustanak: /otkazi",
        parse_mode="Markdown",
    )
    return UT_FILE


async def ut_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Primi file, parsiraj, upiši u Sheets."""
    projekt_key = context.user_data.get("ut_projekt")
    if not projekt_key or not update.message.document:
        await update.message.reply_text("Nešto je krenulo po krivu, pokušaj /uvezi_troskovnik opet.")
        return ConversationHandler.END

    await update.message.reply_text("⏳ Učitavam i parsiram troškovnik (~60-90s)...")
    file = await update.message.document.get_file()
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        await file.download_to_drive(custom_path=tmp_path)
        n = await asyncio.to_thread(_import_xlsx, projekt_key, tmp_path)
        projekt = repo.get_projekt(projekt_key)
        await update.message.reply_text(
            f"✅ Učitano *{n}* stavki troškovnika u projekt *{projekt_key}*.\n"
            f"{_otvori_link(projekt)}",
            parse_mode="Markdown",
        )
    except Exception as e:
        log.exception("Greška uvoza troškovnika")
        await update.message.reply_text(f"❌ Greška: {e}")
    finally:
        tmp_path.unlink(missing_ok=True)
        context.user_data.pop("ut_projekt", None)
    return ConversationHandler.END


def _import_xlsx(projekt_key: str, xlsx_path: Path) -> int:
    """AI uvoz .xls/.xlsx troškovnika u Sheets."""
    return troskovnik_import.import_to_sheets(projekt_key, xlsx_path)


# ----------------------------- ConversationHandler factory ---------------

def build_noviprojekt_handler() -> ConversationHandler:
    from telegram.ext import CommandHandler
    return ConversationHandler(
        entry_points=[CommandHandler("noviprojekt", np_start)],
        states={
            NP_NAZIV: [MessageHandler(filters.TEXT & ~filters.COMMAND, np_naziv)],
            NP_ADRESA: [MessageHandler(filters.TEXT & ~filters.COMMAND, np_adresa)],
            NP_INVESTITOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, np_investitor)],
            NP_IZVODAC: [MessageHandler(filters.TEXT & ~filters.COMMAND, np_izvodac)],
            NP_NADZORNI: [MessageHandler(filters.TEXT & ~filters.COMMAND, np_nadzorni)],
            NP_DOZVOLA: [MessageHandler(filters.TEXT & ~filters.COMMAND, np_dozvola)],
            NP_TROSKOVNIK: [
                MessageHandler(
                    filters.Document.FileExtension("xlsx")
                    | filters.Document.FileExtension("xls"),
                    np_troskovnik,
                ),
                CommandHandler("preskoci", np_preskoci),
            ],
        },
        fallbacks=[CommandHandler("otkazi", np_otkazi)],
    )


def build_dodaj_radnika_handler() -> ConversationHandler:
    from telegram.ext import CommandHandler
    return ConversationHandler(
        entry_points=[CommandHandler("dodaj_radnika", dr_start)],
        states={
            DR_TID: [MessageHandler(filters.TEXT & ~filters.COMMAND, dr_tid)],
            DR_IME: [MessageHandler(filters.TEXT & ~filters.COMMAND, dr_ime)],
            DR_KVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, dr_kval)],
        },
        fallbacks=[CommandHandler("otkazi", np_otkazi)],
    )


def build_uvezi_troskovnik_handler() -> ConversationHandler:
    from telegram.ext import CommandHandler
    return ConversationHandler(
        entry_points=[CommandHandler("uvezi_troskovnik", ut_start)],
        states={
            UT_FILE: [
                MessageHandler(filters.Document.ALL, ut_file),
            ],
        },
        fallbacks=[CommandHandler("otkazi", np_otkazi)],
    )
