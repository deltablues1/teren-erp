"""Web sučelje za terenske radnike — zamjena za Telegram bot.

Rute su pod prefiksom /teren. Radnik se prijavljuje PIN-om (4-6 znamenki),
odabire projekt i šalje izvještaje (tekst / slika / audio). Isti parser i DB
kao i Telegram bot.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from services import repository as repo, zadaci as zadaci_srv
from services.claude_parser import parse_report, procitaj_sliku
from web import data as wd

_VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(prefix="/teren")


# ── helpers ───────────────────────────────────────────────────────────────────

def _pin_hash(pin: str) -> str:
    return hashlib.sha256(pin.strip().encode()).hexdigest()


def _authed(request: Request) -> bool:
    return bool(request.session.get("teren_radnik_id"))


def _redirect_login():
    return RedirectResponse(url="/teren/login", status_code=303)


def _tmpl(name: str, request: Request, ctx: dict):
    ctx["request"] = request
    ctx.setdefault("radnik_ime", request.session.get("teren_radnik_ime", ""))
    ctx.setdefault("projekt_key", request.session.get("teren_projekt_key", ""))
    ctx.setdefault("projekt_naziv", "")
    ctx.setdefault("vapid_public_key", _VAPID_PUBLIC_KEY)
    if ctx["projekt_key"] and not ctx["projekt_naziv"]:
        p = repo.get_projekt(ctx["projekt_key"])
        ctx["projekt_naziv"] = p["naziv"] if p else ctx["projekt_key"]
    return templates.TemplateResponse(f"teren/{name}", ctx)


# ── rute: auth ────────────────────────────────────────────────────────────────

@router.get("/")
def root(request: Request):
    if not _authed(request):
        return _redirect_login()
    if not request.session.get("teren_projekt_key"):
        return RedirectResponse(url="/teren/odabir", status_code=303)
    return RedirectResponse(url="/teren/unos", status_code=303)


@router.get("/login")
def login_get(request: Request):
    if _authed(request):
        return RedirectResponse(url="/teren", status_code=303)
    return _tmpl("login.html", request, {"greska": ""})


@router.post("/login")
def login_post(request: Request, pin: str = Form("")):
    pin = pin.strip()
    if not pin:
        return _tmpl("login.html", request, {"greska": "Unesite PIN."})
    radnik = wd.get_radnik_by_pin(_pin_hash(pin))
    if not radnik:
        return _tmpl("login.html", request, {"greska": "Pogrešan PIN. Pokušaj ponovno."})
    request.session["teren_radnik_id"] = radnik["telegram_id"]
    request.session["teren_radnik_ime"] = radnik["ime"]
    request.session["teren_default_vozilo_id"] = radnik.get("default_vozilo_id")
    request.session.pop("teren_projekt_key", None)
    request.session.pop("teren_draft", None)
    return RedirectResponse(url="/teren/odabir", status_code=303)


@router.get("/odjava")
def odjava(request: Request):
    for k in ("teren_radnik_id", "teren_radnik_ime", "teren_projekt_key", "teren_draft"):
        request.session.pop(k, None)
    return RedirectResponse(url="/teren/login", status_code=303)


# ── rute: odabir projekta ──────────────────────────────────────────────────────

@router.get("/odabir")
def odabir_get(request: Request):
    if not _authed(request):
        return _redirect_login()
    radnik_id = request.session["teren_radnik_id"]
    projekti = wd.get_projekti_za_radnika(radnik_id)
    return _tmpl("odabir.html", request, {"projekti": projekti})


@router.post("/odabir/{key}")
def odabir_post(request: Request, key: str):
    if not _authed(request):
        return _redirect_login()
    request.session["teren_projekt_key"] = key
    request.session.pop("teren_draft", None)
    return RedirectResponse(url="/teren/unos", status_code=303)


@router.get("/odabir/promjena")
def odabir_promjena(request: Request):
    if not _authed(request):
        return _redirect_login()
    request.session.pop("teren_projekt_key", None)
    request.session.pop("teren_draft", None)
    return RedirectResponse(url="/teren/odabir", status_code=303)


# ── rute: unos izvještaja ──────────────────────────────────────────────────────

@router.get("/unos")
def unos_get(request: Request, uspjeh: str = ""):
    if not _authed(request):
        return _redirect_login()
    if not request.session.get("teren_projekt_key"):
        return RedirectResponse(url="/teren/odabir", status_code=303)

    draft = request.session.get("teren_draft") or {}
    prefill = draft.get("sirova", "")

    return _tmpl("unos.html", request, {
        "prefill": prefill,
        "draft": draft,
        "uspjeh": bool(uspjeh),
    })


@router.post("/unos/parsiraj")
async def unos_parsiraj(
    request: Request,
    tekst: str = Form(""),
    datoteka: UploadFile = File(None),
):
    """AJAX: parsira tekst/sliku/audio → JSON + sprema draft u session."""
    if not _authed(request):
        return JSONResponse({"greska": "Nije prijavljen."}, status_code=401)
    if not request.session.get("teren_projekt_key"):
        return JSONResponse({"greska": "Nije odabran projekt."}, status_code=400)

    projekt_key = request.session["teren_projekt_key"]
    radnik_ime = request.session.get("teren_radnik_ime", "")
    sirova = tekst.strip()

    # Obrada uploadane datoteke (audio ili slika)
    if datoteka and datoteka.filename:
        ctype = (datoteka.content_type or "").lower()
        raw = await datoteka.read()

        if ctype.startswith("audio") or ctype.startswith("video"):
            try:
                import asyncio
                from services.transcription import transcribe
                suffix = Path(datoteka.filename).suffix or ".ogg"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
                    tf.write(raw)
                    tmp_path = Path(tf.name)
                txt = await asyncio.to_thread(transcribe, tmp_path)
                tmp_path.unlink(missing_ok=True)
                sirova = f"{sirova}\n\n{txt}".strip() if sirova else txt
            except Exception as e:
                log.exception("Greška transkripcije")
                return JSONResponse({"greska": f"Greška transkripcije: {e}"})

        elif ctype.startswith("image"):
            try:
                import asyncio
                ocr = await asyncio.to_thread(procitaj_sliku, raw, ctype)
                prijepis = ocr.get("prijepis", "")
                if ocr.get("tip") == "otpremnica" and ocr.get("stavke"):
                    dobavljac = ocr.get("dobavljac", "")
                    broj = ocr.get("broj_dokumenta", "")
                    zaglavlje = f"Otpremnica {dobavljac} {broj}".strip()
                    redci = "\n".join(
                        f"{s.get('opis','')} {s.get('kolicina','')} {s.get('jm','')}"
                        for s in ocr["stavke"] if s.get("opis")
                    )
                    sirova = f"{zaglavlje}:\n{redci}".strip()
                else:
                    sirova = f"{sirova}\n\n{prijepis}".strip() if sirova else prijepis
            except Exception as e:
                log.exception("Greška OCR-a")
                return JSONResponse({"greska": f"Greška čitanja slike: {e}"})

    if not sirova:
        return JSONResponse({"greska": "Nema teksta za parsiranje."})

    try:
        troskovnik = repo.get_troskovnik(projekt_key)
    except Exception:
        troskovnik = None

    try:
        import asyncio
        parsed = await asyncio.to_thread(parse_report, sirova, troskovnik)
    except Exception as e:
        log.exception("Greška parsiranja")
        return JSONResponse({"greska": f"Greška AI parsiranja: {e}"})

    draft: dict[str, Any] = {
        "sirova": sirova,
        "radnik_ime": radnik_ime,
        "telegram_id": request.session["teren_radnik_id"],
        "projekt_key": projekt_key,
        "parsed": parsed.to_dict(),
    }
    request.session["teren_draft"] = draft
    return JSONResponse({"parsed": parsed.to_dict(), "sirova": sirova})


@router.post("/unos/spremi")
def unos_spremi(request: Request):
    if not _authed(request):
        return _redirect_login()

    draft = request.session.get("teren_draft")
    if not draft:
        return RedirectResponse(url="/teren/unos", status_code=303)

    parsed = draft["parsed"]

    materijali: list[dict[str, Any]] = []
    for m in parsed.get("materijali") or []:
        try:
            kolicina = float(m.get("kolicina") or 0)
        except (TypeError, ValueError):
            kolicina = 0.0
        materijali.append({
            "radnik": draft["radnik_ime"],
            "telegram_id": draft["telegram_id"],
            "sifra": str(m.get("sifra_stavke") or ""),
            "opis": str(m.get("opis") or ""),
            "kolicina": kolicina,
            "jm": str(m.get("jm") or ""),
            "lokacija": parsed.get("lokacija", ""),
            "strujni_krug": str(
                m.get("strujni_krug") or parsed.get("strujni_krug") or ""
            ),
        })

    problemi = list(parsed.get("problemi") or [])
    for t in parsed.get("potreban_materijal") or []:
        problemi.append(f"Potreban materijal: {t}")

    try:
        repo.append_izvjestaj(
            draft["projekt_key"],
            dnevnik={
                "radnik": draft["radnik_ime"],
                "telegram_id": draft["telegram_id"],
                "opis": parsed.get("opis_rada", ""),
                "lokacija": parsed.get("lokacija", ""),
                "strujni_krug": parsed.get("strujni_krug", "") or "",
                "sirova": draft["sirova"],
                "msg_id": 0,
                "datum_rada": parsed.get("datum_rada", "") or "",
                "vrijeme_rada": parsed.get("vrijeme_rada", "") or "",
                "sati": parsed.get("sati"),
                "radnici_spomenuti": parsed.get("radnici_spomenuti") or [],
                "problemi": problemi,
                "confidence": parsed.get("confidence", ""),
            },
            materijali=materijali,
        )
    except Exception as e:
        log.exception("Greška upisa izvještaja")
        return _tmpl("unos.html", request, {
            "prefill": draft.get("sirova", ""),
            "draft": draft,
            "uspjeh": False,
            "greska_spremi": str(e),
        })

    _obavijesti_admin(draft)
    request.session.pop("teren_draft", None)
    return RedirectResponse(url="/teren/unos?uspjeh=1", status_code=303)


@router.post("/unos/odbaci")
def unos_odbaci(request: Request):
    request.session.pop("teren_draft", None)
    return RedirectResponse(url="/teren/unos", status_code=303)


# ── rute: zadaci ───────────────────────────────────────────────────────────────

@router.get("/zadaci")
def zadaci_get(request: Request):
    if not _authed(request):
        return _redirect_login()
    projekt_key = request.session.get("teren_projekt_key", "")
    radnik_id = request.session["teren_radnik_id"]

    zadaci = []
    if projekt_key:
        try:
            zadaci = zadaci_srv.list_otvoreni(projekt_key, radnik_id)
        except Exception:
            log.exception("Greška dohvaćanja zadataka")

    return _tmpl("zadaci.html", request, {"zadaci": zadaci})


@router.post("/zadaci/{zadatak_id}/dovrsi")
def zadatak_dovrsi(request: Request, zadatak_id: int):
    if not _authed(request):
        return _redirect_login()
    try:
        radnik_id = request.session["teren_radnik_id"]
        zadaci_srv.oznaci_gotovo(zadatak_id, radnik_id)
    except Exception:
        log.exception("Greška označavanja gotovim")
    return RedirectResponse(url="/teren/zadaci", status_code=303)


@router.post("/zadaci/{zadatak_id}/odgodi")
def zadatak_odgodi(request: Request, zadatak_id: int):
    if not _authed(request):
        return _redirect_login()
    try:
        zadaci_srv.odgodi(zadatak_id)
    except Exception:
        log.exception("Greška odgode zadatka")
    return RedirectResponse(url="/teren/zadaci", status_code=303)


# ── rute: zaliha ───────────────────────────────────────────────────────────────

@router.get("/zaliha")
def zaliha_get(request: Request):
    return RedirectResponse(url="/teren/materijal", status_code=303)


@router.get("/materijal")
def materijal_get(request: Request, tab: str = "moje", poruka: str = ""):
    if not _authed(request):
        return _redirect_login()
    radnik_id = request.session["teren_radnik_id"]
    projekt_key = request.session.get("teren_projekt_key", "")
    from services import skladiste as skl
    moja_zaliha = wd.get_zaliha_radnika(radnik_id)
    skladiste = skl.stanje("skladiste", "") if skl.ENABLED else []
    return _tmpl("materijal.html", request, {
        "tab": tab,
        "moja_zaliha": moja_zaliha,
        "skladiste": skladiste,
        "poruka": poruka,
        "ima_projekt": bool(projekt_key),
        "aktivno": "materijal",
    })


def _parse_kolicina(s: str) -> float | None:
    try:
        q = float(s.replace(",", "."))
        return q if q > 0 else None
    except Exception:
        return None


@router.post("/materijal/vrati")
def materijal_vrati(request: Request, opis: str = Form(""), jm: str = Form(""), kolicina: str = Form("")):
    """Povrat materijala s radnika nazad u skladište."""
    if not _authed(request):
        return _redirect_login()
    radnik_id = request.session["teren_radnik_id"]
    q = _parse_kolicina(kolicina)
    if not q:
        return RedirectResponse(url="/teren/materijal?tab=moje&poruka=Nevažeća+količina.", status_code=303)
    from services import skladiste as skl
    skl.povrat(opis, q, od_tip="radnik", od_id=str(radnik_id), jm=jm, created_by=radnik_id)
    return RedirectResponse(url="/teren/materijal?tab=moje&poruka=Vraćeno+u+skladište.", status_code=303)


@router.post("/materijal/na-gradiliste")
def materijal_na_gradiliste(request: Request, opis: str = Form(""), jm: str = Form(""), kolicina: str = Form("")):
    """Prijenos materijala s radnika na gradilište."""
    if not _authed(request):
        return _redirect_login()
    radnik_id = request.session["teren_radnik_id"]
    projekt_key = request.session.get("teren_projekt_key", "")
    if not projekt_key:
        return RedirectResponse(url="/teren/materijal?tab=moje&poruka=Odaberi+projekt+prvo.", status_code=303)
    q = _parse_kolicina(kolicina)
    if not q:
        return RedirectResponse(url="/teren/materijal?tab=moje&poruka=Nevažeća+količina.", status_code=303)
    from services import skladiste as skl
    skl.prijenos(opis, q, od_tip="radnik", od_id=str(radnik_id),
                 na_tip="gradiliste", na_id=projekt_key, jm=jm, created_by=radnik_id)
    return RedirectResponse(url="/teren/materijal?tab=moje&poruka=Preneseno+na+gradilište.", status_code=303)


@router.post("/materijal/zaduzi-na-sebe")
def materijal_zaduzi_na_sebe(request: Request, opis: str = Form(""), jm: str = Form(""), kolicina: str = Form("")):
    """Zaduženje iz centralnog skladišta na radnika."""
    if not _authed(request):
        return _redirect_login()
    radnik_id = request.session["teren_radnik_id"]
    q = _parse_kolicina(kolicina)
    if not q:
        return RedirectResponse(url="/teren/materijal?tab=skladiste&poruka=Nevažeća+količina.", status_code=303)
    from services import skladiste as skl
    skl.zaduzi(opis, q, na_tip="radnik", na_id=str(radnik_id), jm=jm, created_by=radnik_id)
    return RedirectResponse(url="/teren/materijal?tab=moje&poruka=Zaduženo+na+vas.", status_code=303)


@router.post("/materijal/zaduzi-na-gradiliste")
def materijal_zaduzi_na_gradiliste(request: Request, opis: str = Form(""), jm: str = Form(""), kolicina: str = Form("")):
    """Zaduženje iz centralnog skladišta direktno na gradilište."""
    if not _authed(request):
        return _redirect_login()
    radnik_id = request.session["teren_radnik_id"]
    projekt_key = request.session.get("teren_projekt_key", "")
    if not projekt_key:
        return RedirectResponse(url="/teren/materijal?tab=skladiste&poruka=Odaberi+projekt+prvo.", status_code=303)
    q = _parse_kolicina(kolicina)
    if not q:
        return RedirectResponse(url="/teren/materijal?tab=skladiste&poruka=Nevažeća+količina.", status_code=303)
    from services import skladiste as skl
    skl.zaduzi(opis, q, na_tip="gradiliste", na_id=projekt_key, jm=jm, created_by=radnik_id)
    return RedirectResponse(url="/teren/materijal?tab=skladiste&poruka=Zaduženo+na+gradilište.", status_code=303)


@router.get("/troskovnik")
def troskovnik_get(request: Request):
    if not _authed(request):
        return _redirect_login()
    projekt_key = request.session.get("teren_projekt_key", "")
    stavke = []
    if projekt_key:
        pregled = wd.troskovnik_pregled(projekt_key)
        stavke = pregled.get("stavke", [])
    return _tmpl("troskovnik.html", request, {
        "stavke": stavke,
        "aktivno": "troskovnik",
    })


# ── rute: push notifikacije ───────────────────────────────────────────────────

@router.post("/push/subscribe")
async def push_subscribe(request: Request):
    if not _authed(request):
        return JSONResponse({"ok": False}, status_code=401)
    try:
        body = await request.json()
        sub_json = json.dumps(body)
    except Exception:
        return JSONResponse({"ok": False, "greska": "Neispravan JSON."}, status_code=400)

    radnik_id = request.session["teren_radnik_id"]
    wd.set_push_subscription(radnik_id, sub_json)
    return JSONResponse({"ok": True})


# ── rute: offline sync ────────────────────────────────────────────────────────

@router.post("/unos/sync")
async def unos_sync(request: Request):
    """Jedan offline unos iz localStorage queue-a — parse + save u jednom koraku."""
    if not _authed(request):
        return JSONResponse({"ok": False, "greska": "Nije prijavljen."}, status_code=401)
    try:
        body = await request.json()
        sirova = str(body.get("sirova", "")).strip()
        projekt_key = str(body.get("projekt_key", "")).strip()
    except Exception:
        return JSONResponse({"ok": False, "greska": "Neispravan JSON."}, status_code=400)

    if not sirova or not projekt_key:
        return JSONResponse({"ok": False, "greska": "Nedostaje tekst ili projekt."})

    radnik_id = request.session["teren_radnik_id"]
    radnik_ime = request.session.get("teren_radnik_ime", "")

    try:
        troskovnik = repo.get_troskovnik(projekt_key)
    except Exception:
        troskovnik = None

    try:
        import asyncio
        parsed = await asyncio.to_thread(parse_report, sirova, troskovnik)
    except Exception as e:
        return JSONResponse({"ok": False, "greska": f"Greška parsiranja: {e}"})

    materijali: list[dict[str, Any]] = []
    for m in parsed.materijali or []:
        try:
            kolicina = float(m.get("kolicina") or 0)
        except (TypeError, ValueError):
            kolicina = 0.0
        materijali.append({
            "radnik": radnik_ime, "telegram_id": radnik_id,
            "sifra": str(m.get("sifra_stavke") or ""),
            "opis": str(m.get("opis") or ""),
            "kolicina": kolicina, "jm": str(m.get("jm") or ""),
            "lokacija": parsed.lokacija or "",
            "strujni_krug": str(m.get("strujni_krug") or parsed.strujni_krug or ""),
        })

    try:
        repo.append_izvjestaj(
            projekt_key,
            dnevnik={
                "radnik": radnik_ime, "telegram_id": radnik_id,
                "opis": parsed.opis_rada or "", "lokacija": parsed.lokacija or "",
                "strujni_krug": parsed.strujni_krug or "", "sirova": sirova,
                "msg_id": 0, "datum_rada": parsed.datum_rada or "",
                "vrijeme_rada": parsed.vrijeme_rada or "",
                "sati": parsed.sati,
                "radnici_spomenuti": parsed.radnici_spomenuti or [],
                "problemi": parsed.problemi or [],
                "confidence": parsed.confidence or "",
            },
            materijali=materijali,
        )
    except Exception as e:
        return JSONResponse({"ok": False, "greska": f"Greška spremanja: {e}"})

    return JSONResponse({"ok": True, "msg": "Unos sinkroniziran."})


# ── rute: satnica ─────────────────────────────────────────────────────────────

@router.get("/satnica")
def satnica_get(request: Request):
    if not _authed(request):
        return _redirect_login()
    radnik_id = request.session["teren_radnik_id"]
    sat = wd.get_satnica_radnika(radnik_id)
    return _tmpl("satnica.html", request, {"sat": sat, "aktivno": "satnica"})


# ── rute: vozilo + putni nalozi ───────────────────────────────────────────────

@router.get("/vozilo")
def vozilo_get(request: Request, poruka: str = ""):
    if not _authed(request):
        return _redirect_login()
    radnik_id = request.session["teren_radnik_id"]
    projekt_key = request.session.get("teren_projekt_key", "")
    vozila = wd.list_vozila()
    projekti = wd.get_projekti_za_radnika(radnik_id)
    moji_nalozi = wd.list_putni_nalozi(radnik_id=radnik_id, limit=5)
    default_vozilo_id = request.session.get("teren_default_vozilo_id")
    return _tmpl("vozilo.html", request, {
        "vozila": vozila,
        "projekti": projekti,
        "moji_nalozi": moji_nalozi,
        "poruka": poruka,
        "danas": date.today().isoformat(),
        "aktivan_projekt_key": projekt_key,
        "default_vozilo_id": default_vozilo_id,
        "aktivno": "vozilo",
    })


@router.post("/vozilo/nalog")
def vozilo_nalog_post(
    request: Request,
    vozilo_id: int = Form(...),
    datum: str = Form(""),
    polaziste: str = Form(""),
    odrediste: str = Form(""),
    km_start: str = Form("0"),
    km_kraj: str = Form("0"),
    gorivo_l: str = Form(""),
    gorivo_eur: str = Form(""),
    projekt_key: str = Form(""),
    napomena: str = Form(""),
):
    if not _authed(request):
        return _redirect_login()
    radnik_id = request.session["teren_radnik_id"]

    def _f(s: str) -> float | None:
        try:
            v = float(s.replace(",", "."))
            return v if v > 0 else None
        except Exception:
            return None

    km_s = _f(km_start) or 0.0
    km_k = _f(km_kraj) or 0.0
    if km_k <= km_s:
        poruka = urllib.parse.quote("Km kraj mora biti veći od km start.")
        return RedirectResponse(url=f"/teren/vozilo?poruka={poruka}", status_code=303)

    nalog_datum = date.fromisoformat(datum) if datum else date.today()

    wd.save_putni_nalog(
        radnik_id=radnik_id,
        vozilo_id=vozilo_id,
        datum=nalog_datum,
        projekt_key=projekt_key or None,
        polaziste=polaziste.strip(),
        odrediste=odrediste.strip(),
        km_start=km_s,
        km_kraj=km_k,
        gorivo_l=_f(gorivo_l),
        gorivo_eur=_f(gorivo_eur),
        napomena=napomena.strip() or None,
    )
    poruka = urllib.parse.quote("Putni nalog spremljen.")
    return RedirectResponse(url=f"/teren/vozilo?poruka={poruka}", status_code=303)


@router.get("/vozilo/moji")
def vozilo_moji_get(request: Request):
    if not _authed(request):
        return _redirect_login()
    radnik_id = request.session["teren_radnik_id"]
    nalozi = wd.list_putni_nalozi(radnik_id=radnik_id, limit=50)
    return _tmpl("vozilo_moji.html", request, {
        "nalozi": nalozi,
        "aktivno": "vozilo",
    })


# ── privatno: Telegram obavijest voditelju ────────────────────────────────────

def _obavijesti_admin(draft: dict) -> None:
    """Best-effort Telegram poruka voditelju ako izvještaj sadrži probleme."""
    from config import ADMIN_TELEGRAM_ID, TELEGRAM_BOT_TOKEN
    parsed = draft.get("parsed", {})
    problemi = parsed.get("problemi") or []
    potreban = parsed.get("potreban_materijal") or []
    if not (problemi or potreban):
        return
    lines = [f"📣 {draft.get('radnik_ime','')} ({draft.get('projekt_key','')}) [web]:"]
    for p in problemi:
        lines.append(f"⚠️ Problem: {p}")
    for t in potreban:
        lines.append(f"🚚 Treba materijal: {t}")
    msg = "\n".join(lines)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": ADMIN_TELEGRAM_ID, "text": msg}).encode()
    try:
        urllib.request.urlopen(url, data=data, timeout=5)
    except Exception:
        log.warning("Ne mogu poslati Telegram obavijest voditelju")
