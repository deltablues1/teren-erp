# Teren ERP — Telegram bot + web panel za elektroinstalaterske radove

**Hrvatski** · [English](README.en.md)

Sustav za vođenje gradilišta elektroinstalaterske firme: radnici s terena javljaju
radove **glasom ili tekstom** preko Telegrama, a AI to pretvara u strukturirane
podatke. Voditelj kroz **web panel** prati obračun, skladište i ponude te jednim
klikom generira **građevinski dnevnik i knjigu** po Pravilniku NN 60/2024.

> Iz jednostavnog bota za izvještaje prerastao je u mini-ERP: teren → katalog →
> skladište → ponude → obračun (situacije) → dokumenti.

## Sadržaj

- [Mogućnosti](#mogućnosti)
- [Kako radi](#kako-radi)
- [Tehnologije](#tehnologije)
- [Preduvjeti](#preduvjeti)
- [Instalacija](#instalacija)
- [Konfiguracija (.env)](#konfiguracija-env)
- [Baza podataka](#baza-podataka)
- [Pokretanje](#pokretanje)
- [Korištenje](#korištenje)
- [Struktura projekta](#struktura-projekta)
- [Procjena troškova](#procjena-troškova)
- [Licenca](#licenca)

## Mogućnosti

**Telegram bot (radnici)**
- Glasovne i tekstualne poruke → AI parsira opis rada, materijale, sate, lokaciju i **strujni krug**
- Slika rukom pisanog izvještaja → AI prijepis (vision)
- Slika otpremnice → automatska primka u skladište
- Potvrda izvještaja jednim klikom (Točno / Ispravi / Odbaci)
- Zadaci od voditelja (push, odgovor, odgoda, „gotovo")

**Web admin panel (voditelj)**
- Pregled projekata: obračunski (s troškovnikom) i „ključ u ruke"
- Uvoz troškovnika (.xls/.xlsx) uz AI parsiranje
- Povezivanje materijala s terena na troškovničke pozicije
- Obračun i situacije (kumulativ, postotak izvedenosti, ugovorni rabat)
- Katalog artikala + cjenici (nabavne/prodajne cijene, marža)
- Skladište (primke, zaduženja, povrati, prijenosi — ledger model)
- Ponude (PDV, statusi, .docx/.pdf izvoz)
- AI asistent (chatbot nad bazom — „koliko kabela je utrošeno…")
- Generiranje **dnevnika** (raspon datuma ili cijeli projekt) i **knjige** (po stavci, s razradom izvedenog po strujnim krugovima)
- Izvoz cijelog projekta u Excel (5 listova, uključujući originalne poruke s terena)

## Kako radi

```
Radnik (Telegram: glas / tekst / slika)
        │
        ▼
Whisper transkripcija (ako je glas)
        │
        ▼
Claude parsira u strukturu (rad + materijali + lokacija + strujni krug)
        │
        ▼
Bot: „Točno?" → potvrda jednim klikom
        │
        ▼
PostgreSQL (dnevnik, materijali, skladište…)
        │
        ▼
Web panel (voditelj): obračun, skladište, ponude
        │
        ▼
Dokumenti: građevinski dnevnik + knjiga (.docx/.pdf) + Excel izvoz
```

## Tehnologije

- **Python 3.11+**
- **python-telegram-bot** — Telegram bot
- **FastAPI + Jinja2** — web admin panel (server-rendered, bez Node.js)
- **PostgreSQL + SQLAlchemy 2.0** — pohrana podataka
- **Anthropic Claude** — parsiranje poruka, vision, AI asistent (tool-use)
- **OpenAI Whisper** — transkripcija glasovnih poruka
- **python-docx / openpyxl** — generiranje .docx i .xlsx dokumenata

> Postoji i stariji **Google Sheets** backend (`DATA_BACKEND=sheets`); preporučeni
> i aktivno razvijani je PostgreSQL (`DATA_BACKEND=postgres`).

## Preduvjeti

- [Python 3.11+](https://www.python.org/downloads/) (tijekom instalacije uključi „Add Python to PATH")
- [PostgreSQL](https://www.postgresql.org/download/) (za zadani backend)
- Microsoft Word ili LibreOffice — za PDF konverziju (opcionalno; .docx je uvijek dostupan)

### Ključevi koje trebaš nabaviti

| Ključ | Gdje | Napomena |
|---|---|---|
| Telegram bot token | [@BotFather](https://t.me/BotFather) → `/newbot` | obavezno |
| Anthropic API ključ | [console.anthropic.com](https://console.anthropic.com) | obavezno (`sk-ant-…`) |
| OpenAI API ključ | [platform.openai.com](https://platform.openai.com) | obavezno za glas (`sk-…`) |
| OpenWeatherMap ključ | [openweathermap.org/api](https://openweathermap.org/api) | opcionalno (vremenske prilike) |
| Google Service Account | [console.cloud.google.com](https://console.cloud.google.com) | samo ako koristiš Sheets backend |

## Instalacija

```bash
git clone https://github.com/deltablues1/teren-erp.git
cd teren-erp

python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
```

## Konfiguracija (.env)

```bash
cp .env.example .env   # Windows: copy .env.example .env
```

Popuni ključeve. Minimum za PostgreSQL backend:

```ini
TELEGRAM_BOT_TOKEN=...
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
ADMIN_TELEGRAM_ID=...            # tvoj Telegram ID (saznaj preko /id u botu)
DATA_BACKEND=postgres
DATABASE_URL=postgresql+psycopg://postgres:LOZINKA@localhost:5432/teren_bot
WEB_PASSWORD=                    # prazno = bez prijave (samo lokalno)
WEB_SECRET=promijeni-me-u-nasumičan-niz
```

Za `ADMIN_TELEGRAM_ID`: privremeno stavi `0`, pokreni bota, pošalji mu `/id`,
upiši dobiveni broj i restartaj.

## Baza podataka

Kreiraj bazu u PostgreSQL-u (npr. `teren_bot`) pa inicijaliziraj tablice:

```bash
python scripts/init_db.py
```

Ovo kreira sve tablice (`create_all`). Za uvoz iz starog Google Sheets postava
postoji `scripts/migrate_sheets_to_db.py`.

## Pokretanje

```bash
# Telegram bot
python bot.py

# Web admin panel  →  http://127.0.0.1:8000
python run_web.py
```

> Web panel ne učitava promjene koda u hodu — nakon izmjena ga restartaj
> (Ctrl+C pa ponovno `python run_web.py`).

## Korištenje

### Telegram bot — komande

| Komanda | Tko | Opis |
|---|---|---|
| `/start` | svi | Pozdrav + odabir projekta |
| `/id` | svi | Vrati Telegram ID |
| `/projekt` | svi | Trenutni aktivni projekt |
| `/zadaci` | svi | Otvoreni zadaci |
| `/zaduzenja` | svi | Stanje skladišta / zaduženja |
| `/noviprojekt` | admin | Wizard za novi projekt |
| `/projekti` | admin | Lista svih projekata |
| `/dodaj_radnika` | admin | Dodaj radnika u tim |
| `/dnevnik` `/knjiga` | admin | Generiraj dokumente |
| `/uvezi_troskovnik` | admin | Učitaj .xlsx troškovnik |

**Radnik** nakon `/start` bira projekt i šalje poruke, npr.
*„Na strujnom krugu 9.1 postavio sam 30 m cijevi fi16 u podrumu"* — bot izvuče
materijal, količinu, lokaciju i strujni krug, pa traži potvrdu.

### Web panel

Otvori `http://127.0.0.1:8000`. Glavne stranice: Dashboard, Projekt (obračun,
situacije, zadaci, materijali, dnevnik/knjiga/Excel izvoz), Katalog, Skladište,
Ponude, Asistent.

## Struktura projekta

```
teren-erp/
├── bot.py                  # Telegram bot — entry point
├── run_web.py              # Web panel — entry point
├── config.py               # učitavanje .env
├── requirements.txt
├── handlers/               # Telegram event handleri (start, report, confirm, admin, zadaci, skladiste)
├── services/               # poslovna logika
│   ├── db.py, db_backend.py, models.py   # PostgreSQL sloj (SQLAlchemy)
│   ├── sheets.py, repository.py          # apstrakcija backenda (sheets|postgres)
│   ├── claude_parser.py                  # Claude tool-use (parsiranje, vision, sažeci)
│   ├── transcription.py                  # OpenAI Whisper
│   ├── troskovnik_import.py, situacija_import.py, cjenik_import.py
│   ├── docgen.py                         # generator dnevnika i knjige (.docx)
│   ├── excel_export.py                   # izvoz projekta u .xlsx
│   ├── skladiste.py, ponude.py, zadaci.py
│   └── weather.py
├── web/                    # FastAPI admin panel
│   ├── app.py, data.py, asistent.py, jobs.py
│   └── templates/          # Jinja2 predlošci
├── scripts/                # CLI alati (init_db, import_*, migrate_*, backfill_*)
└── secrets/                # Google SA JSON (ako se koristi Sheets) — NIJE u gitu
```

`.env`, `secrets/`, `data/` i `generated/` su izvan gita (vidi `.gitignore`).

## Procjena troškova

Za ~10 radnika i ~50 poruka dnevno:

| Stavka | Mjesečno |
|---|---|
| OpenAI Whisper | ~9 € |
| Anthropic Claude | ~10–15 € |
| PostgreSQL (lokalno) | 0 € |
| **Ukupno** | **~20–25 €** |

## Licenca

MIT — slobodno koristi i mijenjaj.
