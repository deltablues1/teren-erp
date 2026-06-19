"""Quick smoke test - provjeri da su svi API-jevi pristupačni.

Pokreni: python scripts/test_setup.py
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def check_config() -> bool:
    try:
        import config
        print(f"  ✅ Config učitan")
        print(f"     - Telegram token: {config.TELEGRAM_BOT_TOKEN[:10]}...")
        print(f"     - Admin ID: {config.ADMIN_TELEGRAM_ID}")
        print(f"     - Claude model: {config.CLAUDE_MODEL}")
        print(f"     - Folder ID: {config.GOOGLE_SHEETS_FOLDER_ID}")
        return True
    except Exception as e:
        print(f"  ❌ Config greška: {e}")
        return False


def check_sheets() -> bool:
    try:
        from services import sheets
        client = sheets.get_client()
        print(f"  ✅ Google Sheets klijent autenticiran")
        return True
    except Exception as e:
        print(f"  ❌ Google Sheets greška: {e}")
        return False


def check_claude() -> bool:
    try:
        from services import claude_parser
        result = claude_parser.parse_report(
            "Postavio 10 metara peticu u prizemlju"
        )
        print(f"  ✅ Claude API radi")
        print(f"     - Opis: {result.opis_rada}")
        print(f"     - Materijali: {len(result.materijali)}")
        return True
    except Exception as e:
        print(f"  ❌ Claude greška: {e}")
        return False


def check_openai() -> bool:
    try:
        from openai import OpenAI
        from config import OPENAI_API_KEY
        client = OpenAI(api_key=OPENAI_API_KEY)
        models = client.models.list()
        print(f"  ✅ OpenAI API radi")
        return True
    except Exception as e:
        print(f"  ❌ OpenAI greška: {e}")
        return False


def main() -> None:
    print("== Smoke test ==\n")
    print("1) Config:")
    ok_config = check_config()
    if not ok_config:
        print("\nPopravi config prije nastavka.")
        sys.exit(1)

    print("\n2) Google Sheets:")
    ok_sheets = check_sheets()

    print("\n3) Claude API:")
    ok_claude = check_claude()

    print("\n4) OpenAI API (Whisper):")
    ok_openai = check_openai()

    print("\n" + "=" * 40)
    if all([ok_config, ok_sheets, ok_claude, ok_openai]):
        print("✅ Sve radi! Pokreni bota: python bot.py")
    else:
        print("❌ Neki testovi nisu prošli. Provjeri .env i Google Cloud setup.")
        sys.exit(1)


if __name__ == "__main__":
    main()
