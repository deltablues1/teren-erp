"""Web asistent — chatbot nad bazom terenskih podataka (read-only).

Voditelj postavlja pitanja na hrvatskom (npr. "koliko kabela na Kući Horvat"),
a Claude preko tool-use-a pretražuje Postgres (projekti, materijali, dnevnik,
troškovnik, katalog) i odgovara. Ništa ne mijenja u bazi — samo čita.

Sloj: web → web.data / services.db_backend. Isti obrazac tool-use-a kao
services/claude_parser.py.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from anthropic import Anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from services import db_backend
from web import data

log = logging.getLogger(__name__)

_client = Anthropic(api_key=ANTHROPIC_API_KEY)

MAX_KORAKA = 6  # koliko krugova tool-use-a dopuštamo prije nego odustanemo


TOOLS: list[dict[str, Any]] = [
    {
        "name": "popis_projekata",
        "description": (
            "Vrati popis svih aktivnih projekata (key, naziv, adresa, investitor, "
            "broj stavki troškovnika/dnevnika/materijala). Pozovi ovo PRVO kad "
            "trebaš naći 'key' projekta po neformalnom imenu (npr. 'Kuća Horvat')."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "detalji_projekta",
        "description": (
            "Detalji jednog projekta po 'key': zaglavlje, utrošeni materijali "
            "(zbrojeni po opisu, s količinom, JM i vrijednošću) i zadnji unosi "
            "dnevnika. Koristi za pitanja tipa 'koliko je X utrošeno' ili 'što se "
            "radilo'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"projekt_key": {"type": "string"}},
            "required": ["projekt_key"],
        },
    },
    {
        "name": "troskovnik_projekta",
        "description": (
            "Stavke troškovnika projekta po 'key': ugovorena količina, jedinična "
            "cijena i izvedeno do sada. Koristi za pitanja o ugovorenom vs. "
            "izvedenom i koliko je ostalo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"projekt_key": {"type": "string"}},
            "required": ["projekt_key"],
        },
    },
    {
        "name": "situacije_projekta",
        "description": (
            "Situacije (obračuni) projekta po 'key': redni broj, datum, status, "
            "kumulativna vrijednost i iznos svake situacije (razlika prema "
            "prethodnoj). Koristi za pitanja o situacijama i obračunu."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"projekt_key": {"type": "string"}},
            "required": ["projekt_key"],
        },
    },
    {
        "name": "pretrazi_katalog",
        "description": (
            "Pretraži šifrarnik artikala (materijala) po pojmu — traži po nazivu, "
            "žargonu i šifri. Koristi za pitanja o artiklima/cijenama."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"pojam": {"type": "string"}},
            "required": ["pojam"],
        },
    },
]


def _sazmi_detalje(d: dict[str, Any]) -> dict[str, Any]:
    """Zbroji materijale po (naziv/opis, JM) da odgovor bude kompaktan i točan."""
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for m in d.get("materijali", []):
        kljuc = (m.get("katalog_naziv") or m.get("opis") or "?", m.get("jm") or "")
        red = agg.setdefault(
            kljuc, {"opis": kljuc[0], "jm": kljuc[1], "kolicina": 0.0, "vrijednost": 0.0}
        )
        red["kolicina"] += m.get("kolicina") or 0.0
        if m.get("vrijednost"):
            red["vrijednost"] += m["vrijednost"]
    materijali = sorted(agg.values(), key=lambda x: -x["kolicina"])
    for red in materijali:
        red["kolicina"] = round(red["kolicina"], 2)
        red["vrijednost"] = round(red["vrijednost"], 2) or None
    return {
        "projekt": {
            "key": d.get("key"),
            "naziv": d.get("naziv"),
            "adresa": d.get("adresa"),
            "investitor": d.get("investitor"),
            "tip": d.get("tip"),
        },
        "materijali_zbrojeno": materijali,
        "ukupna_vrijednost_materijala_eur": d.get("ukupno_vrijednost"),
        "dnevnik_zadnji": d.get("dnevnik", [])[:40],
    }


def _run_tool(name: str, inp: dict[str, Any]) -> dict[str, Any]:
    if name == "popis_projekata":
        return {"projekti": data.list_projekti()}
    if name == "detalji_projekta":
        d = data.projekt_detail((inp.get("projekt_key") or "").strip())
        if not d:
            return {"greska": "Projekt s tim key-em ne postoji. Pozovi popis_projekata."}
        return _sazmi_detalje(d)
    if name == "troskovnik_projekta":
        stavke = db_backend.get_troskovnik((inp.get("projekt_key") or "").strip())
        if not stavke:
            return {"greska": "Nema troškovnika za taj projekt (ili krivi key)."}
        return {"stavke": stavke}
    if name == "situacije_projekta":
        sits = data.situacije((inp.get("projekt_key") or "").strip())
        if not sits:
            return {"greska": "Nema situacija za taj projekt (ili krivi key)."}
        return {"situacije": sits}
    if name == "pretrazi_katalog":
        artikli, total = data.katalog_search((inp.get("pojam") or "").strip(), limit=30)
        return {"artikli": artikli, "ukupno_nadeno": total}
    return {"greska": f"Nepoznat alat: {name}"}


def _system_prompt() -> str:
    return (
        "Ti si interni asistent za bazu terenskih izvještaja male "
        "elektroinstalaterske firme. Voditelj te pita o projektima, utrošenom "
        "materijalu, dnevniku rada i troškovniku (ugovoreno vs. izvedeno).\n\n"
        "Pravila:\n"
        "1) Odgovaraj ISKLJUČIVO na temelju podataka koje vrate alati — ništa ne "
        "izmišljaj. Ako podataka nema, jasno reci da ih nema.\n"
        "2) Imena projekata su neformalna. Kad ti treba projekt, prvo pozovi "
        "popis_projekata i odaberi najbliži (npr. 'Kuća Horvat' → projekt 'Horvat').\n"
        "3) Količine navodi s mjernom jedinicom; novac u EUR.\n"
        "4) Budi kratak i konkretan. Odgovaraj na hrvatskom.\n"
        "5) Piši običan tekst i jednostavne liste s crticom — NE markdown "
        "tablice (sučelje ih ne prikazuje kao tablicu).\n"
        f"Današnji datum: {date.today().isoformat()}."
    )


def odgovori(poruke: list[dict[str, Any]]) -> str:
    """poruke: [{'uloga': 'korisnik'|'asistent', 'tekst': str}]. Vrati tekst odgovora."""
    messages: list[dict[str, Any]] = [
        {
            "role": "assistant" if p.get("uloga") == "asistent" else "user",
            "content": p.get("tekst", ""),
        }
        for p in poruke
        if (p.get("tekst") or "").strip()
    ]
    if not messages:
        return "Postavi pitanje."

    for _ in range(MAX_KORAKA):
        resp = _client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            system=_system_prompt(),
            tools=TOOLS,
            messages=messages,
        )
        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})
            rezultati = []
            for block in resp.content:
                if block.type == "tool_use":
                    try:
                        out = _run_tool(block.name, dict(block.input or {}))
                    except Exception as e:  # alat ne smije srušiti chat
                        log.exception("Asistent alat '%s' pukao", block.name)
                        out = {"greska": str(e)}
                    rezultati.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(out, ensure_ascii=False, default=str),
                    })
            messages.append({"role": "user", "content": rezultati})
            continue

        tekst = "".join(b.text for b in resp.content if b.type == "text").strip()
        return tekst or "Nemam odgovor."

    return "Pitanje je previše složeno (predugo tražim). Pokušaj preciznije."
