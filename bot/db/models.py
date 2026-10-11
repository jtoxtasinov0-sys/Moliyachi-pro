"""Ma'lumotlar bazasi jadvallari.

Uchta bo'lim (ishchilar, mijozlar, shaxsiy) alohida jadvallarda saqlanadi,
shuning uchun ular bir-biriga aralashmaydi.
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
    is_active: Mapped[bool] = mapped_column(default=True)
    # Ishchi taklif havolasi orqali ulanmaguncha shu token saqlanadi
    invite_token: Mapped[str | None] = mapped_column(String(32), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def is_owner(self) -> bool:
        return self.role == Role.OWNER


class ItemModel(Base):
    """Mahsulot modeli (masalan, "A-12"). Narx — bir dona uchun ish haqi."""

    __tablename__ = "item_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="UZS")
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Alias(Base):
    """Ovozdan noto'g'ri chiqqan ismlarni eslab qolish: "alishir" -> Alisher."""

    __tablename__ = "aliases"
    __table_args__ = (UniqueConstraint("entity_type", "alias"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(20))  # worker | model | client
    entity_id: Mapped[int]
    alias: Mapped[str] = mapped_column(String(100))


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


# --- 1. Ishchilar -----------------------------------------------------------

class EntryStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class WorkEntry(TimestampMixin, Base):
    __tablename__ = "work_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
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


# --- 2. Mijozlar (2-bosqich) ------------------------------------------------

class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    phone: Mapped[str | None] = mapped_column(String(30))
    note: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClientEntry(TimestampMixin, Base):
    __tablename__ = "client_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    direction: Mapped[str] = mapped_column(String(10))  # received | given
    model_id: Mapped[int | None] = mapped_column(ForeignKey("item_models.id"))
    quantity: Mapped[int | None]
    amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    currency: Mapped[str] = mapped_column(String(3), default="UZS")
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    raw_text: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    client: Mapped[Client] = relationship(lazy="joined")
    model: Mapped[ItemModel | None] = relationship(lazy="joined")


# --- 3. Shaxsiy (3-bosqich) -------------------------------------------------

class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("name", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(10))  # expense | income


class PersonalEntry(TimestampMixin, Base):
    __tablename__ = "personal_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # expense | income
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    currency: Mapped[str] = mapped_column(String(3), default="UZS")
    note: Mapped[str | None] = mapped_column(Text)
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    raw_text: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    category: Mapped[Category | None] = relationship(lazy="joined")
