"""Glavni Telegram bot entry point.

Pokreni sa:  python bot.py
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta
from logging.handlers import RotatingFileHandler

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import ROOT_DIR, TELEGRAM_BOT_TOKEN
from handlers import admin, confirm, report, skladiste, start, zadaci
from services.zadaci import SNOOZE_DO_SATI

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=_LOG_FORMAT,
    datefmt="%Y-%m-%d %H:%M:%S",
)
# log i u datoteku (rotira na 2 MB, čuva 3 stare) — za dijagnostiku s terena
_file_handler = RotatingFileHandler(
    ROOT_DIR / "bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
logging.getLogger().addHandler(_file_handler)

logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("teren-bot")


async def _podsjetnik_loop(app: Application) -> None:
    """Dnevni podsjetnik za zadatke. Pri startu pošalje dospjele ODGOĐENE
    (idempotentno — snooze se briše nakon slanja), a zatim svaki dan u
    SNOOZE_DO_SATI i one s rokom danas/prekoračenim."""
    try:
        n = await zadaci.posalji_podsjetnike(app.bot, include_rok=False)
        if n:
            log.info("Podsjetnici pri startu: %d zadataka", n)
    except Exception:
        log.exception("Greška podsjetnika pri startu")

    while True:
        now = datetime.now()
        target = datetime.combine(now.date(), time(hour=SNOOZE_DO_SATI))
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            n = await zadaci.posalji_podsjetnike(app.bot, include_rok=True)
            log.info("Dnevni podsjetnici: %d zadataka", n)
        except Exception:
            log.exception("Greška dnevnih podsjetnika")


async def _post_init(app: Application) -> None:
    app.create_task(_podsjetnik_loop(app))


def build_app() -> Application:
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    # Komande dostupne svima (radnici + admin)
    app.add_handler(CommandHandler("start", start.cmd_start))
    app.add_handler(CommandHandler("id", start.cmd_id))
    app.add_handler(CommandHandler("odjava", start.cmd_odjava))
    app.add_handler(CommandHandler("projekt", start.cmd_projekt))
    app.add_handler(CommandHandler("zadaci", zadaci.cmd_zadaci))
    app.add_handler(CommandHandler("zaduzenja", skladiste.cmd_zaduzenja))
    app.add_handler(CommandHandler("zaliha", skladiste.cmd_zaliha))

    # Admin komande
    app.add_handler(CommandHandler("projekti", admin.cmd_projekti))
    app.add_handler(CommandHandler("dnevnik", admin.cmd_dnevnik))
    app.add_handler(CommandHandler("knjiga", admin.cmd_knjiga))
    # Conversation handlers (multi-step wizards)
    app.add_handler(admin.build_noviprojekt_handler())
    app.add_handler(admin.build_dodaj_radnika_handler())
    app.add_handler(admin.build_uvezi_troskovnik_handler())

    # Callback gumbi (mora ići PRIJE generičnih message handlera)
    app.add_handler(CallbackQueryHandler(confirm.handle_callback))

    # Poruke radnika
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, report.handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, report.handle_photo))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, report.handle_text)
    )

    app.add_error_handler(on_error)

    return app


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Globalni error handler - logira i javlja korisniku umjesto tihog pada."""
    log.exception("Neuhvaćena greška", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Dogodila se greška pri obradi. Pokušaj ponovno ili javi voditelju."
            )
        except Exception:
            pass


def main() -> None:
    app = build_app()
    log.info("Bot je živ. Pritisni Ctrl+C za prekid.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
