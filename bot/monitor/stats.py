"""Kuzatuv boti uchun hisob-kitoblar: foydalanuvchilar, harakatlar, kiritilgan yozuvlar, xatolar."""
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from html import escape

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import ActivityKind as K
from bot.db.models import (
    ActivityLog,
    Client,
    ClientEntry,
    ClientKind,
    DebtDirection,
    DebtEntry,
    ItemModel,
    PersonalEntry,
    PersonalKind,
    User,
    WorkEntry,
    WorkerPayment,
)
from bot.monitor.tracker import ICONS, USER_KINDS, cut, display_name, line, local, role_of
from bot.services.utils import fmt_money


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def day_start(days: int) -> datetime:
    """Bugundan (days − 1) kun oldingi kun boshi (mahalliy vaqt bilan), UTC da."""
    first = datetime.now(settings.tz).date() - timedelta(days=days - 1)
    return datetime.combine(first, time(), tzinfo=settings.tz).astimezone(timezone.utc)


def ago(dt: datetime | None) -> str:
    if dt is None:
        return "hali ishlatmagan"
    now = datetime.now(settings.tz)
    d = local(dt)
    seconds = (now - d).total_seconds()
    if seconds < 120:
        return "hozirgina"
    if seconds < 3600:
        return f"{int(seconds // 60)} daqiqa oldin"
    if d.date() == now.date():
        return f"bugun {d:%H:%M}"
    if d.date() == now.date() - timedelta(days=1):
        return f"kecha {d:%H:%M}"
    return d.strftime("%d.%m.%Y %H:%M")


def dot(dt: datetime | None) -> str:
    if dt is None:
        return "⚫"
    seconds = (utcnow() - local(dt)).total_seconds()
    return "🟢" if seconds < 3600 else "🟡" if seconds < 24 * 3600 else "⚪"


# --- Foydalanuvchilar -------------------------------------------------------

@dataclass
class UserRow:
    tg_id: int | None
    user: User | None
    name: str
    role: str
    last_seen: datetime | None
    day: int  # so'nggi 24 soatdagi harakatlar
    week: int  # so'nggi 7 kundagi harakatlar


async def users_overview(session: AsyncSession) -> list[UserRow]:
    now = utcnow()
    query = (
        select(
            ActivityLog.tg_id,
            func.max(ActivityLog.created_at),
            func.sum(case((ActivityLog.created_at >= now - timedelta(days=1), 1), else_=0)),
            func.sum(case((ActivityLog.created_at >= now - timedelta(days=7), 1), else_=0)),
        )
        .where(ActivityLog.kind.in_(USER_KINDS), ActivityLog.tg_id.is_not(None))
        .group_by(ActivityLog.tg_id)
    )
    activity = {tg: (last, int(day or 0), int(week or 0)) for tg, last, day, week in await session.execute(query)}
    rows = []
    for user in (await session.execute(select(User).order_by(User.id))).scalars():
        last, day, week = activity.pop(user.tg_id, (None, 0, 0)) if user.tg_id else (None, 0, 0)
        name = display_name(user.tg_id, user) if user.tg_id else user.name
        rows.append(UserRow(user.tg_id, user, name, role_of(user, user.tg_id or 0), last, day, week))
    for tg_id, (last, day, week) in activity.items():
        rows.append(UserRow(tg_id, None, display_name(tg_id, None), role_of(None, tg_id), last, day, week))
    rows.sort(key=lambda r: local(r.last_seen).timestamp() if r.last_seen else 0, reverse=True)
    return rows


async def first_last(session: AsyncSession, tg_id: int) -> tuple[datetime | None, datetime | None]:
    query = select(func.min(ActivityLog.created_at), func.max(ActivityLog.created_at)).where(
        ActivityLog.tg_id == tg_id, ActivityLog.kind.in_(USER_KINDS)
    )
    first, last = (await session.execute(query)).one()
    return first, last


KIND_LABELS = [
    (K.VOICE, "ovoz"),
    (K.TEXT, "matn"),
    (K.PHOTO, "rasm"),
    (K.BUTTON, "tugma"),
    (K.COMMAND, "buyruq"),
    (K.OPEN, "ilovani ochdi"),
    (K.VIEW, "ilovada ko'rdi"),
    (K.ACTION, "ilovada o'zgartirdi"),
    (K.OTHER, "boshqa"),
    (K.ERROR, "xato"),
]


async def kind_counts(session: AsyncSession, tg_id: int) -> str:
    week_ago = utcnow() - timedelta(days=7)
    query = (
        select(
            ActivityLog.kind,
            func.sum(case((ActivityLog.created_at >= week_ago, 1), else_=0)),
            func.count(),
        )
        .where(ActivityLog.tg_id == tg_id)
        .group_by(ActivityLog.kind)
    )
    counts = {kind: (int(week or 0), total) for kind, week, total in await session.execute(query)}
    parts = [
        f"{ICONS[kind]} {label}: {counts[kind][0]} / {counts[kind][1]}"
        for kind, label in KIND_LABELS
        if kind in counts
    ]
    return "\n".join(parts) if parts else "Hali harakat yo'q."


async def _count(session: AsyncSession, model, *where) -> tuple[int, int]:
    week_ago = utcnow() - timedelta(days=7)
    query = select(func.count(), func.sum(case((model.created_at >= week_ago, 1), else_=0))).where(*where)
    total, week = (await session.execute(query)).one()
    return int(total or 0), int(week or 0)


async def entry_counts(session: AsyncSession, user: User) -> str:
    """Kiritgan yozuvlari: jami / so'nggi 7 kun (o'chirilganlarsiz)."""
    if not user.is_owner:
        total, week = await _count(session, WorkEntry, WorkEntry.worker_id == user.id, WorkEntry.deleted_at.is_(None))
        return f"🧵 ish yozuvlari: {total} / {week}"
    items = [
        ("💸 xarajat", PersonalEntry, [PersonalEntry.kind == PersonalKind.EXPENSE]),
        ("💰 daromad", PersonalEntry, [PersonalEntry.kind == PersonalKind.INCOME]),
        ("🤝 qarz", DebtEntry, []),
        ("🛍 mijoz", ClientEntry, []),
        ("🧵 ish", WorkEntry, []),
    ]
    parts = []
    for label, model, where in items:
        total, week = await _count(session, model, model.owner_id == user.id, model.deleted_at.is_(None), *where)
        parts.append(f"{label}: {total} / {week}")
    workers = await session.scalar(select(func.count()).select_from(User).where(User.owner_id == user.id))
    models = await session.scalar(select(func.count()).select_from(ItemModel).where(ItemModel.owner_id == user.id))
    clients = await session.scalar(select(func.count()).select_from(Client).where(Client.owner_id == user.id))
    parts.append(f"👷 ishchilar: {workers} · 🧵 modellar: {models} · 🛍 mijozlar: {clients}")
    return "\n".join(parts)


# --- Yozuvlari --------------------------------------------------------------

def _raw(text: str | None) -> str:
    return f"\n    <i>«{escape(cut(text, 120))}»</i>" if text else ""


def _when(e) -> str:
    deleted = " 🗑 o'chirilgan" if e.deleted_at is not None else ""
    return f"{local(e.created_at):%d.%m %H:%M}{deleted}"


CLIENT_KINDS = {ClientKind.SALE: "mol berildi", ClientKind.PAYMENT: "to'lov", ClientKind.RETURN: "qaytardi"}


async def recent_entries(session: AsyncSession, user: User, limit: int = 12) -> list[tuple[datetime, str]]:
    """Oxirgi kiritilgan yozuvlar (o'chirilganlari ham — belgisi bilan)."""
    items: list[tuple[datetime, str]] = []
    if user.is_owner:
        for e in (
            await session.execute(
                select(PersonalEntry)
                .where(PersonalEntry.owner_id == user.id)
                .order_by(PersonalEntry.id.desc())
                .limit(limit)
            )
        ).scalars():
            icon = "💸" if e.kind == PersonalKind.EXPENSE else "💰"
            note = f" · {escape(e.note)}" if e.note else ""
            items.append((e.created_at, (
                f"{icon} {_when(e)} · {escape(e.category)} — <b>{fmt_money(e.amount, e.currency)}</b>"
                f"{note} (sana {e.entry_date:%d.%m}){_raw(e.raw_text)}"
            )))
        for e in (
            await session.execute(
                select(DebtEntry).where(DebtEntry.owner_id == user.id).order_by(DebtEntry.id.desc()).limit(limit)
            )
        ).scalars():
            way = "berildi ➡️" if e.direction == DebtDirection.OUT else "olindi ⬅️"
            if e.stated:
                way = "u qarzdor (holat)" if e.direction == DebtDirection.OUT else "men qarzdorman (holat)"
            items.append((e.created_at, (
                f"🤝 {_when(e)} · {escape(e.person)} — <b>{fmt_money(e.amount, e.currency)}</b> {way}{_raw(e.raw_text)}"
            )))
        for e in (
            await session.execute(
                select(ClientEntry).where(ClientEntry.owner_id == user.id).order_by(ClientEntry.id.desc()).limit(limit)
            )
        ).scalars():
            what = CLIENT_KINDS.get(e.kind, e.kind)
            goods = f" {escape(e.product)}" if e.product else ""
            sets = f" {e.sets} komplekt" if e.sets else ""
            amount = f" — <b>{fmt_money(e.amount, e.currency)}</b>" if e.amount is not None else ""
            items.append((e.created_at, (
                f"🛍 {_when(e)} · {escape(e.client.name)}: {what}{sets}{goods}{amount}{_raw(e.raw_text)}"
            )))
    work_where = WorkEntry.owner_id == user.id if user.is_owner else WorkEntry.worker_id == user.id
    for e in (
        await session.execute(select(WorkEntry).where(work_where).order_by(WorkEntry.id.desc()).limit(limit))
    ).scalars():
        items.append((e.created_at, (
            f"🧵 {_when(e)} · {escape(e.worker.name)}: {escape(e.model.name)} × {e.quantity} "
            f"(sana {e.work_date:%d.%m}, {e.status}){_raw(e.raw_text)}"
        )))
    items.sort(key=lambda item: local(item[0]).timestamp(), reverse=True)
    return items[:limit]


# --- Suhbat tarixi ----------------------------------------------------------

HISTORY_BUDGET = 3500


async def history(session: AsyncSession, tg_id: int, before_id: int = 0) -> tuple[list[str], int, bool]:
    """Suhbat (eskisi tepada). Qaytaradi: qatorlar, eng eski yozuv id si, yana eskisi bormi."""
    query = select(ActivityLog).where(ActivityLog.tg_id == tg_id)
    if before_id:
        query = query.where(ActivityLog.id < before_id)
    rows = (await session.execute(query.order_by(ActivityLog.id.desc()).limit(80))).scalars().all()
    picked, used = [], 0
    for row in rows:
        size = len(row.text) + 20
        if picked and used + min(size, 420) > HISTORY_BUDGET:
            break
        picked.append(row)
        used += min(size, 420)
    picked.reverse()
    lines, day = [], None
    for row in picked:
        at = local(row.created_at)
        if at.date() != day:
            day = at.date()
            lines.append(f"\n📅 <b>{at:%d.%m.%Y}</b>")
        lines.append(f"<code>{at:%H:%M}</code> {line(row.kind, row.source, row.text, limit=400)}")
    has_more = len(picked) < len(rows) or len(rows) == 80
    return lines, picked[0].id if picked else 0, has_more


async def recent_errors(session: AsyncSession, limit: int = 8) -> list[ActivityLog]:
    query = select(ActivityLog).where(ActivityLog.kind == K.ERROR).order_by(ActivityLog.id.desc()).limit(limit)
    return list((await session.execute(query)).scalars())


# --- Umumiy statistika ------------------------------------------------------

async def _entries_since(session: AsyncSession, since: datetime) -> dict[int, int]:
    """Kim (users.id) shu vaqtdan beri nechta yozuv kiritdi."""
    result: dict[int, int] = {}
    for model, who in (
        (PersonalEntry, PersonalEntry.owner_id),
        (DebtEntry, DebtEntry.owner_id),
        (ClientEntry, ClientEntry.created_by),
        (WorkEntry, WorkEntry.created_by),
        (WorkerPayment, WorkerPayment.created_by),
    ):
        query = select(who, func.count()).where(model.created_at >= since).group_by(who)
        for user_id, count in await session.execute(query):
            result[user_id] = result.get(user_id, 0) + count
    return result


async def summary(session: AsyncSession, days: int) -> str:
    since = day_start(days)
    query = (
        select(ActivityLog.tg_id, ActivityLog.kind, func.count())
        .where(ActivityLog.created_at >= since, ActivityLog.tg_id.is_not(None))
        .group_by(ActivityLog.tg_id, ActivityLog.kind)
    )
    per_user: dict[int, dict[str, int]] = {}
    for tg_id, kind, count in await session.execute(query):
        per_user.setdefault(tg_id, {})[kind] = count
    system_errors = await session.scalar(
        select(func.count()).where(
            ActivityLog.kind == K.ERROR, ActivityLog.tg_id.is_(None), ActivityLog.created_at >= since
        )
    )
    entries = await _entries_since(session, since)
    users = {u.tg_id: u for u in (await session.execute(select(User).where(User.tg_id.is_not(None)))).scalars()}

    title = "bugun" if days == 1 else f"so'nggi {days} kun"
    lines = [f"📈 <b>Statistika — {title}</b> ({local(since):%d.%m} dan)\n"]
    active = sorted(
        (tg for tg, kinds in per_user.items() if any(kinds.get(k) for k in USER_KINDS)),
        key=lambda tg: -sum(per_user[tg].get(k, 0) for k in USER_KINDS),
    )
    total_actions = total_entries = total_errors = 0
    for tg_id in active:
        kinds = per_user[tg_id]
        user = users.get(tg_id)
        actions = sum(kinds.get(k, 0) for k in USER_KINDS)
        saved = entries.get(user.id, 0) if user else 0
        errors = kinds.get(K.ERROR, 0)
        total_actions += actions
        total_entries += saved
        total_errors += errors
        detail = " · ".join(f"{ICONS[k]} {kinds[k]}" for k, _ in KIND_LABELS if k != K.ERROR and kinds.get(k))
        lines.append(
            f"👤 <b>{escape(display_name(tg_id, user))}</b> ({role_of(user, tg_id)})\n"
            f"    {actions} ta harakat: {detail}\n"
            f"    🧾 {saved} ta yozuv saqlandi" + (f" · ⚠️ {errors} ta xato" if errors else "")
        )
    if not active:
        lines.append("Hech kim botni ishlatmadi.")
    total_errors += system_errors or 0
    lines.append(
        f"\n<b>Jami:</b> {len(active)} kishi · {total_actions} ta harakat · {total_entries} ta yozuv"
        + (f" · ⚠️ {total_errors} ta xato" if total_errors else "")
    )
    return "\n".join(lines)
