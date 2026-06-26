"""FastAPI web admin panel za Teren.

Pokreni:  py run_web.py   →  http://127.0.0.1:8000
Dijeli PostgreSQL bazu i modele s botom.

Napomena: endpointi su namjerno obični `def` (ne `async def`) — rade sinkroni
SQL/docgen posao, pa ih FastAPI vrti u threadpoolu umjesto da blokiraju event loop.
"""
from __future__ import annotations

import html
import logging
import secrets
import shutil
import tempfile
import threading
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from config import ADMIN_TELEGRAM_ID, WEB_PASSWORD, WEB_SECRET
from handlers.zadaci import format_zadatak, keyboard_dict
from services import (
    cjenik_import, docgen, excel_export, ponude as pon, skladiste as skl,
    telegram_push, troskovnik_import, zadaci as zadaci_srv,
)
from web import asistent, data, jobs
from web.teren_routes import router as teren_router

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Teren — admin panel")
app.add_middleware(SessionMiddleware, secret_key=WEB_SECRET)

_static = BASE_DIR / "static"
_static.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static)), name="static")

app.include_router(teren_router)


def _authed(request: Request) -> bool:
    """Prijava je potrebna samo ako je WEB_PASSWORD postavljen."""
    if not WEB_PASSWORD:
        return True
    return bool(request.session.get("auth"))


def _redirect_login() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=303)


def _error(message: str, exc: Exception, status: int = 500) -> HTMLResponse:
    return HTMLResponse(f"{message}: {html.escape(str(exc))}", status_code=status)


def _redir(url: str, poruka: str = "") -> RedirectResponse:
    """Redirect (PRG) s opcionalnom porukom u query stringu (?poruka=…)."""
    if poruka:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}poruka={quote(poruka)}"
    return RedirectResponse(url=url, status_code=303)


def _save_upload(upload: UploadFile) -> Path:
    """Spremi uploadanu datoteku u temp, čuvajući ekstenziju (bitno za dispatch
    .pdf vs .xls/.xlsx u importerima). Pozivatelj briše datoteku."""
    suffix = Path(upload.filename or "").suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(upload.file, tmp)
        return Path(tmp.name)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if _authed(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"greska": ""})


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, lozinka: str = Form("")):
    if WEB_PASSWORD and secrets.compare_digest(lozinka, WEB_PASSWORD):
        request.session["auth"] = True
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"greska": "Pogrešna lozinka."}
    )


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, poruka: str = ""):
    if not _authed(request):
        return _redirect_login()
    return templates.TemplateResponse(request, "dashboard.html", {
        "counts": data.dashboard_counts(),
        "projekti": data.list_projekti(),
        "poruka": poruka,
        "aktivno": "dashboard",
    })


@app.post("/projekt/nova")
def projekt_nova(
    request: Request,
    naziv: str = Form(...),
    adresa: str = Form(""),
    investitor: str = Form(""),
    izvodac: str = Form(""),
    nadzorni: str = Form(""),
    broj_dozvole: str = Form(""),
):
    if not _authed(request):
        return _redirect_login()
    try:
        key = data.create_projekt(
            naziv, adresa=adresa, investitor=investitor, izvodac=izvodac,
            nadzorni=nadzorni, broj_dozvole=broj_dozvole,
        )
    except Exception as e:
        return _redir("/", f"Greška: {e}")
    return _redir(f"/projekt/{key}", f"Gradilište „{naziv.strip()}” kreirano.")


# ----------------------------- asistent -------------------------------------

class _ChatPoruka(BaseModel):
    uloga: str = "korisnik"
    tekst: str = ""


class _ChatZahtjev(BaseModel):
    poruke: list[_ChatPoruka] = []


@app.get("/asistent", response_class=HTMLResponse)
def asistent_page(request: Request):
    if not _authed(request):
        return _redirect_login()
    return templates.TemplateResponse(request, "asistent.html", {"aktivno": "asistent"})


@app.post("/asistent/pitaj")
def asistent_pitaj(zahtjev: _ChatZahtjev, request: Request):
    if not _authed(request):
        return JSONResponse({"odgovor": "Niste prijavljeni."}, status_code=401)
    try:
        odgovor = asistent.odgovori([p.model_dump() for p in zahtjev.poruke])
    except Exception as e:
        return JSONResponse({"odgovor": f"Greška: {e}"}, status_code=500)
    return JSONResponse({"odgovor": odgovor})


def _zadaci_view(projekt_key: str | None = None) -> list[dict]:
    """Otvoreni zadaci s komentarima i imenom primatelja (za templejte)."""
    zadaci = zadaci_srv.list_otvoreni(projekt_key, include_snoozed=True)
    for z in zadaci:
        z["komentari"] = zadaci_srv.list_komentari(z["id"])
        if z["telegram_id"] is None:
            z["za"] = "Svi radnici"
        else:
            z["za"] = zadaci_srv.ime_radnika(z["telegram_id"]) or str(z["telegram_id"])
    return zadaci


@app.get("/projekt/{key}", response_class=HTMLResponse)
def projekt(request: Request, key: str, poruka: str = "", job: str = ""):
    if not _authed(request):
        return _redirect_login()
    p = data.projekt_detail(key)
    if not p:
        return HTMLResponse("Projekt ne postoji.", status_code=404)
    return templates.TemplateResponse(request, "projekt.html", {
        "p": p,
        "zadaci": _zadaci_view(key),
        "radnici": zadaci_srv.primatelji(key),
        "situacije": data.situacije(key),
        "obracun": data.obracun_summary(key),
        "danas": date.today().strftime("%Y-%m-%d"),
        "poruka": poruka,
        "job": job,
        "aktivno": "dashboard",
    })


def _import_troskovnik_worker(job_id: str, key: str, path: Path) -> None:
    """Teče u zasebnoj dretvi: dugotrajan AI uvoz bez blokiranja HTTP zahtjeva.
    Status (napredak/rezultat/greška) ide u `jobs` registar koji panel povlači."""
    try:
        n = troskovnik_import.import_to_sheets(
            key, path,
            progress=lambda done, total: jobs.set_progress(job_id, done, total),
        )
        jobs.finish(job_id, n)
        log.info("Troškovnik uvezen za %s: %d stavki (job %s)", key, n, job_id)
    except Exception as e:  # noqa: BLE001 — zabilježi i prikaži korisniku
        jobs.fail(job_id, str(e))
        log.exception("Uvoz troškovnika pao za %s (job %s)", key, job_id)
    finally:
        path.unlink(missing_ok=True)


@app.post("/projekt/{key}/troskovnik/uvoz")
def troskovnik_uvoz(request: Request, key: str, datoteka: UploadFile = File(...)):
    if not _authed(request):
        return _redirect_login()
    if not data.projekt_detail(key):
        return HTMLResponse("Projekt ne postoji.", status_code=404)
    if not (datoteka.filename or "").strip():
        return _redir(f"/projekt/{key}", "Nije odabrana datoteka.")
    path = _save_upload(datoteka)
    job = jobs.create(key, datoteka.filename or "")
    threading.Thread(
        target=_import_troskovnik_worker, args=(job.id, key, path), daemon=True,
    ).start()
    # Odmah se vrati — uvoz teče u pozadini, panel prati napredak preko ?job=.
    return RedirectResponse(url=f"/projekt/{key}?job={job.id}", status_code=303)


@app.get("/projekt/{key}/troskovnik/status/{job_id}")
def troskovnik_status(request: Request, key: str, job_id: str):
    if not _authed(request):
        return JSONResponse({"greska": "Niste prijavljeni."}, status_code=401)
    job = jobs.get(job_id)
    if not job or job.projekt_key != key:
        return JSONResponse({"status": "nepoznat"}, status_code=404)
    return JSONResponse(job.as_dict())


@app.get("/projekt/{key}/troskovnik", response_class=HTMLResponse)
def troskovnik_page(request: Request, key: str, poruka: str = ""):
    if not _authed(request):
        return _redirect_login()
    naziv = data.projekt_naziv(key)
    if not data.projekt_detail(key):
        return HTMLResponse("Projekt ne postoji.", status_code=404)
    return templates.TemplateResponse(request, "troskovnik.html", {
        "key": key,
        "naziv": naziv,
        "t": data.troskovnik_pregled(key),
        "poruka": poruka,
        "aktivno": "dashboard",
    })


@app.post("/projekt/{key}/troskovnik/stavka/{stavka_id}")
def troskovnik_stavka_update(
    request: Request, key: str, stavka_id: int,
    sifra: str = Form(""), opis: str = Form(""), jm: str = Form(""),
    ugovorena: str = Form(""), cijena: str = Form(""), izvedeno_rucno: str = Form(""),
):
    if not _authed(request):
        return _redirect_login()
    data.update_troskovnik_stavka(
        stavka_id, sifra=sifra, opis=opis, jm=jm, ugovorena=ugovorena, cijena=cijena,
        izvedeno_rucno=izvedeno_rucno,
    )
    return _redir(f"/projekt/{key}/troskovnik", "Stavka spremljena.")


@app.post("/projekt/{key}/troskovnik/dodaj")
def troskovnik_stavka_dodaj(
    request: Request, key: str,
    sifra: str = Form(""), opis: str = Form(""), jm: str = Form(""),
    ugovorena: str = Form(""), cijena: str = Form(""),
):
    if not _authed(request):
        return _redirect_login()
    ok = data.dodaj_troskovnik_stavka(
        key, sifra=sifra, opis=opis, jm=jm, ugovorena=ugovorena, cijena=cijena,
    )
    return _redir(f"/projekt/{key}/troskovnik",
                  "Stavka dodana." if ok else "Opis je obavezan.")


@app.post("/projekt/{key}/troskovnik/stavka/{stavka_id}/obrisi")
def troskovnik_stavka_obrisi(request: Request, key: str, stavka_id: int):
    if not _authed(request):
        return _redirect_login()
    data.obrisi_troskovnik_stavka(stavka_id)
    return _redir(f"/projekt/{key}/troskovnik", "Stavka obrisana.")


@app.get("/projekt/{key}/povezi", response_class=HTMLResponse)
def povezi_page(request: Request, key: str, poruka: str = ""):
    if not _authed(request):
        return _redirect_login()
    p = data.projekt_detail(key)
    if not p:
        return HTMLResponse("Projekt ne postoji.", status_code=404)
    return templates.TemplateResponse(request, "povezi.html", {
        "p": p,
        "grupe": data.materijali_za_povezivanje(key),
        "izbor": data.troskovnik_izbor(key),
        "poruka": poruka,
        "aktivno": "dashboard",
    })


@app.post("/projekt/{key}/povezi/ai")
def povezi_ai(request: Request, key: str):
    """AI batch-prijedlog: vrati {opis: stavka_id} za nepovezane materijale."""
    if not _authed(request):
        return JSONResponse({"greska": "Niste prijavljeni."}, status_code=401)
    try:
        mapping = data.ai_predlozi_veze(key)
    except Exception as e:
        log.exception("AI prijedlog grešaka za %s", key)
        return JSONResponse({"greska": str(e)}, status_code=500)
    return JSONResponse({"prijedlozi": mapping})


@app.post("/projekt/{key}/povezi")
async def povezi_save(request: Request, key: str):
    if not _authed(request):
        return _redirect_login()
    form = await request.form()
    parovi: dict[str, dict[str, str]] = {}
    for fk, fv in form.items():
        if fk.startswith("opis_"):
            parovi.setdefault(fk[5:], {})["opis"] = str(fv)
        elif fk.startswith("veza_"):
            parovi.setdefault(fk[5:], {})["veza"] = str(fv)
    n_grupa = n_red = 0
    for par in parovi.values():
        opis = par.get("opis")
        if opis is None:
            continue
        veza = (par.get("veza") or "").strip()
        sid = int(veza) if veza.isdigit() else None
        try:
            cnt = data.postavi_troskovnik_vezu(key, opis, sid)
        except ValueError:
            continue
        if cnt:
            n_grupa += 1
            n_red += cnt
    return _redir(f"/projekt/{key}/povezi",
                  f"Spremljeno: {n_grupa} stavki materijala ({n_red} unosa).")


@app.post("/projekt/{key}/zadatak")
def zadatak_create(
    request: Request,
    key: str,
    tekst: str = Form(...),
    radnik: str = Form(""),
    rok: str = Form(""),
):
    if not _authed(request):
        return _redirect_login()

    tid = int(radnik) if radnik.strip() else None
    rok_d = date.fromisoformat(rok) if rok.strip() else None
    z = zadaci_srv.create(
        key, tekst, created_by=ADMIN_TELEGRAM_ID, telegram_id=tid, rok=rok_d,
    )

    # push radnicima odmah (best-effort; zadatak postoji i ako push ne prođe)
    if z:
        naziv = data.projekt_naziv(key)
        for r in zadaci_srv.primatelji(key, tid):
            mid = telegram_push.send_message(
                r["telegram_id"], format_zadatak(z, naziv), keyboard_dict(z["id"]),
            )
            if mid:
                zadaci_srv.zabiljezi_poruku(z["id"], r["telegram_id"], mid)

    return RedirectResponse(url=f"/projekt/{key}#zadaci", status_code=303)


@app.post("/zadatak/{zadatak_id}/zatvori")
def zadatak_zatvori(request: Request, zadatak_id: int, back: str = Form("/zadaci")):
    if not _authed(request):
        return _redirect_login()
    zadaci_srv.oznaci_gotovo(zadatak_id, ADMIN_TELEGRAM_ID)
    if not back.startswith("/"):
        back = "/zadaci"
    return RedirectResponse(url=back, status_code=303)


@app.get("/zadaci", response_class=HTMLResponse)
def zadaci_page(request: Request):
    if not _authed(request):
        return _redirect_login()
    zadaci = _zadaci_view()
    nazivi = {p["key"]: p["naziv"] for p in data.list_projekti()}
    gotovi = zadaci_srv.list_nedavno_gotovi()
    for g in gotovi:
        g["za"] = zadaci_srv.ime_radnika(g["completed_by"]) or ""
    return templates.TemplateResponse(request, "zadaci.html", {
        "zadaci": zadaci,
        "gotovi": gotovi,
        "nazivi": nazivi,
        "aktivno": "zadaci",
    })


@app.get("/katalog", response_class=HTMLResponse)
def katalog(request: Request, q: str = "", pregled: int = 0, poruka: str = ""):
    if not _authed(request):
        return _redirect_login()
    artikli, total = data.katalog_search(q, limit=200, pregled=bool(pregled))
    return templates.TemplateResponse(request, "katalog.html", {
        "artikli": artikli,
        "total": total,
        "q": q,
        "pregled": bool(pregled),
        "cjenici": data.list_cjenici(),
        "poruka": poruka,
        "aktivno": "katalog",
    })


@app.post("/katalog/uvoz")
def katalog_uvoz(
    request: Request,
    datoteka: UploadFile = File(...),
    dobavljac: str = Form(...),
    tip: str = Form("nabavni"),
    datum: str = Form(""),
    valuta: str = Form("EUR"),
):
    if not _authed(request):
        return _redirect_login()
    if not dobavljac.strip():
        return _redir("/katalog", "Dobavljač je obavezan.")
    if not (datoteka.filename or "").strip():
        return _redir("/katalog", "Nije odabrana datoteka.")
    d = None
    if datum.strip():
        try:
            d = date.fromisoformat(datum.strip())
        except ValueError:
            return _redir("/katalog", "Neispravan datum (očekujem YYYY-MM-DD).")
    path = _save_upload(datoteka)
    try:
        res = cjenik_import.import_file(
            path, dobavljac=dobavljac.strip(),
            tip=(tip if tip in ("nabavni", "prodajni") else "nabavni"),
            datum=d, valuta=valuta.strip() or "EUR",
        )
    except Exception as e:
        return _redir("/katalog", f"Greška pri uvozu: {e}")
    finally:
        path.unlink(missing_ok=True)
    return _redir(
        "/katalog",
        f"Cjenik „{res['partner']}” uvezen: {res['stavki']} stavki "
        f"({res['novi_artikli']} novih artikala, {res['povezani_postojeci']} povezano).",
    )


@app.get("/artikl/{artikl_id}", response_class=HTMLResponse)
def artikl_view(request: Request, artikl_id: int, spremljeno: int = 0, poruka: str = ""):
    if not _authed(request):
        return _redirect_login()
    a = data.artikl_detail(artikl_id)
    if not a:
        return HTMLResponse("Artikl ne postoji.", status_code=404)
    return templates.TemplateResponse(request, "artikl.html", {
        "a": a, "spremljeno": bool(spremljeno), "poruka": poruka, "aktivno": "katalog",
    })


@app.post("/artikl/{artikl_id}")
def artikl_save(
    request: Request,
    artikl_id: int,
    naziv: str = Form(""),
    jm: str = Form(""),
    kategorija: str = Form(""),
    zargon: str = Form(""),
    proizvodjac: str = Form(""),
    prodajna_cijena: str = Form(""),
    treba_pregled: str = Form(""),
):
    if not _authed(request):
        return _redirect_login()
    data.update_artikl(
        artikl_id, naziv=naziv, jm=jm, kategorija=kategorija, zargon=zargon,
        proizvodjac=proizvodjac, treba_pregled=(treba_pregled == "on"),
    )
    data.set_prodajna_cijena(artikl_id, prodajna_cijena)
    return RedirectResponse(url=f"/artikl/{artikl_id}?spremljeno=1", status_code=303)


@app.get("/pregled", response_class=HTMLResponse)
def pregled(request: Request):
    if not _authed(request):
        return _redirect_login()
    return templates.TemplateResponse(request, "pregled.html", {
        "stavke": data.unmatched_materijali(),
        "aktivno": "pregled",
    })


@app.post("/pregled/dodaj")
def pregled_dodaj(request: Request, opis: str = Form(...), jm: str = Form("")):
    if not _authed(request):
        return _redirect_login()
    data.dodaj_u_katalog(opis, jm)
    return RedirectResponse(url="/pregled", status_code=303)


@app.post("/artikl/{artikl_id}/ukloni-materijale")
def artikl_ukloni_materijale(request: Request, artikl_id: int):
    if not _authed(request):
        return _redirect_login()
    n = data.ukloni_iz_kataloga(artikl_id)
    msg = f"Razvezano {n} materijala — pojavljuju se opet na stranici Za pregled." if n else "Nema vezanih materijala."
    return _redir(f"/artikl/{artikl_id}", msg)


@app.post("/artikl/{artikl_id}/obrisi")
def artikl_obrisi(request: Request, artikl_id: int):
    if not _authed(request):
        return _redirect_login()
    ok = data.obrisi_artikl(artikl_id)
    return _redir("/katalog", "Artikl obrisan iz kataloga." if ok else "Artikl nije pronađen.")


@app.post("/pregled/vrati")
def pregled_vrati(request: Request, projekt_key: str = Form(...), opis: str = Form(...)):
    """Makni katalog šifru I troškovnik vezu s materijala — pojavi se na /pregled."""
    if not _authed(request):
        return _redirect_login()
    n = data.vrati_na_pregled_po_opisu(projekt_key, opis)
    return _redir("/pregled", f"Materijal vraćen na pregled ({n} zapisa)." if n else "Nije nađen.")


@app.get("/projekt/{key}/obracun", response_class=HTMLResponse)
def obracun_page(request: Request, key: str, poruka: str = ""):
    if not _authed(request):
        return _redirect_login()
    ob = data.obracun_po_stavkama(key)
    if not ob["stavke"] and not ob["uk_ugovoreno"]:
        return _redir(f"/projekt/{key}", "Projekt nema troškovnik ili nema izvedenih materijala.")
    return templates.TemplateResponse(request, "obracun.html", {
        "ob": ob,
        "poruka": poruka,
        "aktivno": "dashboard",
    })


@app.post("/troskovnik/{stavka_id}/izvedeno")
def troskovnik_izvedeno_save(
    request: Request,
    stavka_id: int,
    izvedeno_rucno: str = Form(""),
    back: str = Form("/"),
):
    if not _authed(request):
        return _redirect_login()
    key = data.set_izvedeno_rucno(stavka_id, izvedeno_rucno)
    url = back if back.startswith("/projekt/") else (f"/projekt/{key}/obracun" if key else "/")
    return _redir(url, "Izvedeno ažurirano.")


@app.get("/pregled/spoji", response_class=HTMLResponse)
def pregled_spoji_form(request: Request, opis: str, jm: str = "", q: str = ""):
    if not _authed(request):
        return _redirect_login()
    artikli, _ = data.katalog_search(q or opis, limit=30)
    return templates.TemplateResponse(request, "pregled_spoji.html", {
        "opis": opis, "jm": jm, "q": q, "artikli": artikli, "aktivno": "pregled",
    })


@app.post("/pregled/spoji")
def pregled_spoji_save(request: Request, opis: str = Form(...), artikl_id: int = Form(...)):
    if not _authed(request):
        return _redirect_login()
    data.spoji_na_artikl(opis, artikl_id, uci_zargon=True)
    return RedirectResponse(url="/pregled", status_code=303)


# ----------------------------- skladište -----------------------------------

def _num_form(v: str) -> float:
    """'12,5' ili '12.5' → float; 0 ako nije broj."""
    try:
        return float(str(v).replace(",", ".").strip())
    except (TypeError, ValueError):
        return 0.0


def _parse_lok(v: str) -> tuple[str, str]:
    """'radnik:123' / 'gradiliste:kuca_horvat' / 'skladiste' → (tip, id)."""
    tip, _, lid = (v or "").partition(":")
    return tip, lid


@app.get("/skladiste", response_class=HTMLResponse)
def skladiste_page(request: Request, greska: str = ""):
    if not _authed(request):
        return _redirect_login()
    return templates.TemplateResponse(request, "skladiste.html", {
        "stanje": skl.stanje(),
        "zaduzenja": skl.zaduzenja_pregled(),
        "promet": skl.promet(25),
        "radnici": skl.list_radnici_svi(),
        "projekti": data.list_projekti(),
        "artikli_nazivi": skl.list_artikli_nazivi(),
        "greska": greska,
        "aktivno": "skladiste",
    })


@app.post("/skladiste/primka")
def skladiste_primka(
    request: Request,
    artikl: str = Form(...),
    kolicina: str = Form(...),
    dobavljac: str = Form(""),
    dokument: str = Form(""),
    na: str = Form("skladiste"),
):
    if not _authed(request):
        return _redirect_login()
    q = _num_form(kolicina)
    if q <= 0:
        return RedirectResponse(url="/skladiste?greska=Količina mora biti broj veći od 0.", status_code=303)
    na_tip, na_id = _parse_lok(na)
    skl.primka(
        artikl, q, dobavljac=dobavljac, dokument=dokument,
        na=(na_tip or "skladiste", na_id), created_by=ADMIN_TELEGRAM_ID,
    )
    return RedirectResponse(url="/skladiste", status_code=303)


@app.post("/skladiste/zaduzi")
def skladiste_zaduzi(
    request: Request,
    artikl: str = Form(...),
    kolicina: str = Form(...),
    kome: str = Form(...),
    napomena: str = Form(""),
):
    if not _authed(request):
        return _redirect_login()
    q = _num_form(kolicina)
    na_tip, na_id = _parse_lok(kome)
    if q <= 0 or na_tip not in ("radnik", "gradiliste"):
        return RedirectResponse(url="/skladiste?greska=Provjeri količinu i kome se zadužuje.", status_code=303)
    skl.zaduzi(artikl, q, na_tip=na_tip, na_id=na_id, napomena=napomena,
               created_by=ADMIN_TELEGRAM_ID)
    return RedirectResponse(url="/skladiste", status_code=303)


@app.post("/skladiste/povrat")
def skladiste_povrat(
    request: Request,
    artikl: str = Form(...),
    kolicina: str = Form(...),
    od: str = Form(...),
    napomena: str = Form(""),
):
    if not _authed(request):
        return _redirect_login()
    q = _num_form(kolicina)
    od_tip, od_id = _parse_lok(od)
    if q <= 0 or od_tip not in ("radnik", "gradiliste"):
        return RedirectResponse(url="/skladiste?greska=Provjeri količinu i od koga je povrat.", status_code=303)
    skl.povrat(artikl, q, od_tip=od_tip, od_id=od_id, napomena=napomena,
               created_by=ADMIN_TELEGRAM_ID)
    return RedirectResponse(url="/skladiste", status_code=303)


@app.post("/skladiste/prijenos")
def skladiste_prijenos(
    request: Request,
    artikl: str = Form(...),
    kolicina: str = Form(...),
    od: str = Form(...),
    kome: str = Form(...),
    napomena: str = Form(""),
):
    if not _authed(request):
        return _redirect_login()
    q = _num_form(kolicina)
    od_tip, od_id = _parse_lok(od)
    na_tip, na_id = _parse_lok(kome)
    if q <= 0 or od_tip not in ("radnik", "gradiliste") or na_tip not in ("radnik", "gradiliste"):
        return RedirectResponse(url="/skladiste?greska=Provjeri količinu, od koga i kome.", status_code=303)
    if (od_tip, od_id) == (na_tip, na_id):
        return RedirectResponse(url="/skladiste?greska=Od i kome ne mogu biti isti.", status_code=303)
    skl.prijenos(artikl, q, od_tip=od_tip, od_id=od_id, na_tip=na_tip, na_id=na_id,
                 napomena=napomena, created_by=ADMIN_TELEGRAM_ID)
    return RedirectResponse(url="/skladiste", status_code=303)


# ----------------------------- ponude ---------------------------------------

@app.get("/ponude", response_class=HTMLResponse)
def ponude_page(request: Request):
    if not _authed(request):
        return _redirect_login()
    return templates.TemplateResponse(request, "ponude.html", {
        "ponude": pon.list_ponude(),
        "aktivno": "ponude",
    })


@app.post("/ponude/nova")
def ponuda_nova(request: Request, kupac: str = Form(...), predmet: str = Form("")):
    if not _authed(request):
        return _redirect_login()
    pid = pon.create(kupac, predmet)
    if not pid:
        return RedirectResponse(url="/ponude", status_code=303)
    return RedirectResponse(url=f"/ponuda/{pid}", status_code=303)


@app.get("/ponuda/{ponuda_id}", response_class=HTMLResponse)
def ponuda_view(request: Request, ponuda_id: int):
    if not _authed(request):
        return _redirect_login()
    p = pon.get(ponuda_id)
    if not p:
        return HTMLResponse("Ponuda ne postoji.", status_code=404)
    return templates.TemplateResponse(request, "ponuda.html", {
        "p": p,
        "statusi": pon.STATUSI,
        "pdv_stopa": pon.PDV_STOPA,
        "artikli_nazivi": skl.list_artikli_nazivi(),
        "aktivno": "ponude",
    })


@app.post("/ponuda/{ponuda_id}/header")
def ponuda_header(
    request: Request, ponuda_id: int,
    kupac_naziv: str = Form(""), kupac_adresa: str = Form(""),
    kupac_oib: str = Form(""), predmet: str = Form(""),
    napomena: str = Form(""), valjanost_dana: str = Form("30"),
):
    if not _authed(request):
        return _redirect_login()
    try:
        valjanost = int(valjanost_dana)
    except ValueError:
        valjanost = 30
    pon.update_header(
        ponuda_id, kupac_naziv=kupac_naziv, kupac_adresa=kupac_adresa,
        kupac_oib=kupac_oib, predmet=predmet, napomena=napomena,
        valjanost_dana=valjanost,
    )
    return RedirectResponse(url=f"/ponuda/{ponuda_id}", status_code=303)


@app.post("/ponuda/{ponuda_id}/status")
def ponuda_status(request: Request, ponuda_id: int, status: str = Form(...)):
    if not _authed(request):
        return _redirect_login()
    pon.set_status(ponuda_id, status)
    return RedirectResponse(url=f"/ponuda/{ponuda_id}", status_code=303)


@app.post("/ponuda/{ponuda_id}/stavka")
def ponuda_stavka(
    request: Request, ponuda_id: int,
    artikl: str = Form(...), kolicina: str = Form("1"), cijena: str = Form(""),
):
    if not _authed(request):
        return _redirect_login()
    c = _num_form(cijena) if cijena.strip() else None
    pon.dodaj_stavku(ponuda_id, artikl, _num_form(kolicina) or 1.0, cijena=c)
    return RedirectResponse(url=f"/ponuda/{ponuda_id}", status_code=303)


@app.post("/ponuda/stavka/{stavka_id}/update")
def ponuda_stavka_update(
    request: Request, stavka_id: int,
    ponuda_id: int = Form(...), kolicina: str = Form("1"), cijena: str = Form(""),
):
    if not _authed(request):
        return _redirect_login()
    c = _num_form(cijena) if cijena.strip() else None
    pon.update_stavku(stavka_id, _num_form(kolicina), c)
    return RedirectResponse(url=f"/ponuda/{ponuda_id}", status_code=303)


@app.post("/ponuda/stavka/{stavka_id}/obrisi")
def ponuda_stavka_obrisi(request: Request, stavka_id: int):
    if not _authed(request):
        return _redirect_login()
    pid = pon.obrisi_stavku(stavka_id)
    return RedirectResponse(url=f"/ponuda/{pid}" if pid else "/ponude", status_code=303)


@app.get("/ponuda/{ponuda_id}/docx")
def ponuda_docx(request: Request, ponuda_id: int, pdf: int = 0):
    if not _authed(request):
        return _redirect_login()
    p = pon.get(ponuda_id)
    if not p:
        return HTMLResponse("Ponuda ne postoji.", status_code=404)
    try:
        path = docgen.generate_ponuda(p)
    except Exception as e:
        return _error("Greška pri generiranju ponude", e)
    if pdf:
        pdf_path = docgen.to_pdf(path)
        if pdf_path and pdf_path.exists():
            return FileResponse(str(pdf_path), filename=pdf_path.name,
                                media_type="application/pdf")
        return HTMLResponse(
            "PDF konverzija nije uspjela (treba MS Word) — preuzmi .docx.",
            status_code=500,
        )
    return FileResponse(
        str(path), filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.get("/projekt/{key}/izvoz")
def gen_izvoz(request: Request, key: str):
    """Izvezi cijeli projekt u .xlsx (5 listova, kao stari Google Sheets dokument)."""
    if not _authed(request):
        return _redirect_login()
    try:
        path = excel_export.export_projekt(key)
    except Exception as e:
        return _error("Greška pri izvozu projekta", e)
    return FileResponse(
        str(path), filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/projekt/{key}/dnevnik")
def gen_dnevnik(
    request: Request, key: str, od: str = "", do: str = "",
    sve: int = 0, datum: str = "",
):
    """Generira dnevnik za raspon [od, do]. sve=1 → cijeli projekt.
    `datum` je zadržan radi starih linkova (jedan dan)."""
    if not _authed(request):
        return _redirect_login()
    if sve:
        od_arg, do_arg = None, None
    elif datum:
        od_arg = do_arg = datum
    else:
        od_arg = od or date.today().strftime("%Y-%m-%d")
        do_arg = do or od_arg
    try:
        path = docgen.generate_dnevnik(key, od_arg, do_arg)
    except Exception as e:
        return _error("Greška pri generiranju dnevnika", e)
    return FileResponse(
        str(path), filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.get("/projekt/{key}/knjiga")
def gen_knjiga(request: Request, key: str, situacija: int = 0):
    if not _authed(request):
        return _redirect_login()
    try:
        path = docgen.generate_knjiga(key, situacija or None, None)
    except Exception as e:
        return _error("Greška pri generiranju knjige", e)
    return FileResponse(
        str(path), filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


# ── Admin: PIN management za terenske radnike ─────────────────────────────────

@app.get("/radnici", response_class=HTMLResponse)
def radnici_prikaz(request: Request, poruka: str = ""):
    if not _authed(request):
        return _redirect_login()
    radnici, svi_projekti = data.list_radnici_detalji()
    return templates.TemplateResponse(request, "radnici.html", {
        "radnici": radnici,
        "svi_projekti": svi_projekti,
        "poruka": poruka,
        "aktivno": "radnici",
    })


@app.post("/radnik/dodaj")
def radnik_dodaj(
    request: Request,
    telegram_id: str = Form(""),
    ime: str = Form(""),
    kvalifikacija: str = Form(""),
):
    if not _authed(request):
        return _redirect_login()
    ime = ime.strip()
    if not ime:
        return _redir("/radnici", "Ime je obavezno.")
    try:
        tid = int(telegram_id.strip())
    except ValueError:
        return _redir("/radnici", "Telegram ID mora biti broj.")
    ok = data.dodaj_radnika(tid, ime, kvalifikacija)
    if not ok:
        return _redir("/radnici", f"Radnik s ID {tid} već postoji.")
    return _redir("/radnici", f"Radnik '{ime}' dodan.")


@app.post("/radnik/{telegram_id}/pin")
def radnik_postavi_pin(request: Request, telegram_id: int, pin: str = Form("")):
    if not _authed(request):
        return _redirect_login()
    import hashlib
    pin = pin.strip()
    if not pin:
        return _redir("/radnici", "PIN ne smije biti prazan.")
    if not pin.isdigit() or len(pin) < 4:
        return _redir("/radnici", "PIN mora biti broj od najmanje 4 znamenke.")
    pin_hash = hashlib.sha256(pin.encode()).hexdigest()
    ok = data.postavi_pin(telegram_id, pin_hash)
    if not ok:
        return _redir("/radnici", f"Radnik {telegram_id} nije pronađen.")
    return _redir("/radnici", "PIN uspješno postavljen.")


@app.post("/radnik/{telegram_id}/pin-obrisi")
def radnik_obrisi_pin(request: Request, telegram_id: int):
    if not _authed(request):
        return _redirect_login()
    data.postavi_pin(telegram_id, None)
    return _redir("/radnici", "PIN uklonjen.")


@app.post("/radnik/{telegram_id}/projekt-dodaj")
def radnik_projekt_dodaj(request: Request, telegram_id: int, projekt_key: str = Form("")):
    if not _authed(request):
        return _redirect_login()
    if not projekt_key:
        return _redir("/radnici", "Odaberite projekt.")
    data.dodaj_projekt_radnika(telegram_id, projekt_key)
    return _redir("/radnici", "Radnik dodijeljen projektu.")


@app.post("/radnik/{telegram_id}/projekt-ukloni/{projekt_key}")
def radnik_projekt_ukloni(request: Request, telegram_id: int, projekt_key: str):
    if not _authed(request):
        return _redirect_login()
    data.ukloni_projekt_radnika(telegram_id, projekt_key)
    return _redir("/radnici", "Radnik uklonjen s projekta.")
