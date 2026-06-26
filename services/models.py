"""SQLAlchemy modeli — Postgres shema (Faza 1).

Tablice odgovaraju nekadašnjim Google Sheets tabovima (vidi services/sheets.py
HEADERS). Kolone su snake_case; čitanje ih mapira nazad u Sheets-header ključeve
(npr. 'Opis stavke', 'Ključne riječi') u services/db_backend.py, da docgen i
claude_parser ostanu netaknuti.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Projekt(Base):
    __tablename__ = "projekt"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    naziv: Mapped[str] = mapped_column(String(255), default="")
    adresa: Mapped[str] = mapped_column(String(255), default="")
    investitor: Mapped[str] = mapped_column(String(255), default="")
    izvodac: Mapped[str] = mapped_column(String(255), default="")
    nadzorni: Mapped[str] = mapped_column(String(255), default="")
    broj_dozvole: Mapped[str] = mapped_column(String(255), default="")
    # Legacy (iz Sheets ere) — opcionalno, zadržano radi migracije i poveznice.
    spreadsheet_id: Mapped[str] = mapped_column(String(255), default="")
    spreadsheet_url: Mapped[str] = mapped_column(String(512), default="")
    kreiran: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    aktivan: Mapped[bool] = mapped_column(Boolean, default=True)
    # Ugovorni rabat u % (popust na obračun); 0 = bez rabata.
    rabat_posto: Mapped[float] = mapped_column(Float, default=0.0)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "naziv": self.naziv,
            "adresa": self.adresa,
            "investitor": self.investitor,
            "izvodac": self.izvodac,
            "nadzorni": self.nadzorni,
            "broj_dozvole": self.broj_dozvole,
            "spreadsheet_id": self.spreadsheet_id,
            "spreadsheet_url": self.spreadsheet_url,
            "kreiran": self.kreiran.isoformat() if self.kreiran else "",
            "aktivan": self.aktivan,
            "rabat_posto": self.rabat_posto,
        }


class Radnik(Base):
    __tablename__ = "radnik"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ime: Mapped[str] = mapped_column(String(255), default="")
    kvalifikacija: Mapped[str] = mapped_column(String(255), default="")
    aktivan: Mapped[bool] = mapped_column(Boolean, default=True)
    pin_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)


class ProjektRadnik(Base):
    """Veza radnik ↔ projekt (radnik može biti na više projekata)."""

    __tablename__ = "projekt_radnik"

    projekt_key: Mapped[str] = mapped_column(
        ForeignKey("projekt.key", ondelete="CASCADE"), primary_key=True
    )
    telegram_id: Mapped[int] = mapped_column(
        ForeignKey("radnik.telegram_id", ondelete="CASCADE"), primary_key=True
    )


class TroskovnikStavka(Base):
    __tablename__ = "troskovnik_stavka"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    projekt_key: Mapped[str] = mapped_column(
        ForeignKey("projekt.key", ondelete="CASCADE"), index=True
    )
    redoslijed: Mapped[int] = mapped_column(Integer, default=0)
    sifra: Mapped[str] = mapped_column(String(120), default="")
    sekcija: Mapped[str] = mapped_column(String(255), default="")
    pozicija: Mapped[str] = mapped_column(Text, default="")
    opis: Mapped[str] = mapped_column(Text, default="")
    jm: Mapped[str] = mapped_column(String(40), default="")
    ugovorena_kolicina: Mapped[float | None] = mapped_column(Float, nullable=True)
    jedinicna_cijena: Mapped[float | None] = mapped_column(Float, nullable=True)
    tip: Mapped[str] = mapped_column(String(40), default="stavka")
    kljucne_rijeci: Mapped[str] = mapped_column(Text, default="")
    izvedeno: Mapped[float] = mapped_column(Float, default=0.0)
    # Ručno upisana izvedena količina (override). NULL = koristi automatski zbroj
    # povezanih materijala; postavljena vrijednost ima prednost.
    izvedeno_rucno: Mapped[float | None] = mapped_column(Float, nullable=True)
    razlika: Mapped[str] = mapped_column(String(120), default="")


class DnevnikUnos(Base):
    __tablename__ = "dnevnik_unos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    projekt_key: Mapped[str] = mapped_column(
        ForeignKey("projekt.key", ondelete="CASCADE"), index=True
    )
    datum: Mapped[date] = mapped_column(Date, index=True)
    upisano_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    radnik: Mapped[str] = mapped_column(String(255), default="")
    telegram_id: Mapped[int] = mapped_column(BigInteger, default=0)
    opis: Mapped[str] = mapped_column(Text, default="")
    lokacija: Mapped[str] = mapped_column(String(255), default="")
    vrijeme_rada: Mapped[str] = mapped_column(String(40), default="")
    sati: Mapped[float | None] = mapped_column(Float, nullable=True)
    radnici_spomenuti: Mapped[str] = mapped_column(Text, default="")
    problemi: Mapped[str] = mapped_column(Text, default="")
    sirova: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[str] = mapped_column(String(40), default="")
    telegram_msg_id: Mapped[int] = mapped_column(BigInteger, default=0)
    # Strujni krug/krugovi na koje se rad odnosi (npr. "9.1" ili "9.1, 9.2").
    # Izvlači ga parser iz poruke; prazno ako nije naveden.
    strujni_krug: Mapped[str] = mapped_column(String(120), default="")


class Materijal(Base):
    __tablename__ = "materijal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    projekt_key: Mapped[str] = mapped_column(
        ForeignKey("projekt.key", ondelete="CASCADE"), index=True
    )
    datum: Mapped[date] = mapped_column(Date, index=True)
    vrijeme: Mapped[str] = mapped_column(String(20), default="")
    radnik: Mapped[str] = mapped_column(String(255), default="")
    telegram_id: Mapped[int] = mapped_column(BigInteger, default=0)
    sifra_stavke: Mapped[str] = mapped_column(String(120), default="")
    opis: Mapped[str] = mapped_column(Text, default="")
    kolicina: Mapped[float] = mapped_column(Float, default=0.0)
    jm: Mapped[str] = mapped_column(String(40), default="")
    lokacija: Mapped[str] = mapped_column(String(255), default="")
    napomena: Mapped[str] = mapped_column(Text, default="")
    # Strujni krug na koji se materijal ugrađuje (npr. "9.1"). Prazno ako nije naveden.
    strujni_krug: Mapped[str] = mapped_column(String(60), default="")
    # Eksplicitna veza na stavku troškovnika (za knjigu/obračun izvedenog).
    # NULL dok se ne poveže (ručno/AI u panelu). SET NULL ako se stavka obriše.
    troskovnik_stavka_id: Mapped[int | None] = mapped_column(
        ForeignKey("troskovnik_stavka.id", ondelete="SET NULL"), nullable=True, index=True
    )


class Vrijeme(Base):
    __tablename__ = "vrijeme"
    __table_args__ = (UniqueConstraint("projekt_key", "datum", name="uq_vrijeme_projekt_datum"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    projekt_key: Mapped[str] = mapped_column(
        ForeignKey("projekt.key", ondelete="CASCADE"), index=True
    )
    datum: Mapped[date] = mapped_column(Date, index=True)
    min_temp: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_temp: Mapped[float | None] = mapped_column(Float, nullable=True)
    oborine: Mapped[float | None] = mapped_column(Float, nullable=True)
    opis: Mapped[str] = mapped_column(String(255), default="")


# =============================================================================
# Modul: SITUACIJE (obračun izvedenih radova po stavci troškovnika)
# =============================================================================

class Situacija(Base):
    """Jedna privremena/okončana situacija projekta (1., 2., ...).

    SituacijaStavka čuva KUMULATIVNU izvedenu količinu po stavci u trenutku
    ove situacije (standardna obračunska konvencija). Iznos ove situacije za stavku =
    (kumulativ_ove − kumulativ_prethodne) × jedinična cijena."""

    __tablename__ = "situacija"
    __table_args__ = (UniqueConstraint("projekt_key", "broj", name="uq_situacija_projekt_broj"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    projekt_key: Mapped[str] = mapped_column(
        ForeignKey("projekt.key", ondelete="CASCADE"), index=True
    )
    broj: Mapped[int] = mapped_column(Integer, default=1)
    datum: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="nacrt", index=True)
    # nacrt | ovjerena
    napomena: Mapped[str] = mapped_column(Text, default="")
    izvor_datoteka: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class SituacijaStavka(Base):
    """Snimka kumulativne izvedene količine jedne troškovničke stavke u situaciji."""

    __tablename__ = "situacija_stavka"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    situacija_id: Mapped[int] = mapped_column(
        ForeignKey("situacija.id", ondelete="CASCADE"), index=True
    )
    troskovnik_stavka_id: Mapped[int | None] = mapped_column(
        ForeignKey("troskovnik_stavka.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sifra: Mapped[str] = mapped_column(String(120), default="", index=True)
    opis: Mapped[str] = mapped_column(Text, default="")
    jm: Mapped[str] = mapped_column(String(40), default="")
    kolicina_kumulativ: Mapped[float] = mapped_column(Float, default=0.0)


# =============================================================================
# Modul: ZADACI (voditelj → radnici, dvosmjerno preko bota i panela)
# =============================================================================

class Zadatak(Base):
    """Zadatak koji voditelj zada za projekt; radnik ga vidi/rješava u botu.

    telegram_id = None znači 'svi radnici na projektu'. Odgoda ne mijenja
    status — samo sakrije zadatak do snooze_until (sutra ujutro)."""

    __tablename__ = "zadatak"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    projekt_key: Mapped[str] = mapped_column(
        ForeignKey("projekt.key", ondelete="CASCADE"), index=True
    )
    tekst: Mapped[str] = mapped_column(Text, default="")
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_by: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    rok: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="otvoren", index=True)  # otvoren | gotov
    snooze_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class ZadatakKomentar(Base):
    """Odgovor radnika (Telegram reply) ili voditelja na zadatak."""

    __tablename__ = "zadatak_komentar"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    zadatak_id: Mapped[int] = mapped_column(
        ForeignKey("zadatak.id", ondelete="CASCADE"), index=True
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, default=0)
    ime: Mapped[str] = mapped_column(String(255), default="")
    tekst: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ZadatakPoruka(Base):
    """Mapiranje poslane Telegram poruke → zadatak, da reply na poruku
    znamo pripisati pravom zadatku."""

    __tablename__ = "zadatak_poruka"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    zadatak_id: Mapped[int] = mapped_column(
        ForeignKey("zadatak.id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int] = mapped_column(BigInteger, index=True)


# =============================================================================
# Modul: PONUDE (kupcu; stavke iz kataloga s prodajnim cijenama ili slobodne)
# =============================================================================

class Ponuda(Base):
    __tablename__ = "ponuda"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    broj: Mapped[str] = mapped_column(String(40), default="", index=True)  # P-2026-001
    kupac_naziv: Mapped[str] = mapped_column(String(255), default="")
    kupac_adresa: Mapped[str] = mapped_column(String(255), default="")
    kupac_oib: Mapped[str] = mapped_column(String(20), default="")
    predmet: Mapped[str] = mapped_column(String(512), default="")
    napomena: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="nacrt", index=True)
    # nacrt | poslana | prihvacena | odbijena
    valjanost_dana: Mapped[int] = mapped_column(Integer, default=30)
    datum: Mapped[date] = mapped_column(Date, default=date.today)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class PonudaStavka(Base):
    __tablename__ = "ponuda_stavka"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ponuda_id: Mapped[int] = mapped_column(
        ForeignKey("ponuda.id", ondelete="CASCADE"), index=True
    )
    redoslijed: Mapped[int] = mapped_column(Integer, default=0)
    artikl_id: Mapped[int | None] = mapped_column(
        ForeignKey("artikl.id", ondelete="SET NULL"), nullable=True
    )
    opis: Mapped[str] = mapped_column(String(512), default="")
    jm: Mapped[str] = mapped_column(String(40), default="")
    kolicina: Mapped[float] = mapped_column(Float, default=1.0)
    cijena: Mapped[float | None] = mapped_column(Float, nullable=True)  # jedinična, bez PDV-a


# =============================================================================
# Modul: SKLADIŠTE (ledger transakcija — stanje se izračunava, ne pohranjuje)
# =============================================================================

class SkladisteTransakcija(Base):
    """Jedan ulaz/izlaz materijala. Lokacije: 'dobavljac' | 'skladiste' |
    'radnik' (id = telegram_id) | 'gradiliste' (id = projekt_key).

    Stanje lokacije = Σ(kolicina gdje je u_*) − Σ(kolicina gdje je iz_*).
    artikl_id je NULL za materijal koji (još) nije u katalogu — tada ga
    identificira 'opis'."""

    __tablename__ = "skladiste_transakcija"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tip: Mapped[str] = mapped_column(String(20), default="primka")  # primka | zaduzenje | povrat | korekcija
    artikl_id: Mapped[int | None] = mapped_column(
        ForeignKey("artikl.id", ondelete="SET NULL"), nullable=True, index=True
    )
    opis: Mapped[str] = mapped_column(String(512), default="")
    jm: Mapped[str] = mapped_column(String(40), default="")
    kolicina: Mapped[float] = mapped_column(Float, default=0.0)  # uvijek pozitivna
    iz_tip: Mapped[str] = mapped_column(String(20), default="")
    iz_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    u_tip: Mapped[str] = mapped_column(String(20), default="")
    u_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    dokument: Mapped[str] = mapped_column(String(255), default="")  # npr. "Otpremnica 123/2026"
    napomena: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


# =============================================================================
# Modul: KATALOG + CJENICI (kralježnica firme — artikl + partner)
# Veže se na buduće module: skladište, ponude, situacije/računi.
# =============================================================================

class Partner(Base):
    """Kupci i dobavljači (npr. Inaqua, Zeleni element)."""

    __tablename__ = "partner"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    naziv: Mapped[str] = mapped_column(String(255), index=True)
    tip: Mapped[str] = mapped_column(String(20), default="dobavljac")  # kupac | dobavljac | oboje
    oib: Mapped[str] = mapped_column(String(20), default="")
    adresa: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    telefon: Mapped[str] = mapped_column(String(60), default="")
    aktivan: Mapped[bool] = mapped_column(Boolean, default=True)
    napomena: Mapped[str] = mapped_column(Text, default="")


class Artikl(Base):
    """Katalog materijala/proizvoda — središnja šifrarnik tablica.

    Raste uvozom cjenika i s terena. zargon_aliasi drži hrvatski sleng
    ('petica','trojka','doza') za prepoznavanje u porukama radnika i troškovnicima.
    """

    __tablename__ = "artikl"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sifra: Mapped[str] = mapped_column(String(120), default="", index=True)
    naziv: Mapped[str] = mapped_column(String(512), index=True)
    opis: Mapped[str] = mapped_column(Text, default="")
    jm: Mapped[str] = mapped_column(String(40), default="")
    kategorija: Mapped[str] = mapped_column(String(120), default="", index=True)
    proizvodjac: Mapped[str] = mapped_column(String(255), default="")
    zargon_aliasi: Mapped[str] = mapped_column(Text, default="")
    aktivan: Mapped[bool] = mapped_column(Boolean, default=True)
    treba_pregled: Mapped[bool] = mapped_column(Boolean, default=False)
    napomena: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Cjenik(Base):
    """Jedan uvezeni cjenik (npr. 'Inaqua 2026'). Nabavni (od dobavljača) ili prodajni."""

    __tablename__ = "cjenik"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    partner_id: Mapped[int | None] = mapped_column(
        ForeignKey("partner.id", ondelete="SET NULL"), nullable=True, index=True
    )
    naziv: Mapped[str] = mapped_column(String(255), default="")
    tip: Mapped[str] = mapped_column(String(20), default="nabavni")  # nabavni | prodajni
    valuta: Mapped[str] = mapped_column(String(10), default="EUR")
    datum: Mapped[date | None] = mapped_column(Date, nullable=True)
    aktivan: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class CjenikStavka(Base):
    """Stavka cjenika. artikl_id je NULL dok se ne poveže s katalogom (review)."""

    __tablename__ = "cjenik_stavka"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cjenik_id: Mapped[int] = mapped_column(
        ForeignKey("cjenik.id", ondelete="CASCADE"), index=True
    )
    artikl_id: Mapped[int | None] = mapped_column(
        ForeignKey("artikl.id", ondelete="SET NULL"), nullable=True, index=True
    )
    redoslijed: Mapped[int] = mapped_column(Integer, default=0)
    sifra_dobavljaca: Mapped[str] = mapped_column(String(120), default="")
    naziv: Mapped[str] = mapped_column(String(512), default="")
    jm: Mapped[str] = mapped_column(String(40), default="")
    cijena: Mapped[float | None] = mapped_column(Float, nullable=True)
    rabat: Mapped[float | None] = mapped_column(Float, nullable=True)
    napomena: Mapped[str] = mapped_column(Text, default="")
