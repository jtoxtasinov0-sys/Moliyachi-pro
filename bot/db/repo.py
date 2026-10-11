"""Bazaga so'rovlar. Handlerlar to'g'ridan-to'g'ri SQL yozmaydi, shu yerdan foydalanadi."""
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Alias, EntryStatus, ItemModel, Role, Setting, User, WorkEntry

# --- Sozlamalar -------------------------------------------------------------

ALL_CURRENCIES = ("UZS", "USD", "KRW")

DEFAULT_SETTINGS = {
    "require_approval": "1",
    "currencies": ",".join(ALL_CURRENCIES),
}


async def get_setting(session: AsyncSession, key: str) -> str:
    row = await session.get(Setting, key)
    return row.value if row else DEFAULT_SETTINGS[key]


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(Setting, key)
    if row:
        row.value = value
    else:
        session.add(Setting(key=key, value=value))
    await session.flush()


async def require_approval(session: AsyncSession) -> bool:
    return await get_setting(session, "require_approval") == "1"


async def enabled_currencies(session: AsyncSession) -> list[str]:
    raw = await get_setting(session, "currencies")
    return [c for c in raw.split(",") if c in ALL_CURRENCIES]


# --- Foydalanuvchilar -------------------------------------------------------

async def get_user_by_tg(session: AsyncSession, tg_id: int) -> User | None:
    return await session.scalar(select(User).where(User.tg_id == tg_id))


async def ensure_owner(session: AsyncSession, tg_id: int, name: str) -> User:
    user = await get_user_by_tg(session, tg_id)
    if user is None:
        user = User(tg_id=tg_id, name=name, role=Role.OWNER)
        session.add(user)
        await session.flush()
    elif user.role != Role.OWNER:
        user.role = Role.OWNER
    return user


async def get_owner(session: AsyncSession) -> User | None:
    return await session.scalar(select(User).where(User.role == Role.OWNER).limit(1))


async def create_worker(session: AsyncSession, name: str) -> User:
    user = User(name=name, role=Role.WORKER, invite_token=secrets.token_urlsafe(12))
    session.add(user)
    await session.flush()
    return user


async def new_invite_token(session: AsyncSession, user: User) -> str:
    user.invite_token = secrets.token_urlsafe(12)
    await session.flush()
    return user.invite_token


async def link_invite(session: AsyncSession, token: str, tg_id: int) -> User | None:
    user = await session.scalar(select(User).where(User.invite_token == token))
    if user is None or not user.is_active:
        return None
    # Bitta Telegram akkaunt faqat bitta ishchiga bog'lanadi
    existing = await get_user_by_tg(session, tg_id)
    if existing and existing.id != user.id:
        return None
    user.tg_id = tg_id
    user.invite_token = None
    await session.flush()
    return user


async def list_workers(session: AsyncSession, active_only: bool = True) -> list[User]:
    q = select(User).where(User.role == Role.WORKER).order_by(User.name)
    if active_only:
        q = q.where(User.is_active.is_(True))
    return list(await session.scalars(q))


# --- Modellar ---------------------------------------------------------------

async def list_models(session: AsyncSession, active_only: bool = True) -> list[ItemModel]:
    q = select(ItemModel).order_by(ItemModel.name)
    if active_only:
        q = q.where(ItemModel.is_active.is_(True))
    return list(await session.scalars(q))


async def get_model_by_name(session: AsyncSession, name: str) -> ItemModel | None:
    return await session.scalar(select(ItemModel).where(func.lower(ItemModel.name) == name.lower()))


async def create_model(
    session: AsyncSession, name: str, price: Decimal | None = None, currency: str = "UZS"
) -> ItemModel:
    model = ItemModel(name=name, price=price, currency=currency)
    session.add(model)
    await session.flush()
    return model


# --- Taxalluslar (ovozdagi xato ismlar) -------------------------------------

async def get_aliases(session: AsyncSession, entity_type: str) -> list[tuple[int, str]]:
    rows = await session.execute(
        select(Alias.entity_id, Alias.alias).where(Alias.entity_type == entity_type)
    )
    return [(r[0], r[1]) for r in rows]


async def save_alias(session: AsyncSession, entity_type: str, entity_id: int, alias: str) -> None:
    alias = alias.strip().lower()[:100]
    if not alias:
        return
    row = await session.scalar(
        select(Alias).where(Alias.entity_type == entity_type, Alias.alias == alias)
    )
    if row:
        row.entity_id = entity_id
    else:
        session.add(Alias(entity_type=entity_type, entity_id=entity_id, alias=alias))
    await session.flush()


# --- Ish yozuvlari ----------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


async def get_work_entry(session: AsyncSession, entry_id: int) -> WorkEntry | None:
    entry = await session.get(WorkEntry, entry_id)
    if entry is None or entry.deleted_at is not None:
        return None
    return entry


async def create_work_entry(
    session: AsyncSession,
    *,
    worker_id: int,
    model_id: int,
    quantity: int,
    work_date: date,
    status: str,
    raw_text: str | None,
    created_by: int,
) -> WorkEntry:
    model = await session.get(ItemModel, model_id)
    entry = WorkEntry(
        worker_id=worker_id,
        model_id=model_id,
        quantity=quantity,
        work_date=work_date,
        unit_price=model.price if model else None,
        price_currency=model.currency if model and model.price is not None else None,
        status=status,
        raw_text=raw_text,
        created_by=created_by,
    )
    session.add(entry)
    await session.flush()
    await session.refresh(entry, ["worker", "model"])
    return entry


async def soft_delete_work_entry(session: AsyncSession, entry: WorkEntry) -> None:
    entry.deleted_at = _now()
    await session.flush()


async def review_work_entry(session: AsyncSession, entry: WorkEntry, reviewer: User, approve: bool) -> None:
    entry.status = EntryStatus.APPROVED if approve else EntryStatus.REJECTED
    entry.reviewed_by = reviewer.id
    entry.reviewed_at = _now()
    await session.flush()


async def recent_work_entries(
    session: AsyncSession, worker_id: int | None = None, limit: int = 10
) -> list[WorkEntry]:
    q = (
        select(WorkEntry)
        .where(WorkEntry.deleted_at.is_(None))
        .order_by(WorkEntry.id.desc())
        .limit(limit)
    )
    if worker_id is not None:
        q = q.where(WorkEntry.worker_id == worker_id)
    return list(await session.scalars(q))


async def pending_work_entries(session: AsyncSession) -> list[WorkEntry]:
    q = (
        select(WorkEntry)
        .where(WorkEntry.deleted_at.is_(None), WorkEntry.status == EntryStatus.PENDING)
        .order_by(WorkEntry.id)
    )
    return list(await session.scalars(q))


def apply_model_price(entry: WorkEntry, model: ItemModel) -> None:
    entry.model_id = model.id
    entry.unit_price = model.price
    entry.price_currency = model.currency if model.price is not None else None


@dataclass
class WorkReportRow:
    worker_id: int
    worker_name: str
    model_name: str
    quantity: int
    pay: Decimal | None  # narx kiritilmagan bo'lsa None
    currency: str | None


async def work_report(
    session: AsyncSession, start: date, end: date, worker_id: int | None = None
) -> list[WorkReportRow]:
    """Tasdiqlangan ishlar: ishchi va model bo'yicha jamlangan (start..end, ikkalasi ham kiradi)."""
    price = func.coalesce(WorkEntry.unit_price, ItemModel.price)
    currency = func.coalesce(WorkEntry.price_currency, ItemModel.currency)
    q = (
        select(
            User.id,
            User.name,
            ItemModel.name,
            func.sum(WorkEntry.quantity),
            func.sum(WorkEntry.quantity * price),
            currency,
        )
        .select_from(WorkEntry)
        .join(User, User.id == WorkEntry.worker_id)
        .join(ItemModel, ItemModel.id == WorkEntry.model_id)
        .where(
            WorkEntry.deleted_at.is_(None),
            WorkEntry.status == EntryStatus.APPROVED,
            WorkEntry.work_date >= start,
            WorkEntry.work_date <= end,
        )
        .group_by(User.id, User.name, ItemModel.id, ItemModel.name, currency)
        .order_by(User.name, ItemModel.name)
    )
    if worker_id is not None:
        q = q.where(WorkEntry.worker_id == worker_id)
    rows = await session.execute(q)
    return [
        WorkReportRow(r[0], r[1], r[2], int(r[3] or 0), Decimal(str(r[4])) if r[4] is not None else None, r[5])
        for r in rows
    ]


@dataclass
class WorkStatRow:
    work_date: date
    worker_id: int
    worker_name: str
    model_name: str
    quantity: int
    pay: Decimal | None
    currency: str | None


async def work_stat_rows(
    session: AsyncSession, start: date, end: date, worker_id: int | None = None
) -> list[WorkStatRow]:
    """Tasdiqlangan ishlar: kun, ishchi va model bo'yicha jamlangan — grafiklar uchun."""
    price = func.coalesce(WorkEntry.unit_price, ItemModel.price)
    currency = func.coalesce(WorkEntry.price_currency, ItemModel.currency)
    q = (
        select(
            WorkEntry.work_date,
            User.id,
            User.name,
            ItemModel.name,
            func.sum(WorkEntry.quantity),
            func.sum(WorkEntry.quantity * price),
            currency,
        )
        .select_from(WorkEntry)
        .join(User, User.id == WorkEntry.worker_id)
        .join(ItemModel, ItemModel.id == WorkEntry.model_id)
        .where(
            WorkEntry.deleted_at.is_(None),
            WorkEntry.status == EntryStatus.APPROVED,
            WorkEntry.work_date >= start,
            WorkEntry.work_date <= end,
        )
        .group_by(WorkEntry.work_date, User.id, User.name, ItemModel.id, ItemModel.name, currency)
    )
    if worker_id is not None:
        q = q.where(WorkEntry.worker_id == worker_id)
    rows = await session.execute(q)
    return [
        WorkStatRow(
            r[0], r[1], r[2], r[3], int(r[4] or 0), Decimal(str(r[5])) if r[5] is not None else None, r[6]
        )
        for r in rows
    ]


async def list_work_entries(
    session: AsyncSession,
    *,
    status: str | None = None,
    worker_id: int | None = None,
    limit: int = 30,
    offset: int = 0,
) -> list[WorkEntry]:
    q = (
        select(WorkEntry)
        .where(WorkEntry.deleted_at.is_(None))
        .order_by(WorkEntry.work_date.desc(), WorkEntry.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if status:
        q = q.where(WorkEntry.status == status)
    if worker_id is not None:
        q = q.where(WorkEntry.worker_id == worker_id)
    return list(await session.scalars(q))


async def count_pending(session: AsyncSession, worker_id: int | None = None) -> int:
    q = select(func.count(WorkEntry.id)).where(
        WorkEntry.deleted_at.is_(None), WorkEntry.status == EntryStatus.PENDING
    )
    if worker_id is not None:
        q = q.where(WorkEntry.worker_id == worker_id)
    return int(await session.scalar(q) or 0)
