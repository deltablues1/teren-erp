"""Pamti koji je radnik 'prijavljen' na koji projekt.

Stanje se persistira u data/sessions.json (cache + restart safety).
Također drži privremene 'pending' parsing rezultate prije potvrde.

Sve javne funkcije rade load→modify→save pod globalnim lockom (handleri se
mogu izvršavati paralelno preko asyncio.to_thread), a upis je atoman
(temp file + os.replace) da pad usred pisanja ne korumpira datoteku.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta
from typing import Any

from config import SESSIONS_FILE

SESSION_TTL_HOURS = 16

CONSUMED_TTL_MINUTES = 10   # koliko dugo pamtimo "već potvrđene" pendinge
PENDING_TTL_HOURS = 48      # nakon toga se nepotvrđeni pendingi čiste

_LOCK = threading.Lock()


_EMPTY = {"active": {}, "pending": {}, "consumed": {}, "editing": {}, "preview": {}}


def _load() -> dict[str, Any]:
    if not SESSIONS_FILE.exists():
        return {k: {} for k in _EMPTY}
    try:
        data = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
        for k in _EMPTY:
            data.setdefault(k, {})
        return data
    except (json.JSONDecodeError, OSError):
        return {k: {} for k in _EMPTY}


def _gc_consumed(consumed: dict[str, str]) -> None:
    """Ukloni 'consumed' zapise starije od CONSUMED_TTL_MINUTES."""
    cutoff = datetime.now() - timedelta(minutes=CONSUMED_TTL_MINUTES)
    expired = [k for k, ts in consumed.items() if _ts(ts) < cutoff]
    for k in expired:
        consumed.pop(k, None)


def _gc_pending(data: dict[str, Any]) -> None:
    """Ukloni nepotvrđene pendinge starije od PENDING_TTL_HOURS
    + preview mapiranja koja pokazuju na nepostojeće pendinge."""
    pending = data["pending"]
    cutoff = datetime.now() - timedelta(hours=PENDING_TTL_HOURS)
    expired = [k for k, p in pending.items() if _ts(p.get("ts", "")) < cutoff]
    for k in expired:
        pending.pop(k, None)
    preview = data.get("preview", {})
    for k in [k for k, pid in preview.items() if pid not in pending]:
        preview.pop(k, None)


def _ts(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return datetime.min


def _save(data: dict[str, Any]) -> None:
    tmp = SESSIONS_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, SESSIONS_FILE)


def set_active_projekt(telegram_id: int, projekt_key: str) -> None:
    with _LOCK:
        data = _load()
        data["active"][str(telegram_id)] = {
            "projekt": projekt_key,
            "ts": datetime.now().isoformat(),
        }
        _save(data)


def get_active_projekt(telegram_id: int) -> str | None:
    with _LOCK:
        data = _load()
    entry = data["active"].get(str(telegram_id))
    if not entry:
        return None
    if datetime.now() - _ts(entry.get("ts", "")) > timedelta(hours=SESSION_TTL_HOURS):
        return None
    return entry["projekt"]


def clear_active_projekt(telegram_id: int) -> None:
    with _LOCK:
        data = _load()
        data["active"].pop(str(telegram_id), None)
        _save(data)


def save_pending(telegram_id: int, payload: dict[str, Any]) -> str:
    """Spremi parsirani rezultat čekajući potvrdu. Vrati ID za callback."""
    with _LOCK:
        data = _load()
        _gc_pending(data)
        pending_id = f"{telegram_id}_{int(datetime.now().timestamp() * 1000)}"
        data["pending"][pending_id] = {
            **payload,
            "ts": datetime.now().isoformat(),
        }
        _save(data)
        return pending_id


def link_preview(pending_id: str, chat_id: int, message_id: int) -> None:
    """Zapamti koju je preview poruku bot poslao za ovaj pending —
    reply radnika na tu poruku tretira se kao ispravak/dopuna."""
    with _LOCK:
        data = _load()
        data["preview"][f"{chat_id}:{message_id}"] = pending_id
        _save(data)


def pending_for_preview(chat_id: int, message_id: int) -> str | None:
    """Pending na koji pokazuje preview poruka (None ako nije preview/istekao)."""
    with _LOCK:
        data = _load()
    pid = data.get("preview", {}).get(f"{chat_id}:{message_id}")
    if pid and pid in data["pending"]:
        return pid
    return None


def get_pending(pending_id: str) -> dict[str, Any] | None:
    with _LOCK:
        data = _load()
    return data["pending"].get(pending_id)


def pop_pending(pending_id: str) -> dict[str, Any] | None:
    """Skini pending iz JSON-a i istovremeno ga zabilježi u 'consumed' (10 min)
    da druga (duplicirana) potvrda zna da je već obrađena."""
    with _LOCK:
        data = _load()
        payload = data["pending"].pop(pending_id, None)
        if payload is not None:
            _gc_consumed(data["consumed"])
            data["consumed"][pending_id] = datetime.now().isoformat()
        _save(data)
        return payload


def restore_pending(pending_id: str, payload: dict[str, Any]) -> None:
    """Vrati pending nakon neuspjelog upisa (suprotno od pop_pending),
    da korisnik može ponovno pritisnuti ✅."""
    with _LOCK:
        data = _load()
        data["consumed"].pop(pending_id, None)
        data["pending"][pending_id] = payload
        _save(data)


def was_recently_consumed(pending_id: str) -> bool:
    """Je li ovaj pending nedavno potvrđen (unutar TTL-a)?"""
    with _LOCK:
        data = _load()
        _gc_consumed(data["consumed"])
        return pending_id in data["consumed"]


# --- "Ispravi" tok -----------------------------------------------------------
def set_editing(telegram_id: int, pending_id: str) -> None:
    """Zabilježi da korisnik ispravlja postojeći pending — sljedeća poruka
    se parsira s kontekstom starog unosa."""
    with _LOCK:
        data = _load()
        data["editing"][str(telegram_id)] = pending_id
        _save(data)


def pop_editing_context(telegram_id: int) -> str | None:
    """Ako je korisnik u 'Ispravi' modu: makni stari pending i vrati njegovu
    sirovu poruku kao kontekst za novi parse. Inače None."""
    with _LOCK:
        data = _load()
        pending_id = data["editing"].pop(str(telegram_id), None)
        if not pending_id:
            return None
        payload = data["pending"].pop(pending_id, None)
        _save(data)
        return (payload or {}).get("sirova_poruka")
