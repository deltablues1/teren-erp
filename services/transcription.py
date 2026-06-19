"""OpenAI Whisper API wrapper za transkripciju glasovnih poruka."""
from __future__ import annotations

import logging
from pathlib import Path

from openai import OpenAI

from config import OPENAI_API_KEY

log = logging.getLogger(__name__)

_client = OpenAI(api_key=OPENAI_API_KEY)

WHISPER_PROMPT = (
    "Razgovor o električarskim radovima na gradilištu na hrvatskom jeziku. "
    "Spominju se materijali poput: kabel NYM, NHXH, sapi, doza, razvodna kutija, "
    "instalacijska cijev, prekidač, utičnica, automatski osigurač, FID sklopka, "
    "razvodni ormar, LED rasvjeta. Lokacije: prizemlje, kat, podrum, soba, hodnik."
)


def transcribe(audio_path: Path) -> str:
    """Transkribiraj audio file pomoću Whisper API-ja. Vrati tekst na hrvatskom."""
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file ne postoji: {audio_path}")

    log.debug("Transkribiram %s", audio_path)
    with audio_path.open("rb") as f:
        response = _client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language="hr",
            prompt=WHISPER_PROMPT,
            response_format="text",
        )
    text = response.strip() if isinstance(response, str) else str(response).strip()
    log.info("Transkripcija (%d znakova): %s", len(text), text[:100])
    return text
