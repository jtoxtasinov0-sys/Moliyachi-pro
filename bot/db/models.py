"""Ma'lumotlar bazasi jadvallari.

Har bir ega — alohida kabinet: ishchilar, modellar, sozlamalar, ish yozuvlari,
shaxsiy xarajatlar va qarzlar egasining id si (owner_id) bilan ajratiladi.
Bir ega boshqa eganing hech narsasini ko'rmaydi.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# --- Umumiy -----------------------------------------------------------------

class Role:
    OWNER = "owner"
    WORKER = "worker"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(10), default=Role.WORKER)
    # Ishchi qaysi egaga tegishli (egada — bo'sh)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    # Ishchi (yoki bosh admin qo'shgan ega) taklif havolasi orqali ulanmaguncha shu token saqlanadi
    invite_token: Mapped[str | None] = mapped_column(String(32), unique=True)
    # Faqat egada (bosh admin qo'shgan): tarif (bot/billing.py PLANS) va to'langan oxirgi kun.
    # Bo'sh — cheklovsiz / muddatsiz
    plan: Mapped[str | None] = mapped_column(String(20))
    paid_until: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def is_owner(self) -> bool:
        return self.role == Role.OWNER

    @property
    def account_id(self) -> int:
        """Kabinet: egada — o'zi, ishchida — uning egasi."""
        return self.id if self.is_owner else self.owner_id


class ItemModel(Base):
    """Mahsulot modeli (masalan, "A-12"). Narx — bir dona uchun ish haqi."""

    __tablename__ = "item_models"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="UZS")
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Alias(Base):
    """Ovozdan noto'g'ri chiqqan ismlarni eslab qolish: "alishir" -> Alisher."""

    __tablename__ = "aliases"
    __table_args__ = (UniqueConstraint("owner_id", "entity_type", "alias"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(20))  # worker | model | client
    entity_id: Mapped[int]
    alias: Mapped[str] = mapped_column(String(100))


class Setting(Base):
    __tablename__ = "settings"

    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Usage(Base):
    """Kabinetning oylik sarfi: AI so'rovlar, rasmlar, ovoz va API xarajati."""

    __tablename__ = "usage"

    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    month: Mapped[str] = mapped_column(String(7), primary_key=True)  # YYYY-MM
    requests: Mapped[int] = mapped_column(default=0)
    images: Mapped[int] = mapped_column(default=0)
    voice_sec: Mapped[int] = mapped_column(default=0)
    # Claude va ElevenLabs xarajati, dollarning milliondan bir ulushida
    cost_micro: Mapped[int] = mapped_column(BigInteger, default=0)


# --- 1. Ishchilar -----------------------------------------------------------

class EntryStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class WorkEntry(TimestampMixin, Base):
    __tablename__ = "work_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("item_models.id"))
    quantity: Mapped[int]
    work_date: Mapped[date] = mapped_column(Date, index=True)
    # Saqlangan paytdagi narx: model narxi keyin o'zgarsa, eski hisob buzilmaydi
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    price_currency: Mapped[str | None] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(10), default=EntryStatus.APPROVED, index=True)
    raw_text: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    worker: Mapped[User] = relationship(foreign_keys=[worker_id], lazy="joined")
    model: Mapped[ItemModel] = relationship(lazy="joined")


# --- 2. Mijozlar: optom savdo ---------------------------------------------

class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(30))
    note: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClientKind:
    SALE = "sale"  # mijozga mol berildi — u qarzdor bo'ladi
    PAYMENT = "payment"  # mijoz pul to'ladi
    RETURN = "return"  # mijoz mol qaytardi


class PriceUnit:
    SET = "set"  # bir komplekt narxi
    PIECE = "piece"  # bir dona narxi


class ClientEntry(TimestampMixin, Base):
    """Mijoz bilan oldi-berdi. Qoldiq = mol (sale) − to'lov (payment) − qaytgan mol (return):
    musbat — mijoz bizga qarzdor."""

    __tablename__ = "client_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    kind: Mapped[str] = mapped_column("direction", String(10))  # ClientKind
    model_id: Mapped[int | None] = mapped_column(ForeignKey("item_models.id"))
    product: Mapped[str | None] = mapped_column(String(100))
    sets: Mapped[int | None] = mapped_column("quantity")  # komplekt soni
    pieces: Mapped[int | None]  # jami dona (komplektda — komplekt × razmerlar soni)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    price_unit: Mapped[str | None] = mapped_column(String(5))  # PriceUnit
    amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))  # jami summa
    currency: Mapped[str] = mapped_column(String(3), default="UZS")
    note: Mapped[str | None] = mapped_column(Text)
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    raw_text: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    client: Mapped[Client] = relationship(lazy="joined")


# --- 3. Shaxsiy: xarajat/daromad va qarzlar ---------------------------------

class PersonalKind:
    EXPENSE = "expense"
    INCOME = "income"


class PersonalEntry(TimestampMixin, Base):
    __tablename__ = "personal_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # expense | income
    category: Mapped[str] = mapped_column(String(50))
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    currency: Mapped[str] = mapped_column(String(3), default="UZS")
    note: Mapped[str | None] = mapped_column(Text)
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    raw_text: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DebtDirection:
    IN = "in"  # men undan pul oldim (qarz oldim yoki u qarzini qaytardi)
    OUT = "out"  # men unga pul berdim (qarz berdim yoki qarzimni qaytardim)


class DebtEntry(TimestampMixin, Base):
    """Qarz harakati. Odam bo'yicha qoldiq = berilgan − olingan:
    musbat — u menga qarzdor, manfiy — men unga qarzdorman."""

    __tablename__ = "debt_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    person: Mapped[str] = mapped_column(String(100))
    # Bir odamning turli yozilishlarini ("Abbos", "аббос") birlashtirish uchun
    person_key: Mapped[str] = mapped_column(String(100), index=True)
    direction: Mapped[str] = mapped_column(String(3))
    # Pul berilmagan/olinmagan, faqat qarz holati aytilgan: «Hayrulloda 65 million haqqim bor»
    stated: Mapped[bool] = mapped_column(default=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    currency: Mapped[str] = mapped_column(String(3), default="UZS")
    note: Mapped[str | None] = mapped_column(Text)
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    raw_text: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
