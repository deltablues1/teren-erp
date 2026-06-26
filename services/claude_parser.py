"""Claude API wrapper - parsira poruke radnika u strukturirane podatke
korištenjem tool-use (structured output)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from anthropic import Anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

log = logging.getLogger(__name__)

# timeout bounda najgori slučaj: ako poziv zapne, nit se ne vrti 10 min (SDK default)
_client = Anthropic(api_key=ANTHROPIC_API_KEY, timeout=60.0)


@dataclass
class ParsedReport:
    """Rezultat parsinga jedne poruke radnika."""
    opis_rada: str
    lokacija: str
    strujni_krug: str = ""             # npr. "9.1" ili "9.1, 9.2"; "" ako nije naveden
    datum_rada: str = ""              # YYYY-MM-DD, "" znači koristi today
    vrijeme_rada: str = ""             # "08:00-16:00" ili ""
    sati: float | None = None          # ukupno sati taj dan
    radnici_spomenuti: list[str] = field(default_factory=list)
    materijali: list[dict[str, Any]] = field(default_factory=list)
    problemi: list[str] = field(default_factory=list)
    potreban_materijal: list[str] = field(default_factory=list)
    confidence: str = "high"           # high | medium | low
    nedostaje: list[str] = field(default_factory=list)
    pojasnjenje_potrebno: str = ""

    def has_low_confidence(self) -> bool:
        return self.confidence == "low" or bool(self.pojasnjenje_potrebno)

    def to_dict(self) -> dict[str, Any]:
        return {
            "opis_rada": self.opis_rada,
            "lokacija": self.lokacija,
            "strujni_krug": self.strujni_krug,
            "datum_rada": self.datum_rada,
            "vrijeme_rada": self.vrijeme_rada,
            "sati": self.sati,
            "radnici_spomenuti": self.radnici_spomenuti,
            "materijali": self.materijali,
            "problemi": self.problemi,
            "potreban_materijal": self.potreban_materijal,
            "confidence": self.confidence,
            "nedostaje": self.nedostaje,
            "pojasnjenje_potrebno": self.pojasnjenje_potrebno,
        }


PARSE_TOOL = {
    "name": "zabiljezi_izvjestaj",
    "description": (
        "Zabilježi strukturirani izvještaj električara s terena. "
        "Izvuci opis rada, lokaciju, sate, radnike i materijale. "
        "Polja popunjavaj SAMO ako ih radnik izričito navede ili se mogu "
        "neupitno zaključiti. Ne izmišljaj. Ako fali bitno, dodaj u 'nedostaje'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "opis_rada": {
                "type": "string",
                "description": (
                    "Kratak poslovni opis izvedenih radova (1-2 rečenice, "
                    "hrvatski, prošlo vrijeme/pasiv)."
                ),
            },
            "lokacija": {
                "type": "string",
                "description": (
                    "Lokacija na gradilištu (npr. 'Prizemlje, soba 2'). "
                    "Prazno ako nije navedeno."
                ),
            },
            "strujni_krug": {
                "type": "string",
                "description": (
                    "Strujni krug/krugovi na koje se rad odnosi, samo oznaka "
                    "(npr. '9.1', ili '9.1, 9.2' ako više njih). Električari "
                    "često kažu 'strujni krug 9.1' ili 'SK 9.1'. Prazno ako "
                    "nije naveden."
                ),
            },
            "datum_rada": {
                "type": "string",
                "description": (
                    "Datum izvršenja u formatu YYYY-MM-DD. "
                    "Prazno ako radnik uopće ne navodi datum (tada se podrazumijeva današnji dan). "
                    "KRITIČNO: ako radnik napiše datum BEZ godine (npr. '25.6.', '25/6', '25.06'), "
                    "uvijek koristi TEKUĆU godinu iz system prompta (npr. 2026-06-25, NE 2025-06-25). "
                    "Primjeri: 'jučer' → jučerašnji datum s tekućom godinom; "
                    "'u ponedjeljak' → zadnji ponedjeljak s tekućom godinom."
                ),
            },
            "vrijeme_rada": {
                "type": "string",
                "description": (
                    "Raspon rada u formatu 'HH:MM-HH:MM' (npr. '08:00-16:00'). "
                    "Prazno ako nije navedeno."
                ),
            },
            "sati": {
                "type": ["number", "null"],
                "description": (
                    "Ukupno odrađenih sati za radnika koji šalje poruku. "
                    "Ako je naveden raspon, izračunaj. Null ako nije moguće."
                ),
            },
            "radnici_spomenuti": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Imena drugih radnika spomenutih u poruci (npr. 'Marko i ja' "
                    "→ ['Marko']). Prazna lista ako nema."
                ),
            },
            "materijali": {
                "type": "array",
                "description": "Lista utrošenih materijala. Prazna ako nema.",
                "items": {
                    "type": "object",
                    "properties": {
                        "sifra_stavke": {
                            "type": "string",
                            "description": (
                                "Šifra iz troškovnika ako je sigurno mapirano, "
                                "inače prazan string."
                            ),
                        },
                        "opis": {
                            "type": "string",
                            "description": (
                                "Opis materijala kako ga je radnik naveo "
                                "(npr. 'kabel NYM 3x2.5')."
                            ),
                        },
                        "kolicina": {
                            "type": "number",
                            "description": "Količina (broj).",
                        },
                        "jm": {
                            "type": "string",
                            "description": (
                                "Jedinica mjere (m, kom, m2, kg, ...). "
                                "Ako nije rečena, pretpostavi po kontekstu "
                                "(kabel→m, utičnica→kom)."
                            ),
                        },
                        "strujni_krug": {
                            "type": "string",
                            "description": (
                                "Strujni krug za OVU stavku (npr. '9.1'), ako "
                                "ga radnik veže uz baš taj materijal. Prazno "
                                "ako nije naveden ili vrijedi opći (vidi "
                                "strujni_krug na razini izvještaja)."
                            ),
                        },
                    },
                    "required": ["opis", "kolicina", "jm"],
                },
            },
            "problemi": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Problemi, zastoji, nezgode ili nejasnoće koje radnik "
                    "spominje. Ide u sekciju 'Posebne napomene' dnevnika. "
                    "Prazna lista ako nema."
                ),
            },
            "potreban_materijal": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Materijal koji radniku NEDOSTAJE ili ga treba dovesti "
                    "na gradilište (s količinom ako je navede), npr. "
                    "'50m NYM 3x1,5', 'gips ploče'. NIJE isto što i "
                    "'materijali' (to je utrošeno). Prazna lista ako ne "
                    "spominje potrebu."
                ),
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": (
                    "high: sve je jasno; medium: ponešto nejasno ali se može "
                    "spremiti; low: previše nejasno, treba pitati radnika."
                ),
            },
            "nedostaje": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Lista bitnih polja koja fale za potpun unos (npr. "
                    "'količina kabela', 'lokacija', 'broj sati'). Prazno ako "
                    "je sve jasno."
                ),
            },
            "pojasnjenje_potrebno": {
                "type": "string",
                "description": (
                    "Ako confidence='low', napiši kratko pitanje za radnika "
                    "(npr. 'Koliko metara kabela si postavio?'). Inače prazno."
                ),
            },
        },
        "required": [
            "opis_rada", "lokacija", "strujni_krug", "datum_rada", "vrijeme_rada",
            "sati", "radnici_spomenuti", "materijali", "problemi",
            "potreban_materijal", "confidence", "nedostaje", "pojasnjenje_potrebno",
        ],
    },
}


def _build_system_prompt(troskovnik: list[dict[str, Any]] | None) -> str:
    from datetime import datetime as _dt
    today_str = _dt.now().strftime("%Y-%m-%d")
    year_str = _dt.now().strftime("%Y")
    base = (
        f"Danas je {today_str} (tekuća godina: {year_str}). "
        "VAŽNO za datume: kada radnik navede datum bez godine (npr. '25.6.', '25/6'), "
        f"uvijek pretpostavi tekuću godinu {year_str}. Nikad ne koristiti prošle godine. "
        "Ti si asistent koji parsira izvještaje električara s gradilišta na hrvatskom jeziku. "
        "Razumiješ stručni žargon ('petica' = NYM 3x2.5, 'trojka' = NYM 3x1.5, "
        "'crijevo' = instalacijska cijev, 'doza' = razvodna kutija itd.). "
        "Tvoj zadatak je iz slobodnog teksta izvući strukturu i pozvati alat zabiljezi_izvjestaj. "
        "Uvijek pozovi alat - i kad je poruka nejasna (tada s niskim confidence). "
        "Nikad ne odgovaraj tekstom. "
        "VAŽNO razlikuj: 'materijali' = što je UGRAĐENO/potrošeno; "
        "'potreban_materijal' = što radniku FALI ili traži da se doveze "
        "('ponestalo nam je...', 'treba nam...', 'nemamo više...'). "
        "STRUJNI KRUG: električari često rade i javljaju po strujnim krugovima "
        "('strujni krug 9.1', 'SK 9.1', 'krug 9.1'). Izvuci oznaku kruga u "
        "'strujni_krug' (na razini izvještaja i/ili po stavci materijala ako se "
        "krug navodi uz pojedini kabel/cijev). Upiši samo oznaku (npr. '9.1'). "
        "Probleme i potrebu za materijalom uvijek izvuci ako se spominju - "
        "to su prioritetne informacije za voditelja. "
        "Ako poruka ima 'Prethodni kontekst', nova poruka je ISPRAVAK ili "
        "DOPUNA tog istog izvještaja - spoji ih u JEDAN potpun izvještaj; "
        "kod proturječja vrijedi novija informacija."
    )
    if troskovnik:
        lines = []
        for row in troskovnik[:400]:
            sifra = row.get("Šifra", "")
            opis = row.get("Opis stavke", "")
            jm = row.get("JM", "")
            sekcija = row.get("Sekcija", "")
            zargon = row.get("Ključne riječi", "")
            line = f"  [{sifra}] {opis} (JM:{jm}"
            if sekcija:
                line += f", sekcija:{sekcija}"
            if zargon:
                line += f", žargon:{zargon}"
            line += ")"
            lines.append(line)
        items_text = "\n".join(lines)
        base += (
            "\n\nTroškovnik projekta — mapiraj materijale na ove šifre. "
            "Koristi 'žargon' ključne riječi za prepoznavanje slenga "
            "('petica'→NYM 3x2,5). Pazi na sekciju i lokaciju: isti kabel "
            "može biti u više pozicija (rasvjeta vs. utičnice).\n"
            f"{items_text}\n\n"
            "Pravilo: postavi sifra_stavke SAMO ako si razumno siguran. "
            "Ako nisi siguran, ostavi prazno - voditelj će ručno mapirati."
        )
    return base


def parse_report(
    tekst: str, troskovnik: list[dict[str, Any]] | None = None,
    prethodni_kontekst: str = "",
) -> ParsedReport:
    """Parsiraj jednu poruku radnika u strukturu."""
    user_content = tekst
    if prethodni_kontekst:
        user_content = (
            f"Prethodni kontekst:\n{prethodni_kontekst}\n\n"
            f"Nova poruka radnika:\n{tekst}"
        )

    log.debug("Pozivam Claude za parsing, model=%s", CLAUDE_MODEL)
    # cache_control: troškovnik u system promptu može imati 10k+ tokena;
    # caching ga naplati jednom, a ponovne poruke unutar 5 min idu ~10x jeftinije
    response = _client.messages.create(
        model=CLAUDE_MODEL,
        # 1024 je bio premalen: kod detaljnog izvještaja (npr. materijali po
        # strujnim krugovima) model potroši limit na opis pa se lista materijala
        # TIHO odsiječe (stop_reason=max_tokens) i izgubi se sav materijal.
        max_tokens=4096,
        temperature=0,
        system=[{
            "type": "text",
            "text": _build_system_prompt(troskovnik),
            "cache_control": {"type": "ephemeral"},
        }],
        tools=[PARSE_TOOL],
        tool_choice={"type": "tool", "name": "zabiljezi_izvjestaj"},
        messages=[{"role": "user", "content": user_content}],
    )
    if response.stop_reason == "max_tokens":
        log.warning(
            "parse_report: izlaz odsječen na max_tokens — materijali mogu biti "
            "nepotpuni. Poruka je vjerojatno predugačka; povećaj max_tokens."
        )

    for block in response.content:
        if block.type == "tool_use" and block.name == "zabiljezi_izvjestaj":
            data = block.input
            sati_raw = data.get("sati")
            try:
                sati = float(sati_raw) if sati_raw is not None else None
            except (TypeError, ValueError):
                sati = None
            # sanity check za datum: ako Claude javi datum izvan ±90 dana od danas,
            # vjerojatno je kriva godina → zamijeni godinom tekuće; ako i dalje
            # izvan ±90 dana, ignoriraj (default = today)
            datum_raw = (data.get("datum_rada") or "").strip()
            datum_valid = ""
            if datum_raw:
                from datetime import datetime as _dt, timedelta as _td
                try:
                    d = _dt.strptime(datum_raw, "%Y-%m-%d").date()
                    today = _dt.now().date()
                    if abs((d - today).days) <= 90:
                        datum_valid = datum_raw
                    else:
                        # pokušaj zamijeniti samo godinu tekućom
                        fixed = d.replace(year=today.year)
                        if abs((fixed - today).days) <= 90:
                            log.warning(
                                "Claude datum_rada %s ima krivu godinu, ispravljam na %s",
                                datum_raw, fixed,
                            )
                            datum_valid = fixed.strftime("%Y-%m-%d")
                        else:
                            log.warning(
                                "Claude datum_rada %s je izvan ±90 dana (danas=%s), ignoriram",
                                datum_raw, today,
                            )
                except ValueError:
                    pass
            return ParsedReport(
                opis_rada=data.get("opis_rada", ""),
                lokacija=data.get("lokacija", ""),
                strujni_krug=(data.get("strujni_krug") or "").strip(),
                datum_rada=datum_valid,
                vrijeme_rada=data.get("vrijeme_rada", "") or "",
                sati=sati,
                radnici_spomenuti=data.get("radnici_spomenuti", []) or [],
                materijali=data.get("materijali", []) or [],
                problemi=data.get("problemi", []) or [],
                potreban_materijal=data.get("potreban_materijal", []) or [],
                confidence=data.get("confidence", "high"),
                nedostaje=data.get("nedostaje", []) or [],
                pojasnjenje_potrebno=data.get("pojasnjenje_potrebno", "") or "",
            )

    raise RuntimeError(
        f"Claude nije pozvao tool. Odgovor: {response.content}"
    )


VISION_TOOL = {
    "name": "procitaj_sliku",
    "description": (
        "Pročitaj papir s gradilišta sa slike: klasificiraj o čemu se radi, "
        "vjerno prepiši tekst, a ako je otpremnica/dostavnica/račun izvuci "
        "i strukturirane stavke."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "tip": {
                "type": "string",
                "enum": ["opis_rada", "otpremnica", "drugo"],
                "description": (
                    "'otpremnica' = otpremnica/dostavnica/račun s popisom "
                    "robe i količinama (tiskani dokument dobavljača). "
                    "'opis_rada' = (rukom) pisani opis izvedenih radova. "
                    "'drugo' = ostalo (nacrt, fotka gradilišta...)."
                ),
            },
            "prijepis": {
                "type": "string",
                "description": (
                    "Vjeran prijepis SVEG teksta sa slike, hrvatski. Brojeve "
                    "i količine prepiši točno, žargon ostavi. Nečitljivo "
                    "označi [nečitko]. Prazno ako nema teksta."
                ),
            },
            "dobavljac": {
                "type": "string",
                "description": "Naziv dobavljača/firme s dokumenta. Prazno ako nije otpremnica.",
            },
            "broj_dokumenta": {
                "type": "string",
                "description": "Broj otpremnice/računa (npr. 'OTP 123/2026'). Prazno ako nema.",
            },
            "datum": {
                "type": "string",
                "description": "Datum dokumenta YYYY-MM-DD. Prazno ako nije vidljiv.",
            },
            "stavke": {
                "type": "array",
                "description": (
                    "Stavke robe s otpremnice. Prazna lista ako tip nije "
                    "'otpremnica'."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "opis": {
                            "type": "string",
                            "description": "Naziv/opis artikla kako piše na dokumentu.",
                        },
                        "kolicina": {
                            "type": ["number", "null"],
                            "description": "Količina. Null ako nije čitljiva.",
                        },
                        "jm": {
                            "type": "string",
                            "description": "Jedinica mjere (m, kom, kpl...). Prazno ako ne piše.",
                        },
                    },
                    "required": ["opis", "kolicina", "jm"],
                },
            },
        },
        "required": ["tip", "prijepis", "dobavljac", "broj_dokumenta", "datum", "stavke"],
    },
}


def procitaj_sliku(
    image_bytes: bytes,
    media_type: str = "image/jpeg",
    napomena: str = "",
) -> dict[str, Any]:
    """Pročitaj sliku papira s gradilišta: klasifikacija + prijepis + stavke.

    napomena = radnikov caption uz sliku ('otpremnica od Sonepara') — pomaže
    klasifikaciji. Vraća dict s ključevima iz VISION_TOOL sheme.
    """
    import base64

    prompt = (
        "Pročitaj ovu sliku s gradilišta i pozovi alat procitaj_sliku. "
        "Najčešće je rukom pisani opis elektroinstalaterskih radova ili "
        "tiskana otpremnica dobavljača."
    )
    if napomena.strip():
        prompt += f"\n\nRadnik uz sliku kaže: „{napomena.strip()}\""

    response = _client.messages.create(
        model=CLAUDE_MODEL,
        # otpremnica zna imati puno stavki + cijeli prijepis → 3000 može odsjeći
        max_tokens=8000,
        temperature=0,
        tools=[VISION_TOOL],
        tool_choice={"type": "tool", "name": "procitaj_sliku"},
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.b64encode(image_bytes).decode(),
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "procitaj_sliku":
            d = block.input
            return {
                "tip": d.get("tip", "drugo"),
                "prijepis": (d.get("prijepis") or "").strip(),
                "dobavljac": (d.get("dobavljac") or "").strip(),
                "broj_dokumenta": (d.get("broj_dokumenta") or "").strip(),
                "datum": (d.get("datum") or "").strip(),
                "stavke": d.get("stavke") or [],
            }
    raise RuntimeError("Claude nije pozvao procitaj_sliku tool.")


SUMMARY_TOOL = {
    "name": "sazimi_dnevnik",
    "description": (
        "Generiraj narativni opis radova izvedenih taj dan za građevinski dnevnik "
        "prema NN 60/2024. Spoji unose istog radnika u jedan paragraf."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "narativ": {
                "type": "string",
                "description": (
                    "Tekst za sekciju 'Opis izvedenih radova' u dnevniku. "
                    "Strukturiraj po radnicima ili po vrstama radova. "
                    "Formalni hrvatski stručni stil, prošlo vrijeme. "
                    "Bez 'ja' i 'mi' - koristi pasiv ili treće lice."
                ),
            },
        },
        "required": ["narativ"],
    },
}


def summarize_dnevnik(
    dnevnik_zapisi: list[dict[str, Any]],
    materijali_zapisi: list[dict[str, Any]],
    projekt_naziv: str,
    datum: str,
) -> str:
    """Generiraj narativ za sekciju 'Opis izvedenih radova'.

    Dnevnik traži OPĆENIT opis radova — BEZ nabrajanja točnih količina i BEZ
    oznaka strujnih krugova (to ide u građevinsku knjigu, ne u dnevnik).
    """
    if not dnevnik_zapisi:
        return f"Na dan {datum} nisu zabilježeni radovi."

    unosi_text = "\n".join(
        f"- {r.get('Vrijeme', '')} | {r.get('Radnik', '')}: "
        f"{r.get('Opis rada', '')} (lokacija: {r.get('Lokacija', 'n/p')})"
        for r in dnevnik_zapisi
    )

    user_msg = (
        f"Projekt: {projekt_naziv}\nDatum: {datum}\n\n"
        f"Sirovi unosi radnika:\n{unosi_text}\n\n"
        "Generiraj formalni narativ za građevinski dnevnik — općenit opis "
        "izvedenih radova po vrstama i lokacijama. NE navodi točne količine "
        "(metre, komade) ni oznake strujnih krugova; to ide u građevinsku knjigu."
    )

    response = _client.messages.create(
        model=CLAUDE_MODEL,
        # narativ za dan s puno unosa zna biti dug → 2048 može odsjeći
        max_tokens=4096,
        temperature=0.3,
        system=(
            "Ti pišeš službene građevinske dnevnike na hrvatskom jeziku "
            "prema Pravilniku NN 60/2024. Stil je formalan, stručan, prošlo "
            "vrijeme, treće lice ili pasiv. Bez emoji, bez markdowna. "
            "Opis radova je OPĆENIT (vrste radova i lokacije) — bez točnih "
            "količina i bez oznaka strujnih krugova."
        ),
        tools=[SUMMARY_TOOL],
        tool_choice={"type": "tool", "name": "sazimi_dnevnik"},
        messages=[{"role": "user", "content": user_msg}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "sazimi_dnevnik":
            return block.input.get("narativ", "")

    return ""


MATCH_TOOL = {
    "name": "povezi_materijale",
    "description": (
        "Za svaki materijal odaberi artikl_id iz ponuđenih kandidata kataloga "
        "koji mu NAJBOLJE odgovara. Ako nijedan kandidat ne odgovara (krivi "
        "proizvod, sasvim druga stvar), vrati artikl_id = null. Ne pogađaj na silu."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "poveznice": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "materijal_index": {"type": "integer"},
                        "artikl_id": {
                            "type": ["integer", "null"],
                            "description": "ID odabranog artikla iz kandidata, ili null.",
                        },
                    },
                    "required": ["materijal_index", "artikl_id"],
                },
            },
        },
        "required": ["poveznice"],
    },
}


def match_materijali_katalog(
    materijali: list[dict[str, Any]],
    kandidati_po_materijalu: list[list[dict[str, Any]]],
) -> list[dict[str, Any] | None]:
    """Za svaki materijal vrati odabrani kandidat-artikl (dict) ili None.
    Jedan Claude poziv za cijelu poruku (ekonomično)."""
    if not materijali or not any(kandidati_po_materijalu):
        return [None] * len(materijali)

    # mapa id -> kandidat dict (za rekonstrukciju odabira)
    id_to_kand: dict[int, dict[str, Any]] = {}
    blocks = []
    for i, (m, kand) in enumerate(zip(materijali, kandidati_po_materijalu)):
        red = [
            f"MATERIJAL {i}: \"{m.get('opis', '')}\" "
            f"({m.get('kolicina', '')} {m.get('jm', '')})"
        ]
        if kand:
            for k in kand:
                id_to_kand[k["id"]] = k
                red.append(
                    f"   kandidat artikl_id={k['id']}: {k['naziv']} (JM {k['jm']})"
                )
        else:
            red.append("   (nema kandidata — vrati null)")
        blocks.append("\n".join(red))

    user_msg = (
        "Poveži svaki materijal s točnim artiklom iz kataloga (ili null).\n\n"
        + "\n\n".join(blocks)
    )
    try:
        resp = _client.messages.create(
            model=CLAUDE_MODEL,
            # mapiranje vraća redak po materijalu → kod puno materijala 1024 odsiječe
            max_tokens=4096,
            temperature=0,
            system=(
                "Ti spajaš materijale koje radnik javi s artiklima iz kataloga "
                "dobavljača. Biraš isključivo iz ponuđenih kandidata po artikl_id. "
                "Budi strog: ako kandidat nije isti proizvod, vrati null."
            ),
            tools=[MATCH_TOOL],
            tool_choice={"type": "tool", "name": "povezi_materijale"},
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception:
        log.exception("Greška AI matchanja kataloga")
        return [None] * len(materijali)

    rezultat: list[dict[str, Any] | None] = [None] * len(materijali)
    for block in resp.content:
        if block.type == "tool_use" and block.name == "povezi_materijale":
            for p in block.input.get("poveznice", []):
                idx = p.get("materijal_index")
                aid = p.get("artikl_id")
                if isinstance(idx, int) and 0 <= idx < len(materijali) and aid in id_to_kand:
                    rezultat[idx] = id_to_kand[aid]
    return rezultat
