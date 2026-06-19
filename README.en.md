# Teren ERP — Telegram bot + web panel for electrical contracting

[Hrvatski](README.md) · **English**

A site-management system for an electrical contracting company: field workers
report their work by **voice or text** through Telegram, and AI turns it into
structured data. Through a **web panel**, the project manager tracks billing,
inventory and quotes, and generates the **construction log and book** (per the
Croatian regulation NN 60/2024) with one click.

> It grew from a simple reporting bot into a mini-ERP: field → catalog →
> inventory → quotes → billing (progress claims) → documents.

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Tech stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration (.env)](#configuration-env)
- [Database](#database)
- [Running](#running)
- [Usage](#usage)
- [Project structure](#project-structure)
- [Cost estimate](#cost-estimate)
- [License](#license)

## Features

**Telegram bot (workers)**
- Voice and text messages → AI extracts work description, materials, hours, location and **electrical circuit**
- Photo of a handwritten report → AI transcription (vision)
- Photo of a delivery note → automatic goods-receipt into inventory
- One-click report confirmation (Correct / Fix / Discard)
- Tasks from the manager (push, reply, snooze, "done")

**Web admin panel (manager)**
- Project overview: billable (with bill of quantities) and "turnkey"
- Bill-of-quantities import (.xls/.xlsx) with AI parsing
- Linking field materials to BoQ items
- Billing and progress claims (cumulative, % complete, contract discount)
- Item catalog + price lists (purchase/sale prices, margin)
- Inventory (receipts, issues, returns, transfers — ledger model)
- Quotes (VAT, statuses, .docx/.pdf export)
- AI assistant (chatbot over the database — "how much cable was used…")
- Generates the **daily log** (date range or whole project) and the **construction book** (per item, with executed quantities broken down by electrical circuit)
- Full-project export to Excel (5 sheets, including the original field messages)

## How it works

```
Worker (Telegram: voice / text / photo)
        │
        ▼
Whisper transcription (if voice)
        │
        ▼
Claude parses into structure (work + materials + location + circuit)
        │
        ▼
Bot: "Correct?" → one-click confirmation
        │
        ▼
PostgreSQL (daily log, materials, inventory…)
        │
        ▼
Web panel (manager): billing, inventory, quotes
        │
        ▼
Documents: construction log + book (.docx/.pdf) + Excel export
```

## Tech stack

- **Python 3.11+**
- **python-telegram-bot** — Telegram bot
- **FastAPI + Jinja2** — web admin panel (server-rendered, no Node.js)
- **PostgreSQL + SQLAlchemy 2.0** — data storage
- **Anthropic Claude** — message parsing, vision, AI assistant (tool-use)
- **OpenAI Whisper** — voice transcription
- **python-docx / openpyxl** — .docx and .xlsx generation

> A legacy **Google Sheets** backend also exists (`DATA_BACKEND=sheets`); the
> recommended, actively developed one is PostgreSQL (`DATA_BACKEND=postgres`).

## Prerequisites

- [Python 3.11+](https://www.python.org/downloads/) (enable "Add Python to PATH" on Windows)
- [PostgreSQL](https://www.postgresql.org/download/) (for the default backend)
- Microsoft Word or LibreOffice — for PDF conversion (optional; .docx always works)

### Keys you need

| Key | Where | Note |
|---|---|---|
| Telegram bot token | [@BotFather](https://t.me/BotFather) → `/newbot` | required |
| Anthropic API key | [console.anthropic.com](https://console.anthropic.com) | required (`sk-ant-…`) |
| OpenAI API key | [platform.openai.com](https://platform.openai.com) | required for voice (`sk-…`) |
| OpenWeatherMap key | [openweathermap.org/api](https://openweathermap.org/api) | optional (weather) |
| Google Service Account | [console.cloud.google.com](https://console.cloud.google.com) | only for the Sheets backend |

## Installation

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

## Configuration (.env)

```bash
cp .env.example .env   # Windows: copy .env.example .env
```

Fill in the keys. Minimum for the PostgreSQL backend:

```ini
TELEGRAM_BOT_TOKEN=...
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
ADMIN_TELEGRAM_ID=...            # your Telegram ID (get it via /id in the bot)
DATA_BACKEND=postgres
DATABASE_URL=postgresql+psycopg://postgres:PASSWORD@localhost:5432/teren_bot
WEB_PASSWORD=                    # empty = no login (local use only)
WEB_SECRET=change-me-to-a-random-string
```

For `ADMIN_TELEGRAM_ID`: temporarily set `0`, start the bot, send it `/id`,
put the returned number in, and restart.

## Database

Create a database in PostgreSQL (e.g. `teren_bot`), then initialize the tables:

```bash
python scripts/init_db.py
```

This creates all tables (`create_all`). To migrate from an old Google Sheets
setup, use `scripts/migrate_sheets_to_db.py`.

## Running

```bash
# Telegram bot
python bot.py

# Web admin panel  →  http://127.0.0.1:8000
python run_web.py
```

> The web panel does not hot-reload code — restart it after changes
> (Ctrl+C, then `python run_web.py` again).

## Usage

### Telegram bot — commands

| Command | Who | Description |
|---|---|---|
| `/start` | all | Greeting + project selection |
| `/id` | all | Return Telegram ID |
| `/projekt` | all | Current active project |
| `/zadaci` | all | Open tasks |
| `/zaduzenja` | all | Inventory / issued items |
| `/noviprojekt` | admin | New-project wizard |
| `/projekti` | admin | List all projects |
| `/dodaj_radnika` | admin | Add a worker to the team |
| `/dnevnik` `/knjiga` | admin | Generate documents |
| `/uvezi_troskovnik` | admin | Import a .xlsx bill of quantities |

After `/start`, a **worker** picks a project and sends messages, e.g.
*"On circuit 9.1 I laid 30 m of fi16 conduit in the basement"* — the bot extracts
the material, quantity, location and circuit, then asks for confirmation.

### Web panel

Open `http://127.0.0.1:8000`. Main pages: Dashboard, Project (billing, progress
claims, tasks, materials, log/book/Excel export), Catalog, Inventory, Quotes,
Assistant.

## Project structure

```
teren-erp/
├── bot.py                  # Telegram bot — entry point
├── run_web.py              # Web panel — entry point
├── config.py               # loads .env
├── requirements.txt
├── handlers/               # Telegram event handlers (start, report, confirm, admin, tasks, inventory)
├── services/               # business logic
│   ├── db.py, db_backend.py, models.py   # PostgreSQL layer (SQLAlchemy)
│   ├── sheets.py, repository.py          # backend abstraction (sheets|postgres)
│   ├── claude_parser.py                  # Claude tool-use (parsing, vision, summaries)
│   ├── transcription.py                  # OpenAI Whisper
│   ├── troskovnik_import.py, situacija_import.py, cjenik_import.py
│   ├── docgen.py                         # construction log & book generator (.docx)
│   ├── excel_export.py                   # project export to .xlsx
│   ├── skladiste.py, ponude.py, zadaci.py
│   └── weather.py
├── web/                    # FastAPI admin panel
│   ├── app.py, data.py, asistent.py, jobs.py
│   └── templates/          # Jinja2 templates
├── scripts/                # CLI tools (init_db, import_*, migrate_*, backfill_*)
└── secrets/                # Google SA JSON (if using Sheets) — NOT in git
```

`.env`, `secrets/`, `data/` and `generated/` are excluded from git (see `.gitignore`).

## Cost estimate

For ~10 workers and ~50 messages per day:

| Item | Monthly |
|---|---|
| OpenAI Whisper | ~€9 |
| Anthropic Claude | ~€10–15 |
| PostgreSQL (local) | €0 |
| **Total** | **~€20–25** |

## License

MIT — free to use and modify.
