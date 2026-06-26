"""Web Push notifikacije za terenske radnike (VAPID / pywebpush).

Koristi se kad voditelj dodijeli zadatak radniku — radnik dobije push
notifikaciju na mobitelu čak i ako ima browser zatvoren (PWA).

Konfiguracija u .env:
    VAPID_PUBLIC_KEY=...        (base64url kodirani uncompressed public key)
    VAPID_PRIVATE_KEY_PEM=...   (PEM string private ključa)
    VAPID_EMAIL=mailto:admin@sidcom.hr

Ako ključevi nisu postavljeni, sve funkcije tiho preskočite (best-effort).
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
_PRIVATE_KEY_PEM = os.getenv("VAPID_PRIVATE_KEY_PEM", "")
_EMAIL = os.getenv("VAPID_EMAIL", "mailto:admin@sidcom.hr")

ENABLED = bool(_PUBLIC_KEY and _PRIVATE_KEY_PEM)


def send_push(telegram_id: int, title: str, body: str) -> None:
    """Pošalji push notifikaciju radniku. Best-effort — nikad ne crasha."""
    if not ENABLED:
        return
    try:
        from services import db
        from services.models import Radnik

        with db.session() as s:
            r = s.get(Radnik, telegram_id)
            if not r or not r.push_subscription:
                return
            subscription_info = json.loads(r.push_subscription)

        _send(subscription_info, title, body)
    except Exception:
        log.warning("push_service: greška slanja push za %s", telegram_id, exc_info=True)


def _send(subscription_info: dict, title: str, body: str) -> None:
    from pywebpush import webpush, WebPushException

    payload = json.dumps({"title": title, "body": body})
    try:
        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=_PRIVATE_KEY_PEM,
            vapid_claims={"sub": _EMAIL},
        )
    except WebPushException as e:
        if e.response and e.response.status_code in (404, 410):
            # Subscription istekla — obrišemo je iz DB
            _invalidate_subscription(subscription_info.get("endpoint", ""))
        else:
            log.warning("WebPushException: %s", e)


def _invalidate_subscription(endpoint: str) -> None:
    if not endpoint:
        return
    try:
        from services import db
        from services.models import Radnik
        import sqlalchemy

        with db.session() as s:
            s.execute(
                sqlalchemy.update(Radnik)
                .where(Radnik.push_subscription.like(f'%{endpoint[:80]}%'))
                .values(push_subscription=None)
            )
    except Exception:
        pass
