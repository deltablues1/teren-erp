"""Slanje Telegram poruka IZVAN bot procesa (npr. iz web panela).

Web panel i bot su odvojeni procesi — panel ne može koristiti botov
Application objekt, ali može zvati Telegram HTTP API istim tokenom.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from config import TELEGRAM_BOT_TOKEN

log = logging.getLogger(__name__)

_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def send_message(
    chat_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> int | None:
    """Pošalji poruku; vrati message_id ili None ako slanje nije uspjelo.
    Namjerno ne baca iznimku — push je best-effort (panel mora raditi i
    kad je Telegram nedostupan ili radnik blokirao bota)."""
    payload: dict[str, Any] = {"chat_id": int(chat_id), "text": text}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        r = httpx.post(f"{_API}/sendMessage", data=payload, timeout=15.0)
        data = r.json()
        if not data.get("ok"):
            log.warning("Telegram sendMessage nije uspio za %s: %s", chat_id, data)
            return None
        return data["result"]["message_id"]
    except Exception as e:
        log.warning("Telegram push greška za %s: %s", chat_id, e)
        return None
