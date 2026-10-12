"""Bazaga so'rovlar. Handlerlar to'g'ridan-to'g'ri SQL yozmaydi, shu yerdan foydalanadi.

Deyarli har bir funksiya owner_id (kabinet) oladi va faqat o'sha kabinet ma'lumotini qaytaradi.
"""
import secrets
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import (
    Alias,
    Client,
    ClientEntry,
    ClientKind,
    DebtDirection,
    DebtEntry,
    EntryStatus,
    ItemModel,
    PersonalEntry,
    Role,
    Setting,
    User,
    WorkEntry,
)
from bot.services.matcher import normalize

# --- Sozlamalar -------------------------------------------------------------

ALL_CURRENCIES = ("UZS", "USD", "KRW")

DEFAULT_SETTINGS = {
    "require_approval": "1",
    "currencies": ",".join(ALL_CURRENCIES),
    # Summa valyutasiz aytilsa shu valyuta olinadi
    "main_currency": ALL_CURRENCIES[0],
    # Bir komplektdagi dona (razmerlar) soni
    "set_size": "5",
}


async def get_setting(session: AsyncSession, owner_id: int, key: str) -> str:
    row = await session.get(Setting, (owner_id, key))
    return row.value if row else DEFAULT_SETTINGS[key]


async def set_setting(session: AsyncSession, owner_id: int, key: str, value: str) -> None:
    row = await session.get(Setting, (owner_id, key))
    if row:
        row.value = value
    else:
        session.add(Setting(owner_id=owner_id, key=key, value=value))
    await session.flush()


async def require_approval(session: AsyncSession, owner_id: int) -> bool:
    return await get_setting(session, owner_id, "require_approval") == "1"


async def enabled_currencies(session: AsyncSession, owner_id: int) -> list[str]:
    """Yoqilgan valyutalar, asosiysi birinchi."""
    raw = await get_setting(session, owner_id, "currencies")
    enabled = [c for c in raw.split(",") if c in ALL_CURRENCIES]
    main = await get_setting(session, owner_id, "main_currency")
    if main in enabled:
        enabled.remove(main)
        enabled.insert(0, main)
    return enabled


async def set_currencies(session: AsyncSession, owner_id: int, codes: list[str]) -> None:
    await set_setting(session, owner_id, "currencies", ",".join(c for c in ALL_CURRENCIES if c in codes))


async def set_main_currency(session: AsyncSession, owner_id: int, code: str) -> None:
    """Asosiy valyuta o'chirilgan bo'lsa — uni yoqib ham qo'yadi."""
    enabled = await enabled_currencies(session, owner_id)
    if code not in enabled:
        await set_currencies(session, owner_id, enabled + [code])
    await set_setting(session, owner_id, "main_currency", code)


MAX_SET_SIZE = 50


async def set_size(session: AsyncSession, owner_id: int) -> int:
    try:
        value = int(await get_setting(session, owner_id, "set_size"))
    except ValueError:
        value = 0
    return value if 1 <= value <= MAX_SET_SIZE else int(DEFAULT_SETTINGS["set_size"])


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
        user.owner_id = None
    return user


async def create_worker(session: AsyncSession, owner_id: int, name: str) -> User:
    user = User(name=name, role=Role.WORKER, owner_id=owner_id, invite_token=secrets.token_urlsafe(12))
    session.add(user)
    await session.flush()
    return user


async def get_worker(session: AsyncSession, owner_id: int, worker_id: int) -> User | None:
    user = await session.get(User, worker_id)
    if user is None or user.role != Role.WORKER or user.owner_id != owner_id:
        return None
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


async def list_workers(session: AsyncSession, owner_id: int, active_only: bool = True) -> list[User]:
    q = select(User).where(User.role == Role.WORKER, User.owner_id == owner_id).order_by(User.name)
    if active_only:
        q = q.where(User.is_active.is_(True))
    return list(await session.scalars(q))


# --- Modellar ---------------------------------------------------------------

async def list_models(session: AsyncSession, owner_id: int, active_only: bool = True) -> list[ItemModel]:
    q = select(ItemModel).where(ItemModel.owner_id == owner_id).order_by(ItemModel.name)
    if active_only:
        q = q.where(ItemModel.is_active.is_(True))
    return list(await session.scalars(q))


async def get_model(session: AsyncSession, owner_id: int, model_id: int) -> ItemModel | None:
    model = await session.get(ItemModel, model_id)
    return model if model is not None and model.owner_id == owner_id else None


async def get_model_by_name(session: AsyncSession, owner_id: int, name: str) -> ItemModel | None:
    return await session.scalar(
        select(ItemModel).where(ItemModel.owner_id == owner_id, func.lower(ItemModel.name) == name.lower())
    )


async def create_model(
    session: AsyncSession, owner_id: int, name: str, price: Decimal | None = None, currency: str = "UZS"
) -> ItemModel:
    model = ItemModel(owner_id=owner_id, name=name, price=price, currency=currency)
    session.add(model)
    await session.flush()
    return model


# --- Taxalluslar (ovozdagi xato ismlar) -------------------------------------

async def get_aliases(session: AsyncSession, owner_id: int, entity_type: str) -> list[tuple[int, str]]:
    rows = await session.execute(
        select(Alias.entity_id, Alias.alias).where(Alias.owner_id == owner_id, Alias.entity_type == entity_type)
    )
    return [(r[0], r[1]) for r in rows]


async def save_alias(session: AsyncSession, owner_id: int, entity_type: str, entity_id: int, alias: str) -> None:
    alias = alias.strip().lower()[:100]
    if not alias:
        return
    row = await session.scalar(
        select(Alias).where(Alias.owner_id == owner_id, Alias.entity_type == entity_type, Alias.alias == alias)
    )
    if row:
        row.entity_id = entity_id
    else:
        session.add(Alias(owner_id=owner_id, entity_type=entity_type, entity_id=entity_id, alias=alias))
    await session.flush()


# --- Ish yozuvlari ----------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


async def get_work_entry(session: AsyncSession, owner_id: int, entry_id: int) -> WorkEntry | None:
    entry = await session.get(WorkEntry, entry_id)
    if entry is None or entry.deleted_at is not None or entry.owner_id != owner_id:
        return None
    return entry


async def create_work_entry(
    session: AsyncSession,
    *,
    owner_id: int,
    worker_id: int,
    model_id: int,
    quantity: int,
    work_date: date,
    status: str,
    raw_text: str | None,
    created_by: int,
) -> WorkEntry:
    model = await get_model(session, owner_id, model_id)
    entry = WorkEntry(
        owner_id=owner_id,
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


async def soft_delete(
    session: AsyncSession, entry: WorkEntry | PersonalEntry | DebtEntry | ClientEntry
) -> None:
    entry.deleted_at = _now()
    await session.flush()


async def review_work_entry(session: AsyncSession, entry: WorkEntry, reviewer: User, approve: bool) -> None:
    entry.status = EntryStatus.APPROVED if approve else EntryStatus.REJECTED
    entry.reviewed_by = reviewer.id
    entry.reviewed_at = _now()
    await session.flush()


async def recent_work_entries(
    session: AsyncSession, owner_id: int, worker_id: int | None = None, limit: int = 10
) -> list[WorkEntry]:
    q = (
        select(WorkEntry)
        .where(WorkEntry.owner_id == owner_id, WorkEntry.deleted_at.is_(None))
        .order_by(WorkEntry.id.desc())
        .limit(limit)
    )
    if worker_id is not None:
        q = q.where(WorkEntry.worker_id == worker_id)
    return list(await session.scalars(q))


async def pending_work_entries(session: AsyncSession, owner_id: int) -> list[WorkEntry]:
    q = (
        select(WorkEntry)
        .where(
            WorkEntry.owner_id == owner_id,
            WorkEntry.deleted_at.is_(None),
            WorkEntry.status == EntryStatus.PENDING,
        )
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
    session: AsyncSession, owner_id: int, start: date, end: date, worker_id: int | None = None
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
            WorkEntry.owner_id == owner_id,
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
    session: AsyncSession, owner_id: int, start: date, end: date, worker_id: int | None = None
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
            WorkEntry.owner_id == owner_id,
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
    owner_id: int,
    *,
    status: str | None = None,
    worker_id: int | None = None,
    limit: int = 30,
    offset: int = 0,
) -> list[WorkEntry]:
    q = (
        select(WorkEntry)
        .where(WorkEntry.owner_id == owner_id, WorkEntry.deleted_at.is_(None))
        .order_by(WorkEntry.work_date.desc(), WorkEntry.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if status:
        q = q.where(WorkEntry.status == status)
    if worker_id is not None:
        q = q.where(WorkEntry.worker_id == worker_id)
    return list(await session.scalars(q))


async def has_work(session: AsyncSession, owner_id: int) -> bool:
    """Kabinetda ishchi yoki ish yozuvi bormi — bo'lmasa ishxona hisoboti yashiriladi."""
    worker = await session.scalar(
        select(User.id).where(User.role == Role.WORKER, User.owner_id == owner_id).limit(1)
    )
    if worker is not None:
        return True
    entry = await session.scalar(
        select(WorkEntry.id).where(WorkEntry.owner_id == owner_id, WorkEntry.deleted_at.is_(None)).limit(1)
    )
    return entry is not None


async def count_pending(session: AsyncSession, owner_id: int, worker_id: int | None = None) -> int:
    q = select(func.count(WorkEntry.id)).where(
        WorkEntry.owner_id == owner_id, WorkEntry.deleted_at.is_(None), WorkEntry.status == EntryStatus.PENDING
    )
    if worker_id is not None:
        q = q.where(WorkEntry.worker_id == worker_id)
    return int(await session.scalar(q) or 0)


# --- Shaxsiy xarajat / daromad ----------------------------------------------

async def create_personal_entry(
    session: AsyncSession,
    *,
    owner_id: int,
    kind: str,
    category: str,
    amount: Decimal,
    currency: str,
    note: str | None,
    entry_date: date,
    raw_text: str | None,
) -> PersonalEntry:
    entry = PersonalEntry(
        owner_id=owner_id,
        kind=kind,
        category=category[:50],
        amount=amount,
        currency=currency,
        note=note,
        entry_date=entry_date,
        raw_text=raw_text,
    )
    session.add(entry)
    await session.flush()
    return entry


async def get_personal_entry(session: AsyncSession, owner_id: int, entry_id: int) -> PersonalEntry | None:
    entry = await session.get(PersonalEntry, entry_id)
    if entry is None or entry.deleted_at is not None or entry.owner_id != owner_id:
        return None
    return entry


async def list_personal_entries(
    session: AsyncSession, owner_id: int, *, kind: str | None = None, limit: int = 30, offset: int = 0
) -> list[PersonalEntry]:
    q = (
        select(PersonalEntry)
        .where(PersonalEntry.owner_id == owner_id, PersonalEntry.deleted_at.is_(None))
        .order_by(PersonalEntry.entry_date.desc(), PersonalEntry.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if kind is not None:
        q = q.where(PersonalEntry.kind == kind)
    return list(await session.scalars(q))


@dataclass
class PersonalStatRow:
    entry_date: date
    kind: str
    category: str
    currency: str
    amount: Decimal


async def personal_stat_rows(session: AsyncSession, owner_id: int, start: date, end: date) -> list[PersonalStatRow]:
    """Kun, tur, kategoriya va valyuta bo'yicha jamlangan summalar."""
    q = (
        select(
            PersonalEntry.entry_date,
            PersonalEntry.kind,
            PersonalEntry.category,
            PersonalEntry.currency,
            func.sum(PersonalEntry.amount),
        )
        .where(
            PersonalEntry.owner_id == owner_id,
            PersonalEntry.deleted_at.is_(None),
            PersonalEntry.entry_date >= start,
            PersonalEntry.entry_date <= end,
        )
        .group_by(PersonalEntry.entry_date, PersonalEntry.kind, PersonalEntry.category, PersonalEntry.currency)
    )
    rows = await session.execute(q)
    return [PersonalStatRow(r[0], r[1], r[2], r[3], Decimal(str(r[4] or 0))) for r in rows]


async def personal_categories(session: AsyncSession, owner_id: int) -> list[str]:
    """Ishlatilgan kategoriyalar — ko'p ishlatilgani birinchi."""
    q = (
        select(PersonalEntry.category)
        .where(PersonalEntry.owner_id == owner_id, PersonalEntry.deleted_at.is_(None))
        .group_by(PersonalEntry.category)
        .order_by(func.count(PersonalEntry.id).desc())
        .limit(40)
    )
    return list(await session.scalars(q))


# --- Qarzlar ----------------------------------------------------------------

def person_key(name: str) -> str:
    return (normalize(name) or name.strip().lower())[:100]


async def create_debt_entry(
    session: AsyncSession,
    *,
    owner_id: int,
    person: str,
    direction: str,
    amount: Decimal,
    currency: str,
    note: str | None,
    entry_date: date,
    raw_text: str | None,
    stated: bool = False,
) -> DebtEntry:
    person = person.strip()[:100]
    entry = DebtEntry(
        owner_id=owner_id,
        person=person,
        person_key=person_key(person),
        direction=direction,
        stated=stated,
        amount=amount,
        currency=currency,
        note=note,
        entry_date=entry_date,
        raw_text=raw_text,
    )
    session.add(entry)
    await session.flush()
    return entry


async def get_debt_entry(session: AsyncSession, owner_id: int, entry_id: int) -> DebtEntry | None:
    entry = await session.get(DebtEntry, entry_id)
    if entry is None or entry.deleted_at is not None or entry.owner_id != owner_id:
        return None
    return entry


async def list_debt_entries(
    session: AsyncSession, owner_id: int, *, key: str | None = None, limit: int = 30, offset: int = 0
) -> list[DebtEntry]:
    q = (
        select(DebtEntry)
        .where(DebtEntry.owner_id == owner_id, DebtEntry.deleted_at.is_(None))
        .order_by(DebtEntry.entry_date.desc(), DebtEntry.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if key is not None:
        q = q.where(DebtEntry.person_key == key)
    return list(await session.scalars(q))


@dataclass
class DebtBalance:
    key: str
    person: str  # birinchi yozilishi
    # valyuta -> qoldiq: musbat — u menga qarzdor, manfiy — men unga qarzdorman
    balance: dict[str, Decimal] = field(default_factory=dict)
    last_date: date | None = None
    count: int = 0

    @property
    def settled(self) -> bool:
        return not any(self.balance.values())


async def debt_balances(session: AsyncSession, owner_id: int) -> list[DebtBalance]:
    signed = case((DebtEntry.direction == DebtDirection.OUT, DebtEntry.amount), else_=-DebtEntry.amount)
    alive = (DebtEntry.owner_id == owner_id, DebtEntry.deleted_at.is_(None))
    rows = await session.execute(
        select(
            DebtEntry.person_key,
            DebtEntry.currency,
            func.sum(signed),
            func.max(DebtEntry.entry_date),
            func.count(DebtEntry.id),
        )
        .where(*alive)
        .group_by(DebtEntry.person_key, DebtEntry.currency)
    )
    balances: dict[str, DebtBalance] = {}
    for key, currency, total, last, count in rows:
        b = balances.setdefault(key, DebtBalance(key=key, person=key))
        b.balance[currency] = Decimal(str(total or 0))
        b.last_date = max(filter(None, [b.last_date, last]), default=None)
        b.count += int(count)

    # Ism birinchi yozilganidek ko'rsatiladi (keyingi yozuvlar ham shu ismga moslanadi)
    names = await session.execute(
        select(DebtEntry.person_key, DebtEntry.person).where(*alive).order_by(DebtEntry.id.desc())
    )
    for key, person in names:
        if key in balances:
            balances[key].person = person

    result = list(balances.values())
    result.sort(key=lambda b: (b.settled, -(b.last_date or date.min).toordinal(), b.person.lower()))
    return result


def debt_totals(balances: list[DebtBalance]) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    """(menga qarzlar, mening qarzlarim) — valyuta bo'yicha, ikkalasi ham musbat son."""
    owed_to_me: dict[str, Decimal] = defaultdict(Decimal)
    i_owe: dict[str, Decimal] = defaultdict(Decimal)
    for b in balances:
        for currency, value in b.balance.items():
            if value > 0:
                owed_to_me[currency] += value
            elif value < 0:
                i_owe[currency] += -value
    return dict(owed_to_me), dict(i_owe)


async def debt_people(session: AsyncSession, owner_id: int) -> list[tuple[str, str]]:
    """(person_key, ism) — LLM va moslashtirish uchun."""
    return [(b.key, b.person) for b in await debt_balances(session, owner_id)]


# --- Mijozlar: optom savdo --------------------------------------------------

async def list_clients(session: AsyncSession, owner_id: int, active_only: bool = True) -> list[Client]:
    q = select(Client).where(Client.owner_id == owner_id).order_by(Client.name)
    if active_only:
        q = q.where(Client.is_active.is_(True))
    return list(await session.scalars(q))


async def get_client(session: AsyncSession, owner_id: int, client_id: int) -> Client | None:
    client = await session.get(Client, client_id)
    return client if client is not None and client.owner_id == owner_id else None


async def get_client_by_name(session: AsyncSession, owner_id: int, name: str) -> Client | None:
    return await session.scalar(
        select(Client).where(Client.owner_id == owner_id, func.lower(Client.name) == name.strip().lower())
    )


async def create_client(session: AsyncSession, owner_id: int, name: str, phone: str | None = None) -> Client:
    client = Client(owner_id=owner_id, name=name.strip()[:100], phone=phone)
    session.add(client)
    await session.flush()
    return client


async def get_or_create_client(session: AsyncSession, owner_id: int, name: str) -> Client:
    client = await get_client_by_name(session, owner_id, name)
    if client is None:
        client = await create_client(session, owner_id, name)
    elif not client.is_active:
        client.is_active = True
    return client


async def create_client_entry(
    session: AsyncSession,
    *,
    owner_id: int,
    client_id: int,
    kind: str,
    product: str | None,
    sets: int | None,
    pieces: int | None,
    unit_price: Decimal | None,
    price_unit: str | None,
    amount: Decimal | None,
    currency: str,
    note: str | None,
    entry_date: date,
    raw_text: str | None,
    created_by: int,
) -> ClientEntry:
    entry = ClientEntry(
        owner_id=owner_id,
        client_id=client_id,
        kind=kind,
        product=product,
        sets=sets,
        pieces=pieces,
        unit_price=unit_price,
        price_unit=price_unit,
        amount=amount,
        currency=currency,
        note=note,
        entry_date=entry_date,
        raw_text=raw_text,
        created_by=created_by,
    )
    session.add(entry)
    await session.flush()
    await session.refresh(entry, ["client"])
    return entry


async def get_client_entry(session: AsyncSession, owner_id: int, entry_id: int) -> ClientEntry | None:
    entry = await session.get(ClientEntry, entry_id)
    if entry is None or entry.deleted_at is not None or entry.owner_id != owner_id:
        return None
    return entry


async def list_client_entries(
    session: AsyncSession, owner_id: int, *, client_id: int | None = None, limit: int = 30, offset: int = 0
) -> list[ClientEntry]:
    q = (
        select(ClientEntry)
        .where(ClientEntry.owner_id == owner_id, ClientEntry.deleted_at.is_(None))
        .order_by(ClientEntry.entry_date.desc(), ClientEntry.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if client_id is not None:
        q = q.where(ClientEntry.client_id == client_id)
    return list(await session.scalars(q))


@dataclass
class ClientStats:
    client: Client
    # valyuta -> qoldiq: musbat — mijoz bizga qarzdor, manfiy — oldindan to'lagan
    balance: dict[str, Decimal] = field(default_factory=dict)
    sold: dict[str, Decimal] = field(default_factory=dict)
    paid: dict[str, Decimal] = field(default_factory=dict)
    sets: int = 0
    pieces: int = 0
    last_date: date | None = None
    count: int = 0


async def client_stats(
    session: AsyncSession, owner_id: int, start: date | None = None, end: date | None = None
) -> list[ClientStats]:
    """Har bir mijoz bo'yicha jami. Qoldiq doim butun davr uchun, qolganlari — start..end oralig'ida."""
    clients = {c.id: c for c in await list_clients(session, owner_id, active_only=False)}
    stats = {cid: ClientStats(client=c) for cid, c in clients.items()}
    rows = await session.execute(
        select(
            ClientEntry.client_id,
            ClientEntry.kind,
            ClientEntry.currency,
            ClientEntry.entry_date,
            func.sum(ClientEntry.amount),
            func.sum(ClientEntry.sets),
            func.sum(ClientEntry.pieces),
            func.count(ClientEntry.id),
        )
        .where(ClientEntry.owner_id == owner_id, ClientEntry.deleted_at.is_(None))
        .group_by(ClientEntry.client_id, ClientEntry.kind, ClientEntry.currency, ClientEntry.entry_date)
    )
    for client_id, kind, currency, entry_date, amount, sets, pieces, count in rows:
        st = stats.get(client_id)
        if st is None:
            continue
        amount = Decimal(str(amount or 0))
        sign = 1 if kind == ClientKind.SALE else -1
        st.balance[currency] = st.balance.get(currency, Decimal(0)) + sign * amount
        st.last_date = max(filter(None, [st.last_date, entry_date]), default=None)
        st.count += int(count)
        if (start is None or entry_date >= start) and (end is None or entry_date <= end):
            if kind == ClientKind.SALE:
                st.sold[currency] = st.sold.get(currency, Decimal(0)) + amount
                st.sets += int(sets or 0)
                st.pieces += int(pieces or 0)
            elif kind == ClientKind.PAYMENT:
                st.paid[currency] = st.paid.get(currency, Decimal(0)) + amount
    result = [st for st in stats.values() if st.client.is_active or st.count]
    result.sort(
        key=lambda st: (
            not any(v > 0 for v in st.balance.values()),
            -(st.last_date or date.min).toordinal(),
            st.client.name.lower(),
        )
    )
    return result


async def client_balance(session: AsyncSession, owner_id: int, client_id: int) -> dict[str, Decimal]:
    for st in await client_stats(session, owner_id):
        if st.client.id == client_id:
            return {c: v for c, v in st.balance.items() if v}
    return {}
