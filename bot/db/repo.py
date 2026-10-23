"""Bazaga so'rovlar. Handlerlar to'g'ridan-to'g'ri SQL yozmaydi, shu yerdan foydalanadi.

Deyarli har bir funksiya owner_id (kabinet) oladi va faqat o'sha kabinet ma'lumotini qaytaradi.
"""
import secrets
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
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
    Job,
    MaterialEntry,
    ModelPrice,
    PayKind,
    PersonalEntry,
    Role,
    Setting,
    Supplier,
    User,
    WorkEntry,
    WorkerPayment,
)
from bot.services.matcher import normalize

# --- Sozlamalar -------------------------------------------------------------

ALL_CURRENCIES = ("UZS", "USD", "KRW")

DEFAULT_SETTINGS = {
    "require_approval": "1",
    # Ishchi yozgan pul («500 ming oldim», «qaytardim») egasi tasdiqlagach qoldiqqa kiradi
    "require_pay_approval": "1",
    "currencies": ",".join(ALL_CURRENCIES),
    # Summa valyutasiz aytilsa shu valyuta olinadi
    "main_currency": ALL_CURRENCIES[0],
    # Bir komplektdagi dona (razmerlar) soni
    "set_size": "5",
    # Ovoz yoki matndan to'liq tushunilgan yozuv (ism, model, mijoz topildi) «✅ Saqlash» bosilmasdan saqlanadi
    "auto_save": "1",
    # Bank skrinshoti / chek rasmidan o'qilgan xarajatlar «✅ Tasdiqlash» bosilmasdan saqlanadi
    "auto_save_images": "0",
    # Daromadning necha foizi jamg'armaga ajratiladi (maslahat va byudjet uchun)
    "savings_pct": "20",
    # Kundalik eslatma soati (mahalliy vaqt), "off" — o'chiq
    "reminder_hour": "21",
    # Eslatma oxirgi marta yuborilgan sana (YYYY-MM-DD) — bir kunda ikki marta yubormaslik uchun
    "reminder_sent": "",
    # Tasdiqlanmagan pul yozuvlari eslatmasi oxirgi marta tekshirilgan sana — kuniga bir marta
    "pay_reminder_sent": "",
    # Qo'llanma videosining Telegram file_id si — har safar qayta yuklamaslik uchun
    "guide_video_id": "",
    # Standart kasblar (Kroychi, Brsovkachi...) bir marta qo'shilgan: egasi o'chirsa, qaytib chiqmaydi
    "jobs_seeded": "0",
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


async def require_pay_approval(session: AsyncSession, owner_id: int) -> bool:
    return await get_setting(session, owner_id, "require_pay_approval") == "1"


async def auto_save(session: AsyncSession, owner_id: int) -> bool:
    return await get_setting(session, owner_id, "auto_save") == "1"


async def auto_save_images(session: AsyncSession, owner_id: int) -> bool:
    return await get_setting(session, owner_id, "auto_save_images") == "1"


SAVINGS_CHOICES = (10, 20, 30)
REMINDER_HOURS = (19, 20, 21, 22)


async def savings_pct(session: AsyncSession, owner_id: int) -> int:
    value = await get_setting(session, owner_id, "savings_pct")
    return int(value) if value.isdigit() and int(value) in SAVINGS_CHOICES else int(DEFAULT_SETTINGS["savings_pct"])


async def reminder_hour(session: AsyncSession, owner_id: int) -> int | None:
    """Kundalik eslatma soati yoki None (o'chiq)."""
    value = await get_setting(session, owner_id, "reminder_hour")
    return int(value) if value.isdigit() and 0 <= int(value) <= 23 else None


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


async def create_worker(session: AsyncSession, owner_id: int, name: str, job_id: int | None = None) -> User:
    user = User(
        name=name, role=Role.WORKER, owner_id=owner_id, invite_token=secrets.token_urlsafe(12), job_id=job_id
    )
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


# --- Kasblar ---------------------------------------------------------------

async def _seed_jobs(session: AsyncSession, owner_id: int) -> None:
    """Standart kasblarni bir marta qo'shadi (Kroychi, Brsovkachi, Tikuvchi, Tortuvchi, Pardozchi)."""
    from bot.jobs import DEFAULT_JOBS, MODEL_FREE_JOBS  # aylanma importdan qochish uchun

    if await get_setting(session, owner_id, "jobs_seeded") == "1":
        return
    try:
        # Bir vaqtda ikkita so'rov kelsa — ikkinchisi takror qo'shmaydi
        async with session.begin_nested():
            existing = {n.lower() for n in await session.scalars(select(Job.name).where(Job.owner_id == owner_id))}
            for i, name in enumerate(DEFAULT_JOBS, start=1):
                if name.lower() not in existing:
                    session.add(Job(owner_id=owner_id, name=name, position=i, model_free=name in MODEL_FREE_JOBS))
            await set_setting(session, owner_id, "jobs_seeded", "1")
    except IntegrityError:
        pass


async def list_jobs(session: AsyncSession, owner_id: int, active_only: bool = True) -> list[Job]:
    await _seed_jobs(session, owner_id)
    q = select(Job).where(Job.owner_id == owner_id).order_by(Job.position, Job.id)
    if active_only:
        q = q.where(Job.is_active.is_(True))
    return list(await session.scalars(q))


async def get_job(session: AsyncSession, owner_id: int, job_id: int | None) -> Job | None:
    if not job_id:
        return None
    job = await session.get(Job, job_id)
    return job if job is not None and job.owner_id == owner_id and job.is_active else None


async def get_job_by_name(session: AsyncSession, owner_id: int, name: str) -> Job | None:
    return await session.scalar(
        select(Job).where(Job.owner_id == owner_id, func.lower(Job.name) == name.strip().lower())
    )


async def create_job(session: AsyncSession, owner_id: int, name: str) -> Job:
    """Yangi kasb. Shu nomli o'chirilgan kasb bo'lsa — o'shani qaytaradi (eski narxlari bilan)."""
    from bot.jobs import clean_name

    name = clean_name(name)
    job = await get_job_by_name(session, owner_id, name)
    if job is not None:
        job.is_active = True
        return job
    last = await session.scalar(select(func.max(Job.position)).where(Job.owner_id == owner_id))
    job = Job(owner_id=owner_id, name=name, position=(last or 0) + 1)
    session.add(job)
    await session.flush()
    return job


async def rename_job(session: AsyncSession, job: Job, name: str) -> bool:
    """False — shu nomli boshqa kasb bor. O'chirilgan kasb nomni band qilib turmaydi."""
    from bot.jobs import clean_name

    name = clean_name(name)
    other = await get_job_by_name(session, job.owner_id, name)
    if other is not None and other.id != job.id:
        if other.is_active:
            return False
        other.name = f"{other.name[:40]} #{other.id}"
        await session.flush()
    job.name = name
    await session.flush()
    return True


async def delete_job(session: AsyncSession, job: Job) -> None:
    """Kasb ro'yxatdan olinadi, ishchilar kasbsiz qoladi. Eski yozuvlarda kasb nomi saqlanadi."""
    job.is_active = False
    workers = await session.scalars(select(User).where(User.owner_id == job.owner_id, User.job_id == job.id))
    for w in workers:
        w.job_id = None
    await session.flush()


async def set_job_price(session: AsyncSession, job: Job, price: Decimal | None, currency: str) -> None:
    """Kasbning o'z narxi (hamma modellar uchun). price=None — olib tashlanadi."""
    job.price = price
    job.currency = currency if price is not None else None
    await session.flush()


async def job_worker_counts(session: AsyncSession, owner_id: int) -> dict[int, int]:
    rows = await session.execute(
        select(User.job_id, func.count(User.id))
        .where(User.role == Role.WORKER, User.owner_id == owner_id, User.is_active.is_(True), User.job_id.is_not(None))
        .group_by(User.job_id)
    )
    return {job_id: int(n) for job_id, n in rows}


# --- Model narxlari: umumiy va kasb bo'yicha ---------------------------------

async def model_prices(session: AsyncSession, model_ids: list[int]) -> dict[int, dict[int, ModelPrice]]:
    """model_id -> {job_id: narx}."""
    if not model_ids:
        return {}
    out: dict[int, dict[int, ModelPrice]] = defaultdict(dict)
    for row in await session.scalars(select(ModelPrice).where(ModelPrice.model_id.in_(model_ids))):
        out[row.model_id][row.job_id] = row
    return dict(out)


async def set_model_price(
    session: AsyncSession, model: ItemModel, job_id: int | None, price: Decimal | None, currency: str
) -> None:
    """job_id=None — umumiy narx. price=None — narx olib tashlanadi."""
    if job_id is None:
        model.price = price
        if price is not None:
            model.currency = currency
        await session.flush()
        return
    row = await session.get(ModelPrice, (model.id, job_id))
    if price is None:
        if row is not None:
            await session.delete(row)
    elif row is None:
        session.add(ModelPrice(model_id=model.id, job_id=job_id, price=price, currency=currency))
    else:
        row.price, row.currency = price, currency
    await session.flush()


async def priced_jobs(session: AsyncSession, owner_id: int) -> set[int]:
    """Modellarga alohida narx qo'yilgan kasblar (faol modellarda): bunday kasbda ish topshirilganda model so'raladi —
    narx modelga qarab farq qiladi (Loafer kroyi va Sapog kroyi)."""
    rows = await session.scalars(
        select(ModelPrice.job_id)
        .join(ItemModel, ItemModel.id == ModelPrice.model_id)
        .where(ItemModel.owner_id == owner_id, ItemModel.is_active.is_(True))
        .distinct()
    )
    return set(rows)


async def clear_model_prices(session: AsyncSession, model_id: int) -> None:
    await session.execute(delete(ModelPrice).where(ModelPrice.model_id == model_id))


async def unit_price(
    session: AsyncSession, model: ItemModel | None, job_id: int | None
) -> tuple[Decimal | None, str | None]:
    """Bir dona uchun ish haqi: modelning shu kasb narxi, u bo'lmasa — kasbning o'z narxi, u ham bo'lmasa —
    modelning umumiy narxi. Model aytilmagan (kroy, brsovka) — kasbning o'z narxi."""
    if model is not None and job_id:
        row = await session.get(ModelPrice, (model.id, job_id))
        if row is not None:
            return row.price, row.currency
    job = await session.get(Job, job_id) if job_id else None
    if job is not None and job.price is not None:
        return job.price, job.currency
    if model is None or model.price is None:
        return None, None
    return model.price, model.currency


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
    model_id: int | None,
    quantity: int,
    work_date: date,
    status: str,
    raw_text: str | None,
    created_by: int,
    job_id: int | None = None,
) -> WorkEntry:
    model = await get_model(session, owner_id, model_id) if model_id else None
    price, currency = await unit_price(session, model, job_id)
    entry = WorkEntry(
        owner_id=owner_id,
        worker_id=worker_id,
        model_id=model_id,
        job_id=job_id,
        quantity=quantity,
        work_date=work_date,
        unit_price=price,
        price_currency=currency,
        status=status,
        raw_text=raw_text,
        created_by=created_by,
        # Vaqt ilovada qo'yiladi: bir saqlashdagi yozuvlar ham har xil vaqtda bo'ladi (hisob-kitob tartibi uchun)
        created_at=_now(),
    )
    session.add(entry)
    await session.flush()
    await session.refresh(entry, ["worker", "model", "job"])
    return entry


async def soft_delete(
    session: AsyncSession,
    entry: WorkEntry | PersonalEntry | DebtEntry | ClientEntry | MaterialEntry | WorkerPayment,
) -> None:
    entry.deleted_at = _now()
    await session.flush()


async def reset_entries(
    session: AsyncSession,
    owner_id: int,
    tables: tuple[type[WorkEntry | WorkerPayment | ClientEntry | MaterialEntry | PersonalEntry], ...],
) -> int:
    """Bo'lim hisobini 0 qilish: kabinetdagi shu jadvallarning hamma yozuvlari o'chiriladi (deleted_at bilan —
    bazada qoladi). Mijoz, ishchi, ta'minotchilarning o'zi saqlanadi. Qaytadi: nechta yozuv o'chdi."""
    now = _now()
    count = 0
    for table in tables:
        result = await session.execute(
            update(table)
            .where(table.owner_id == owner_id, table.deleted_at.is_(None))
            .values(deleted_at=now)
            .execution_options(synchronize_session=False)
        )
        count += result.rowcount or 0
    return count


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


async def reprice_entry(
    session: AsyncSession, entry: WorkEntry, model: ItemModel | None, job_id: int | None
) -> None:
    """Yozuvning modeli yoki kasbi o'zgarganda — narx hozirgi narxlardan olinadi."""
    entry.model_id = model.id if model is not None else None
    entry.job_id = job_id
    entry.unit_price, entry.price_currency = await unit_price(session, model, job_id)


# Narx yozuvda saqlanmagan bo'lsa (yozilgan paytda narx yo'q edi) — hozirgi narx: modelning kasb narxi, kasbning
# o'z narxi, modelning umumiy narxi
_REPORT_PRICE = func.coalesce(WorkEntry.unit_price, ModelPrice.price, Job.price, ItemModel.price)
_REPORT_CURRENCY = func.coalesce(WorkEntry.price_currency, ModelPrice.currency, Job.currency, ItemModel.currency)


def _work_rows_query(*columns):
    """Model aytilmagan ish (kroy, brsovka) ham kiradi — model nomi bo'sh."""
    return (
        select(*columns)
        .select_from(WorkEntry)
        .join(User, User.id == WorkEntry.worker_id)
        .outerjoin(ItemModel, ItemModel.id == WorkEntry.model_id)
        .outerjoin(Job, Job.id == WorkEntry.job_id)
        .outerjoin(
            ModelPrice, (ModelPrice.model_id == WorkEntry.model_id) & (ModelPrice.job_id == WorkEntry.job_id)
        )
    )


@dataclass
class WorkReportRow:
    worker_id: int
    worker_name: str
    model_name: str | None  # model aytilmagan ish — None
    quantity: int
    pay: Decimal | None  # narx kiritilmagan bo'lsa None
    currency: str | None
    job_name: str | None = None  # kasb ko'rsatilmagan bo'lsa None


async def work_report(
    session: AsyncSession, owner_id: int, start: date, end: date, worker_id: int | None = None
) -> list[WorkReportRow]:
    """Tasdiqlangan ishlar: ishchi, model va kasb bo'yicha jamlangan (start..end, ikkalasi ham kiradi)."""
    q = (
        _work_rows_query(
            User.id,
            User.name,
            ItemModel.name,
            func.sum(WorkEntry.quantity),
            func.sum(WorkEntry.quantity * _REPORT_PRICE),
            _REPORT_CURRENCY,
            Job.name,
        )
        .where(
            WorkEntry.owner_id == owner_id,
            WorkEntry.deleted_at.is_(None),
            WorkEntry.status == EntryStatus.APPROVED,
            WorkEntry.work_date >= start,
            WorkEntry.work_date <= end,
        )
        .group_by(User.id, User.name, ItemModel.id, ItemModel.name, _REPORT_CURRENCY, Job.id, Job.name, Job.position)
        .order_by(User.name, ItemModel.name, Job.position)
    )
    if worker_id is not None:
        q = q.where(WorkEntry.worker_id == worker_id)
    rows = await session.execute(q)
    return [
        WorkReportRow(
            r[0], r[1], r[2], int(r[3] or 0), Decimal(str(r[4])) if r[4] is not None else None, r[5], r[6]
        )
        for r in rows
    ]


@dataclass
class WorkStatRow:
    work_date: date
    worker_id: int
    worker_name: str
    model_name: str | None
    quantity: int
    pay: Decimal | None
    currency: str | None
    job_name: str | None = None


async def work_stat_rows(
    session: AsyncSession, owner_id: int, start: date, end: date, worker_id: int | None = None
) -> list[WorkStatRow]:
    """Tasdiqlangan ishlar: kun, ishchi, model va kasb bo'yicha jamlangan — grafiklar uchun."""
    q = (
        _work_rows_query(
            WorkEntry.work_date,
            User.id,
            User.name,
            ItemModel.name,
            func.sum(WorkEntry.quantity),
            func.sum(WorkEntry.quantity * _REPORT_PRICE),
            _REPORT_CURRENCY,
            Job.name,
        )
        .where(
            WorkEntry.owner_id == owner_id,
            WorkEntry.deleted_at.is_(None),
            WorkEntry.status == EntryStatus.APPROVED,
            WorkEntry.work_date >= start,
            WorkEntry.work_date <= end,
        )
        .group_by(
            WorkEntry.work_date, User.id, User.name, ItemModel.id, ItemModel.name, _REPORT_CURRENCY, Job.id, Job.name
        )
    )
    if worker_id is not None:
        q = q.where(WorkEntry.worker_id == worker_id)
    rows = await session.execute(q)
    return [
        WorkStatRow(
            r[0], r[1], r[2], r[3], int(r[4] or 0), Decimal(str(r[5])) if r[5] is not None else None, r[6], r[7]
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


# --- Ishchi bilan hisob-kitob: avans, ish haqi, qaytarilgan pul ---------------

async def create_payment(
    session: AsyncSession,
    *,
    owner_id: int,
    worker_id: int,
    kind: str,
    amount: Decimal,
    currency: str,
    note: str | None,
    entry_date: date,
    status: str,
    raw_text: str | None,
    created_by: int,
) -> WorkerPayment:
    """kind=BALANCE da amount — qoldiqning o'zi: musbat — egasi qarzdor, manfiy — ishchi ortiqcha olgan."""
    entry = WorkerPayment(
        owner_id=owner_id,
        worker_id=worker_id,
        kind=kind,
        amount=amount,
        currency=currency,
        note=note,
        entry_date=entry_date,
        status=status,
        raw_text=raw_text,
        created_by=created_by,
        created_at=_now(),
    )
    session.add(entry)
    await session.flush()
    await session.refresh(entry, ["worker"])
    return entry


async def get_payment(session: AsyncSession, owner_id: int, payment_id: int) -> WorkerPayment | None:
    entry = await session.get(WorkerPayment, payment_id)
    if entry is None or entry.deleted_at is not None or entry.owner_id != owner_id:
        return None
    return entry


async def review_payment(session: AsyncSession, entry: WorkerPayment, reviewer: User, approve: bool) -> None:
    entry.status = EntryStatus.APPROVED if approve else EntryStatus.REJECTED
    entry.reviewed_by = reviewer.id
    entry.reviewed_at = _now()
    await session.flush()


async def list_payments(
    session: AsyncSession,
    owner_id: int,
    *,
    worker_id: int | None = None,
    status: str | None = None,
    limit: int = 30,
    offset: int = 0,
) -> list[WorkerPayment]:
    q = (
        select(WorkerPayment)
        .where(WorkerPayment.owner_id == owner_id, WorkerPayment.deleted_at.is_(None))
        .order_by(WorkerPayment.entry_date.desc(), WorkerPayment.created_at.desc(), WorkerPayment.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if worker_id is not None:
        q = q.where(WorkerPayment.worker_id == worker_id)
    if status:
        q = q.where(WorkerPayment.status == status)
    return list(await session.scalars(q))


async def count_pending_payments(session: AsyncSession, owner_id: int, worker_id: int | None = None) -> int:
    q = select(func.count(WorkerPayment.id)).where(
        WorkerPayment.owner_id == owner_id,
        WorkerPayment.deleted_at.is_(None),
        WorkerPayment.status == EntryStatus.PENDING,
    )
    if worker_id is not None:
        q = q.where(WorkerPayment.worker_id == worker_id)
    return int(await session.scalar(q) or 0)


def _dec(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _add_to(totals: dict[str, Decimal], currency: str, value: Decimal) -> None:
    totals[currency] = totals.get(currency, Decimal(0)) + value


@dataclass
class WorkerBalance:
    worker_id: int
    # valyuta -> qoldiq: musbat — egasi ishchiga qarzdor (ishchi olishi kerak), manfiy — ishchi ortiqcha olgan
    balance: dict[str, Decimal] = field(default_factory=dict)
    # Oxirgi aytilgan hisob holati (since sanasida) va undan keyingi harakatlar; aytilmagan bo'lsa — boshidan
    since: date | None = None
    start: dict[str, Decimal] = field(default_factory=dict)
    earned: dict[str, Decimal] = field(default_factory=dict)  # tasdiqlangan ishlar summasi
    paid: dict[str, Decimal] = field(default_factory=dict)  # ishchi olgan pul
    returned: dict[str, Decimal] = field(default_factory=dict)  # ishchi qaytargan pul

    @property
    def settled(self) -> bool:
        return not any(self.balance.values())


def _before(date_col, ts_col, key: tuple[date, datetime]):
    d, ts = key
    return or_(date_col < d, and_(date_col == d, ts_col < ts))


def _after(date_col, ts_col, key: tuple[date, datetime]):
    d, ts = key
    return or_(date_col > d, and_(date_col == d, ts_col > ts))


async def worker_balances(
    session: AsyncSession,
    owner_id: int,
    worker_ids: list[int] | None = None,
    before: tuple[date, datetime] | None = None,
) -> dict[int, WorkerBalance]:
    """Ishchilar qoldig'i: oxirgi aytilgan hisob holati + undan keyingi ishlar − olingan + qaytarilgan pul.

    Faqat tasdiqlangan yozuvlar. before=(sana, vaqt) — shu paytgacha bo'lgan qoldiq (hisob-kitob ro'yxati uchun).
    Harakatlar tartibi: sana, keyin kiritilgan vaqti.
    """
    alive = (
        WorkerPayment.owner_id == owner_id,
        WorkerPayment.deleted_at.is_(None),
        WorkerPayment.status == EntryStatus.APPROVED,
    )
    st_q = select(WorkerPayment).where(*alive, WorkerPayment.kind == PayKind.BALANCE)
    if worker_ids is not None:
        st_q = st_q.where(WorkerPayment.worker_id.in_(worker_ids))
    if before is not None:
        st_q = st_q.where(_before(WorkerPayment.entry_date, WorkerPayment.created_at, before))
    statements: dict[int, WorkerPayment] = {}
    for p in await session.scalars(
        st_q.order_by(WorkerPayment.entry_date, WorkerPayment.created_at, WorkerPayment.id)
    ):
        statements[p.worker_id] = p  # eng oxirgisi qoladi

    def scope(date_col, ts_col, worker_col) -> list:
        conds = []
        if worker_ids is not None:
            conds.append(worker_col.in_(worker_ids))
        if statements:
            conds.append(
                or_(
                    worker_col.not_in(list(statements)),
                    *(
                        and_(worker_col == wid, _after(date_col, ts_col, (p.entry_date, p.created_at)))
                        for wid, p in statements.items()
                    ),
                )
            )
        if before is not None:
            conds.append(_before(date_col, ts_col, before))
        return conds

    out: dict[int, WorkerBalance] = {}

    def get(worker_id: int) -> WorkerBalance:
        return out.setdefault(worker_id, WorkerBalance(worker_id=worker_id))

    for wid, p in statements.items():
        b = get(wid)
        b.since = p.entry_date
        if p.amount:
            b.start[p.currency] = _dec(p.amount)

    work_q = (
        _work_rows_query(WorkEntry.worker_id, _REPORT_CURRENCY, func.sum(WorkEntry.quantity * _REPORT_PRICE))
        .where(
            WorkEntry.owner_id == owner_id,
            WorkEntry.deleted_at.is_(None),
            WorkEntry.status == EntryStatus.APPROVED,
            *scope(WorkEntry.work_date, WorkEntry.created_at, WorkEntry.worker_id),
        )
        .group_by(WorkEntry.worker_id, _REPORT_CURRENCY)
    )
    for wid, currency, total in await session.execute(work_q):
        if total is not None and currency:
            _add_to(get(wid).earned, currency, _dec(total))

    pay_q = (
        select(WorkerPayment.worker_id, WorkerPayment.kind, WorkerPayment.currency, func.sum(WorkerPayment.amount))
        .where(
            *alive,
            WorkerPayment.kind.in_((PayKind.PAY, PayKind.RETURN)),
            *scope(WorkerPayment.entry_date, WorkerPayment.created_at, WorkerPayment.worker_id),
        )
        .group_by(WorkerPayment.worker_id, WorkerPayment.kind, WorkerPayment.currency)
    )
    for wid, kind, currency, total in await session.execute(pay_q):
        b = get(wid)
        _add_to(b.paid if kind == PayKind.PAY else b.returned, currency, _dec(total))

    for b in out.values():
        balance: dict[str, Decimal] = {}
        for sign, part in ((1, b.start), (1, b.earned), (-1, b.paid), (1, b.returned)):
            for currency, value in part.items():
                _add_to(balance, currency, sign * value)
        b.balance = {c: v for c, v in balance.items() if v}
    return out


async def worker_balance(session: AsyncSession, owner_id: int, worker_id: int) -> WorkerBalance:
    return (await worker_balances(session, owner_id, [worker_id])).get(worker_id) or WorkerBalance(worker_id)


async def worker_day_payments(
    session: AsyncSession, owner_id: int, worker_id: int, days: list[date]
) -> dict[date, tuple[dict[str, Decimal], dict[str, Decimal]]]:
    """Kunlar bo'yicha ishchi olgan va qaytargan pul (tasdiqlanganlari): kun -> (olgan, qaytargan)."""
    out: dict[date, tuple[dict[str, Decimal], dict[str, Decimal]]] = {d: ({}, {}) for d in days}
    if not days:
        return out
    q = (
        select(WorkerPayment.entry_date, WorkerPayment.kind, WorkerPayment.currency, func.sum(WorkerPayment.amount))
        .where(
            WorkerPayment.owner_id == owner_id,
            WorkerPayment.worker_id == worker_id,
            WorkerPayment.deleted_at.is_(None),
            WorkerPayment.kind.in_((PayKind.PAY, PayKind.RETURN)),
            WorkerPayment.entry_date.in_(days),
            WorkerPayment.status == EntryStatus.APPROVED,
        )
        .group_by(WorkerPayment.entry_date, WorkerPayment.kind, WorkerPayment.currency)
    )
    for day, kind, currency, total in await session.execute(q):
        paid, returned = out.setdefault(day, ({}, {}))
        _add_to(paid if kind == PayKind.PAY else returned, currency, _dec(total))
    return out


@dataclass
class LedgerRow:
    kind: str  # "work" yoki PayKind
    entry: WorkEntry | WorkerPayment
    # Qoldiqqa ta'siri (ishda — ish haqi, olinganda — manfiy); BALANCE da — aytilgan qoldiq
    delta: dict[str, Decimal]
    counted: bool  # tasdiqlangan — qoldiqqa kirgan
    balance: dict[str, Decimal] = field(default_factory=dict)  # shu yozuvdan keyingi qoldiq

    @property
    def day(self) -> date:
        return self.entry.work_date if isinstance(self.entry, WorkEntry) else self.entry.entry_date


def _naive_utc(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.min
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


# Bir vaqtda kiritilgan bo'lsa: avval ish, keyin pul, oxirida hisob holati
_LEDGER_TIE = {"work": 0, PayKind.PAY: 1, PayKind.RETURN: 1, PayKind.BALANCE: 2}


def _ledger_key(row: LedgerRow) -> tuple:
    return (row.day, _naive_utc(row.entry.created_at), _LEDGER_TIE.get(row.kind, 1), row.entry.id)


def work_pay(entry: WorkEntry, prices: dict[int, dict[int, ModelPrice]]) -> tuple[Decimal | None, str | None]:
    """Yozuvning ish haqi — hisobotdagidek: yozilgan narx, bo'lmasa modelning kasb narxi, kasbning o'z narxi,
    modelning umumiy narxi."""
    price, currency = entry.unit_price, entry.price_currency
    if price is None:
        row = prices.get(entry.model_id, {}).get(entry.job_id) if entry.model_id and entry.job_id else None
        if row is not None:
            price, currency = row.price, row.currency
        elif entry.job is not None and entry.job.price is not None:
            price, currency = entry.job.price, entry.job.currency
        elif entry.model is not None:
            price, currency = entry.model.price, entry.model.currency
    if price is None or not currency:
        return None, None
    return _dec(price * entry.quantity), currency


async def worker_ledger(
    session: AsyncSession, owner_id: int, worker_id: int, limit: int = 30, offset: int = 0
) -> tuple[list[LedgerRow], bool]:
    """Ishchi hisob-kitobi (yangisi birinchi): ishlar va pul harakatlari, har biridan keyingi qoldiq bilan.
    Rad etilganlar ko'rsatilmaydi, tasdiq kutayotganlar — qoldiqqa kirmasdan."""
    need = offset + limit + 1
    works = list(
        await session.scalars(
            select(WorkEntry)
            .where(
                WorkEntry.owner_id == owner_id,
                WorkEntry.worker_id == worker_id,
                WorkEntry.deleted_at.is_(None),
                WorkEntry.status != EntryStatus.REJECTED,
            )
            .order_by(WorkEntry.work_date.desc(), WorkEntry.created_at.desc(), WorkEntry.id.desc())
            .limit(need)
        )
    )
    payments = [
        p
        for p in await list_payments(session, owner_id, worker_id=worker_id, limit=need)
        if p.status != EntryStatus.REJECTED
    ]
    prices = await model_prices(session, list({e.model_id for e in works if e.model_id}))

    rows: list[LedgerRow] = []
    for e in works:
        pay, currency = work_pay(e, prices)
        delta = {currency: pay} if pay else {}
        rows.append(LedgerRow("work", e, delta, e.status == EntryStatus.APPROVED))
    for p in payments:
        amount = _dec(p.amount)
        delta = {p.currency: -amount if p.kind == PayKind.PAY else amount}
        rows.append(LedgerRow(p.kind, p, delta, p.status == EntryStatus.APPROVED))
    rows.sort(key=_ledger_key, reverse=True)
    rows = rows[:need]

    # Hozirgi qoldiqdan orqaga: har bir yozuvdan keyingi qoldiq
    running = dict((await worker_balance(session, owner_id, worker_id)).balance)
    for row in rows[: offset + limit]:
        row.balance = {c: v for c, v in running.items() if v}
        if not row.counted:
            continue
        if row.kind == PayKind.BALANCE:
            prev = await worker_balances(session, owner_id, [worker_id], before=(row.day, row.entry.created_at))
            running = dict(prev[worker_id].balance) if worker_id in prev else {}
        else:
            for currency, value in row.delta.items():
                running[currency] = running.get(currency, Decimal(0)) - value
    return rows[offset : offset + limit], len(rows) > offset + limit


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


async def personal_entries_between(
    session: AsyncSession, owner_id: int, start: date, end: date
) -> list[PersonalEntry]:
    q = select(PersonalEntry).where(
        PersonalEntry.owner_id == owner_id,
        PersonalEntry.deleted_at.is_(None),
        PersonalEntry.entry_date >= start,
        PersonalEntry.entry_date <= end,
    )
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
    materials: int = 0  # shundan qarzga olingan xom ashyo yozuvlari
    # Umumiy qarzlardagi bo'limi (DEBT_SOURCES): debt — odamlar bilan qarz, material — ta'minotchi (qarzga xom ashyo
    # olingan yoki ro'yxatdagi ta'minotchi), worker — ishchi, client — mijoz
    source: str = "debt"
    ref_id: int | None = None  # ishchi yoki mijoz id si

    @property
    def settled(self) -> bool:
        return not any(self.balance.values())


# Umumiy qarzlar bo'limlari — shu tartibda ko'rsatiladi
DEBT_SOURCES = ("debt", "material", "worker", "client")


def _debt_order(b: DebtBalance) -> tuple:
    return b.settled, -(b.last_date or date.min).toordinal(), b.person.lower()


def _credit_materials(owner_id: int) -> tuple:
    """Qarzga olingan, narxi va ta'minotchisi aytilgan xom ashyo — ta'minotchiga qarz."""
    return (
        MaterialEntry.owner_id == owner_id,
        MaterialEntry.deleted_at.is_(None),
        MaterialEntry.on_credit.is_(True),
        MaterialEntry.amount.is_not(None),
        MaterialEntry.supplier.is_not(None),
        MaterialEntry.supplier != "",
    )


async def debt_balances(session: AsyncSession, owner_id: int) -> list[DebtBalance]:
    """Odamlar bo'yicha qarz qoldig'i. Qarzga olingan xom ashyo — shu ismli ta'minotchiga qarz: uning qarz
    yozuvlari bilan bitta qatorda («Aslcharmdan qarzga teri oldim» va «Aslcharmga 10 million berdim»)."""
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

    credit = await session.execute(
        select(
            MaterialEntry.supplier,
            MaterialEntry.currency,
            func.sum(MaterialEntry.amount),
            func.max(MaterialEntry.entry_date),
            func.count(MaterialEntry.id),
        )
        .where(*_credit_materials(owner_id))
        .group_by(MaterialEntry.supplier, MaterialEntry.currency)
    )
    for supplier, currency, total, last, count in credit:
        key = person_key(supplier)
        b = balances.setdefault(key, DebtBalance(key=key, person=supplier))
        b.balance[currency] = b.balance.get(currency, Decimal(0)) - Decimal(str(total or 0))
        b.last_date = max(filter(None, [b.last_date, last]), default=None)
        b.count += int(count)
        b.materials += int(count)

    # Ism birinchi yozilganidek ko'rsatiladi (keyingi yozuvlar ham shu ismga moslanadi)
    names = await session.execute(
        select(DebtEntry.person_key, DebtEntry.person).where(*alive).order_by(DebtEntry.id.desc())
    )
    for key, person in names:
        if key in balances:
            balances[key].person = person

    result = list(balances.values())
    result.sort(key=_debt_order)
    return result


async def list_credit_materials(
    session: AsyncSession, owner_id: int, key: str | None = None, limit: int = 30
) -> list[MaterialEntry]:
    """Qarzga olingan xom ashyo (yangisi birinchi); key — shu odamdan olinganlari (debt_balances dagi kalit)."""
    q = (
        select(MaterialEntry)
        .where(*_credit_materials(owner_id))
        .order_by(MaterialEntry.entry_date.desc(), MaterialEntry.id.desc())
    )
    if key is None:
        return list(await session.scalars(q.limit(limit)))
    return [e for e in await session.scalars(q) if person_key(e.supplier) == key][:limit]


async def _worker_last_dates(session: AsyncSession, owner_id: int) -> dict[int, date]:
    """Ishchi bilan oxirgi tasdiqlangan ish yoki pul harakati sanasi."""
    out: dict[int, date] = {}
    for worker_col, date_col, table in (
        (WorkEntry.worker_id, WorkEntry.work_date, WorkEntry),
        (WorkerPayment.worker_id, WorkerPayment.entry_date, WorkerPayment),
    ):
        q = (
            select(worker_col, func.max(date_col))
            .where(table.owner_id == owner_id, table.deleted_at.is_(None), table.status == EntryStatus.APPROVED)
            .group_by(worker_col)
        )
        for worker_id, day in await session.execute(q):
            if day is not None:
                out[worker_id] = max(out.get(worker_id, day), day)
    return out


async def _debts_with_suppliers(session: AsyncSession, owner_id: int) -> list[DebtBalance]:
    """debt_balances, ta'minotchilar bilan qarzlar «material» bo'limida: qarzga xom ashyo olingan yoki ismi
    ro'yxatdagi ta'minotchiniki."""
    rows = await debt_balances(session, owner_id)
    suppliers = {person_key(s.name) for s in await list_suppliers(session, owner_id, active_only=False)}
    for b in rows:
        if b.materials or b.key in suppliers:
            b.source = "material"
    return rows


async def supplier_debts(session: AsyncSession, owner_id: int) -> list[DebtBalance]:
    """Xom ashyo bo'limi uchun: ta'minotchilar bilan ochiq qarzlar (umumiy qarzlardagi «material» bo'limi)."""
    return [b for b in await _debts_with_suppliers(session, owner_id) if b.source == "material" and not b.settled]


def debt_groups(rows: list[DebtBalance]) -> dict[str, tuple[dict[str, Decimal], dict[str, Decimal]]]:
    """Bo'lim -> (menga qarzlar, mening qarzlarim), faqat qatori bor bo'limlar, DEBT_SOURCES tartibida."""
    return {
        source: debt_totals([b for b in rows if b.source == source])
        for source in DEBT_SOURCES
        if any(b.source == source for b in rows)
    }


async def debt_overview(session: AsyncSession, owner_id: int) -> list[DebtBalance]:
    """Umumiy qarzlar: qarz yozuvlari, ta'minotchilar (qarzga olingan xom ashyo bilan), ishchilar va mijozlar
    qoldig'i — har biri o'z bo'limida (source), jami hammasidan.

    Ishchi — pul yoki hisob holati yozilgan bo'lsa (yozilmagan bo'lsa qoldiq — hamma ishlari summasi, ishchilar
    ro'yxatidagidek hisobga olinmaydi): ishchining haqqi — mening qarzim, ortiqcha olgani (avans) — uning qarzi.
    Mijoz: qarzi — menga qarz, oldindan to'lagani — mening qarzim. Hisobi yopilgan ishchi va mijoz ko'rsatilmaydi.
    Oddiy xom ashyo xaridi (qarzga olinmagan) bu yerga kirmaydi."""
    rows = await _debts_with_suppliers(session, owner_id)

    workers = {w.id: w for w in await list_workers(session, owner_id, active_only=False)}
    last = await _worker_last_dates(session, owner_id)
    for worker_id, wb in (await worker_balances(session, owner_id)).items():
        worker = workers.get(worker_id)
        if worker is None or not wb.balance or not (wb.since or wb.paid or wb.returned):
            continue
        rows.append(
            DebtBalance(
                key=f"worker:{worker_id}",
                person=worker.name,
                balance={c: -v for c, v in wb.balance.items()},
                last_date=last.get(worker_id),
                source="worker",
                ref_id=worker_id,
            )
        )

    for st in await client_stats(session, owner_id):
        balance = {c: v for c, v in st.balance.items() if v}
        if balance:
            rows.append(
                DebtBalance(
                    key=f"client:{st.client.id}",
                    person=st.client.name,
                    balance=balance,
                    last_date=st.last_date,
                    count=st.count,
                    source="client",
                    ref_id=st.client.id,
                )
            )
    rows.sort(key=_debt_order)
    return rows


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
    """Yangi mijoz — taklif havolasi bilan: mijoz bosganda botga ulanadi va oldi-berdini ko'radi."""
    client = Client(owner_id=owner_id, name=name.strip()[:100], phone=phone, invite_token=secrets.token_urlsafe(12))
    session.add(client)
    await session.flush()
    return client


async def get_or_create_client(session: AsyncSession, owner_id: int, name: str) -> tuple[Client, bool]:
    """(mijoz, yangi qo'shildimi)."""
    client = await get_client_by_name(session, owner_id, name)
    if client is None:
        return await create_client(session, owner_id, name), True
    if not client.is_active:
        client.is_active = True
    return client, False


async def new_client_token(session: AsyncSession, client: Client) -> str:
    client.invite_token = secrets.token_urlsafe(12)
    await session.flush()
    return client.invite_token


async def link_client(session: AsyncSession, token: str, tg_id: int) -> Client | None:
    client = await session.scalar(select(Client).where(Client.invite_token == token))
    if client is None or not client.is_active:
        return None
    # Bir egada bitta Telegram akkaunt — bitta mijoz: avval boshqa mijozga ulangan bo'lsa, o'shasidan ajratiladi
    await session.execute(
        update(Client)
        .where(Client.owner_id == client.owner_id, Client.tg_id == tg_id, Client.id != client.id)
        .values(tg_id=None)
        .execution_options(synchronize_session=False)
    )
    client.tg_id = tg_id
    client.invite_token = None
    await session.flush()
    return client


async def clients_by_tg(session: AsyncSession, tg_id: int) -> list[Client]:
    """Shu Telegram akkaunt ulangan faol mijozlar (har xil egalarda bo'lishi mumkin)."""
    q = select(Client).where(Client.tg_id == tg_id, Client.is_active.is_(True)).order_by(Client.id)
    return list(await session.scalars(q))


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
    by_client: bool = False,
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
        by_client=by_client,
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


def _client_delta(e: ClientEntry) -> Decimal:
    """Yozuvning qoldiqqa ta'siri: mol — qarz qo'shiladi, to'lov va qaytgan mol — ayriladi."""
    return _dec(e.amount) if e.kind == ClientKind.SALE else -_dec(e.amount)


async def client_balance(session: AsyncSession, owner_id: int, client_id: int) -> dict[str, Decimal]:
    """Valyuta -> qoldiq: musbat — mijoz qarzdor, manfiy — oldindan to'lagan."""
    signed = case((ClientEntry.kind == ClientKind.SALE, ClientEntry.amount), else_=-ClientEntry.amount)
    rows = await session.execute(
        select(ClientEntry.currency, func.sum(signed))
        .where(ClientEntry.owner_id == owner_id, ClientEntry.client_id == client_id, ClientEntry.deleted_at.is_(None))
        .group_by(ClientEntry.currency)
    )
    return {currency: _dec(total) for currency, total in rows if _dec(total)}


@dataclass
class ClientLedgerRow:
    entry: ClientEntry
    balance: dict[str, Decimal]  # shu yozuvdan keyingi qoldiq


async def client_ledger(
    session: AsyncSession, owner_id: int, client_id: int, limit: int = 30
) -> tuple[list[ClientLedgerRow], bool]:
    """Mijoz bilan oldi-berdi (yangisi birinchi), har biridan keyingi qoldiq bilan. Qaytadi: (qatorlar, yana bormi)."""
    entries = await list_client_entries(session, owner_id, client_id=client_id, limit=limit + 1)
    running = await client_balance(session, owner_id, client_id)
    rows = []
    for e in entries[:limit]:
        rows.append(ClientLedgerRow(e, {c: v for c, v in running.items() if v}))
        running[e.currency] = running.get(e.currency, Decimal(0)) - _client_delta(e)
    return rows, len(entries) > limit


# --- Xom ashyo --------------------------------------------------------------

async def create_material_entry(
    session: AsyncSession,
    *,
    owner_id: int,
    kind: str,
    name: str | None,
    quantity: Decimal | None,
    unit: str | None,
    unit_price: Decimal | None,
    amount: Decimal | None,
    currency: str,
    supplier: str | None,
    entry_date: date,
    raw_text: str | None,
    created_by: int,
    supplier_id: int | None = None,
    on_credit: bool = False,
) -> MaterialEntry:
    entry = MaterialEntry(
        owner_id=owner_id,
        kind=kind[:50],
        name=name,
        quantity=quantity,
        unit=unit,
        unit_price=unit_price,
        amount=amount,
        currency=currency,
        supplier=supplier,
        supplier_id=supplier_id,
        on_credit=on_credit,
        entry_date=entry_date,
        raw_text=raw_text,
        created_by=created_by,
    )
    session.add(entry)
    await session.flush()
    return entry


async def get_material_entry(session: AsyncSession, owner_id: int, entry_id: int) -> MaterialEntry | None:
    entry = await session.get(MaterialEntry, entry_id)
    if entry is None or entry.deleted_at is not None or entry.owner_id != owner_id:
        return None
    return entry


async def list_material_entries(
    session: AsyncSession, owner_id: int, *, supplier_id: int | None = None, limit: int = 30, offset: int = 0
) -> list[MaterialEntry]:
    q = (
        select(MaterialEntry)
        .where(MaterialEntry.owner_id == owner_id, MaterialEntry.deleted_at.is_(None))
        .order_by(MaterialEntry.entry_date.desc(), MaterialEntry.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if supplier_id is not None:
        q = q.where(MaterialEntry.supplier_id == supplier_id)
    return list(await session.scalars(q))


@dataclass
class MaterialStatRow:
    entry_date: date
    kind: str
    unit: str | None
    currency: str
    quantity: Decimal
    amount: Decimal
    count: int


async def material_stat_rows(session: AsyncSession, owner_id: int, start: date, end: date) -> list[MaterialStatRow]:
    """Kun, tur, o'lchov birligi va valyuta bo'yicha jamlangan xaridlar."""
    q = (
        select(
            MaterialEntry.entry_date,
            MaterialEntry.kind,
            MaterialEntry.unit,
            MaterialEntry.currency,
            func.sum(MaterialEntry.quantity),
            func.sum(MaterialEntry.amount),
            func.count(MaterialEntry.id),
        )
        .where(
            MaterialEntry.owner_id == owner_id,
            MaterialEntry.deleted_at.is_(None),
            MaterialEntry.entry_date >= start,
            MaterialEntry.entry_date <= end,
        )
        .group_by(MaterialEntry.entry_date, MaterialEntry.kind, MaterialEntry.unit, MaterialEntry.currency)
    )
    rows = await session.execute(q)
    return [
        MaterialStatRow(r[0], r[1], r[2], r[3], Decimal(str(r[4] or 0)), Decimal(str(r[5] or 0)), int(r[6]))
        for r in rows
    ]


async def _material_values(session: AsyncSession, owner_id: int, column) -> list[str]:
    """Avval yozilgan turlar yoki nomlar — ko'p ishlatilgani birinchi (LLM bir xil yozishi uchun)."""
    q = (
        select(column)
        .where(MaterialEntry.owner_id == owner_id, MaterialEntry.deleted_at.is_(None), column.is_not(None))
        .group_by(column)
        .order_by(func.count(MaterialEntry.id).desc())
        .limit(40)
    )
    return list(await session.scalars(q))


async def material_kinds(session: AsyncSession, owner_id: int) -> list[str]:
    return await _material_values(session, owner_id, MaterialEntry.kind)


async def material_names(session: AsyncSession, owner_id: int) -> list[str]:
    return await _material_values(session, owner_id, MaterialEntry.name)


# --- Ta'minotchilar: xom ashyo kimdan olinadi ---------------------------------

async def list_suppliers(session: AsyncSession, owner_id: int, active_only: bool = True) -> list[Supplier]:
    q = select(Supplier).where(Supplier.owner_id == owner_id).order_by(Supplier.name)
    if active_only:
        q = q.where(Supplier.is_active.is_(True))
    return list(await session.scalars(q))


async def get_supplier(session: AsyncSession, owner_id: int, supplier_id: int) -> Supplier | None:
    supplier = await session.get(Supplier, supplier_id)
    return supplier if supplier is not None and supplier.owner_id == owner_id else None


async def get_supplier_by_name(session: AsyncSession, owner_id: int, name: str) -> Supplier | None:
    return await session.scalar(
        select(Supplier).where(Supplier.owner_id == owner_id, func.lower(Supplier.name) == name.strip().lower())
    )


async def create_supplier(session: AsyncSession, owner_id: int, name: str) -> Supplier:
    """Yangi ta'minotchi (taklif havolasi bilan). Avval xuddi shu ism bilan yozilgan xaridlar ham unga bog'lanadi."""
    supplier = Supplier(owner_id=owner_id, name=name.strip()[:100], invite_token=secrets.token_urlsafe(12))
    session.add(supplier)
    await session.flush()
    await session.execute(
        update(MaterialEntry)
        .where(
            MaterialEntry.owner_id == owner_id,
            MaterialEntry.supplier_id.is_(None),
            func.lower(MaterialEntry.supplier) == supplier.name.lower(),
        )
        .values(supplier_id=supplier.id)
        .execution_options(synchronize_session=False)
    )
    return supplier


async def new_supplier_token(session: AsyncSession, supplier: Supplier) -> str:
    supplier.invite_token = secrets.token_urlsafe(12)
    await session.flush()
    return supplier.invite_token


async def link_supplier(session: AsyncSession, token: str, tg_id: int) -> Supplier | None:
    supplier = await session.scalar(select(Supplier).where(Supplier.invite_token == token))
    if supplier is None or not supplier.is_active:
        return None
    supplier.tg_id = tg_id
    supplier.invite_token = None
    await session.flush()
    return supplier


async def suppliers_by_tg(session: AsyncSession, tg_id: int) -> list[Supplier]:
    """Shu Telegram akkaunt ulangan faol ta'minotchilar (har xil egalarda bo'lishi mumkin)."""
    q = select(Supplier).where(Supplier.tg_id == tg_id, Supplier.is_active.is_(True)).order_by(Supplier.id)
    return list(await session.scalars(q))


async def supplier_material_entries(
    session: AsyncSession, supplier_ids: list[int], limit: int = 10
) -> list[MaterialEntry]:
    """Ta'minotchilardan olingan xom ashyo — yangisi birinchi (ta'minotchining o'ziga ko'rsatish uchun)."""
    if not supplier_ids:
        return []
    q = (
        select(MaterialEntry)
        .where(MaterialEntry.supplier_id.in_(supplier_ids), MaterialEntry.deleted_at.is_(None))
        .order_by(MaterialEntry.entry_date.desc(), MaterialEntry.id.desc())
        .limit(limit)
    )
    return list(await session.scalars(q))


@dataclass
class SupplierStats:
    month: dict[str, Decimal] = field(default_factory=dict)  # shu oy olingan xom ashyo summasi
    month_count: int = 0
    total: dict[str, Decimal] = field(default_factory=dict)
    count: int = 0
    last_date: date | None = None


async def supplier_stats(session: AsyncSession, owner_id: int, month_start: date) -> dict[int, SupplierStats]:
    """Ta'minotchi -> undan olingan xom ashyo: shu oy va jami (narxi aytilmaganlari faqat sonda)."""
    rows = await session.execute(
        select(
            MaterialEntry.supplier_id,
            MaterialEntry.currency,
            MaterialEntry.entry_date,
            func.sum(MaterialEntry.amount),
            func.count(MaterialEntry.id),
        )
        .where(
            MaterialEntry.owner_id == owner_id,
            MaterialEntry.deleted_at.is_(None),
            MaterialEntry.supplier_id.is_not(None),
        )
        .group_by(MaterialEntry.supplier_id, MaterialEntry.currency, MaterialEntry.entry_date)
    )
    out: dict[int, SupplierStats] = {}
    for supplier_id, currency, entry_date, amount, count in rows:
        st = out.setdefault(supplier_id, SupplierStats())
        amount = Decimal(str(amount or 0))
        if amount:
            _add_to(st.total, currency, amount)
        st.count += int(count)
        st.last_date = max(filter(None, [st.last_date, entry_date]), default=None)
        if entry_date >= month_start:
            if amount:
                _add_to(st.month, currency, amount)
            st.month_count += int(count)
    return out
