"""Pregled i korekcija negativnih zaduženja za radnika.

Pokreni:  py scripts/fix_negativna_zaliha.py
Skripta prikazuje sve transakcije i trenutno stanje, a zatim nudi brisanje
specifičnih transakcija.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text
from services import db
from services.models import Radnik, SkladisteTransakcija


def run():
    with db.session() as s:
        # Nađi sve radnike
        radnici = s.scalars(select(Radnik)).all()

    print("Radnici u bazi:")
    for r in radnici:
        print(f"  [{r.telegram_id}] {r.ime}")

    tid_str = input("\nUnesite telegram_id radnika čije transakcije pregledati: ").strip()
    try:
        tid = int(tid_str)
    except ValueError:
        print("Nevažeći ID.")
        return

    lok_id = str(tid)

    with db.session() as s:
        rows = s.scalars(
            select(SkladisteTransakcija).where(
                (
                    (SkladisteTransakcija.u_tip == "radnik") &
                    (SkladisteTransakcija.u_id == lok_id)
                ) | (
                    (SkladisteTransakcija.iz_tip == "radnik") &
                    (SkladisteTransakcija.iz_id == lok_id)
                )
            ).order_by(SkladisteTransakcija.id)
        ).all()

        print(f"\nSve transakcije za radnik/{lok_id}:")
        print(f"{'ID':>6}  {'Opis':<30}  {'Kol':>8}  {'JM':<6}  {'Smjer'}")
        print("-" * 70)
        for t in rows:
            if t.u_tip == "radnik" and t.u_id == lok_id:
                smjer = f"← ULAZ  (iz {t.iz_tip}/{t.iz_id})"
                sign = "+"
            else:
                smjer = f"→ IZLAZ (na {t.u_tip}/{t.u_id})"
                sign = "-"
            print(f"{t.id:>6}  {(t.opis or ''):<30}  {sign}{t.kolicina:>7.3f}  {(t.jm or ''):<6}  {smjer}")

    # Izračunaj saldo
    from services import skladiste as skl
    stanje = skl.stanje("radnik", lok_id)
    print(f"\nTrenutno stanje (saldo) za radnik/{lok_id}:")
    if not stanje:
        print("  (prazno)")
    for z in stanje:
        marker = " ⚠️  NEGATIVNO" if z["kolicina"] < 0 else ""
        print(f"  {z['opis']:<30}  {z['kolicina']:>8.3f} {z['jm']}{marker}")

    print()
    del_str = input(
        "Unesite ID transakcija za brisanje (razdvojene zarezom), "
        "ili Enter za izlaz: "
    ).strip()
    if not del_str:
        print("Izlaz bez promjena.")
        return

    ids = [int(x.strip()) for x in del_str.split(",") if x.strip().isdigit()]
    if not ids:
        print("Nema valjanih ID-ova. Izlaz.")
        return

    with db.session() as s:
        obrisano = 0
        for del_id in ids:
            t = s.get(SkladisteTransakcija, del_id)
            if t is None:
                print(f"  Transakcija {del_id} ne postoji — preskačem.")
                continue
            print(f"  Brišem: [{t.id}] {t.opis} {t.kolicina} {t.jm}")
            s.delete(t)
            obrisano += 1
        s.commit()
    print(f"\nObrisano {obrisano} transakcija.")

    # Novo stanje
    stanje2 = skl.stanje("radnik", lok_id)
    print("Novo stanje:")
    if not stanje2:
        print("  (prazno)")
    for z in stanje2:
        print(f"  {z['opis']:<30}  {z['kolicina']:>8.3f} {z['jm']}")


if __name__ == "__main__":
    run()
