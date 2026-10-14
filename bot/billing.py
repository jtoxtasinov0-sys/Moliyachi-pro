"""Obuna va limitlar: bosh admin qo'shgan egalar uchun tarif, muddat va oylik sarf.

Bosh admin (OWNER_TG_ID dagi ID) va uning ishchilari — cheklovsiz. Boshqa egada:
- paid_until — to'langan oxirgi kun. Undan keyin kabinet (ega ham, ishchilari ham) to'xtaydi;
- plan — oylik limit: AI so'rovlar (matn, ovoz, tuzatish, shaxsiy maslahat), rasmlar va ishchilar soni.
  Limit har oy 1-sanada yangilanadi.
Bo'sh plan — cheklovsiz, bo'sh paid_until — muddatsiz (masalan, bosh admin tizimidan oldingi egalar).
Ega to'xtatilsa (is_active=False) — u ham, ishchilari ham botdan foydalana olmaydi.
"""
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from html import escape
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db import repo
from bot.db.models import Usage, User
from bot.services import llm
from bot.services.utils import fmt_number, today


@dataclass(frozen=True)
class Plan:
    key: str
    title: str
    price: int  # so'm / oy
    requests: int  # AI so'rov / oy
    images: int  # rasm / oy
    workers: int | None  # faol ishchilar, None — cheksiz


PLANS = {
    plan.key: plan
    for plan in (
        Plan("start", "Start", 99_000, 300, 30, 3),
        Plan("biznes", "Biznes", 199_000, 700, 70, 10),
        Plan("pro", "Pro", 349_000, 1400, 120, None),
    )
}
PERIOD_DAYS = 30
# ElevenLabs Scribe: dollar / soat ovoz
STT_USD_PER_HOUR = 0.22


class QuotaError(Exception):
    """Limit tugagan: foydalanuvchiga ko'rsatiladigan xabar."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def month_key(day: date | None = None) -> str:
    return (day or today()).strftime("%Y-%m")


def is_admin(owner: User) -> bool:
    return settings.is_admin(owner.tg_id)


def plan_of(owner: User) -> Plan | None:
    """None — cheklovsiz."""
    return None if is_admin(owner) else PLANS.get(owner.plan or "")


def expired(owner: User) -> bool:
    return owner.paid_until is not None and today() > owner.paid_until


def contact_line() -> str:
    return f"\nAloqa: {escape(settings.admin_contact)}" if settings.admin_contact else ""


def fmt_cost(micro: int) -> str:
    usd = micro / 1_000_000
    return f"${usd:.2f} ≈ {fmt_number(round(usd * settings.usd_rate, -2))} so'm"


async def account_owner(session: AsyncSession, user: User) -> User | None:
    return user if user.is_owner else await session.get(User, user.owner_id)


# --- Kirish ruxsati ---------------------------------------------------------

async def access_error(session: AsyncSession, user: User) -> str | None:
    """Kabinet to'xtatilgan yoki obuna tugagan bo'lsa — foydalanuvchiga xabar, aks holda None."""
    owner = await account_owner(session, user)
    if owner is None or is_admin(owner):
        return None
    if not owner.is_active:
        reason = "to'xtatilgan"
    elif expired(owner):
        reason = f"muddati tugagan ({owner.paid_until.strftime('%d.%m.%Y')})"
    else:
        return None
    if user.is_owner:
        return f"⛔ Obunangiz {reason}.\nDavom ettirish uchun bosh admin bilan bog'laning.{contact_line()}"
    return f"⛔ «{escape(owner.name)}» kabinetining obunasi {reason}. Egangizga murojaat qiling."


# --- Limitlar ---------------------------------------------------------------

def _limit_text(user: User, plan: Plan, problem: str) -> str:
    if not user.is_owner:
        return f"⛔ {problem}. Egangizga ayting."
    return (
        f"⛔ {problem} («{plan.title}» tarifi). Limit keyingi oyning 1-sanasida yangilanadi. "
        f"Tarifni oshirish uchun bosh admin bilan bog'laning.{contact_line()}"
    )


async def check(session: AsyncSession, user: User, *, requests: int = 0, images: int = 0) -> User | None:
    """Limit yetmasa — QuotaError. Kabinet egasini qaytaradi."""
    owner = await account_owner(session, user)
    plan = plan_of(owner) if owner else None
    if plan is None:
        return owner
    usage = await repo.get_usage(session, owner.id, month_key())
    if requests and usage.requests + requests > plan.requests:
        raise QuotaError(_limit_text(user, plan, f"Bu oylik AI so'rov limiti tugadi ({plan.requests} ta)"))
    if images and usage.images + images > plan.images:
        left = max(plan.images - usage.images, 0)
        problem = (
            f"Bu oy yana {left} ta rasm o'qish mumkin (oyiga {plan.images} ta) — kamroq rasm yuboring"
            if left
            else f"Bu oylik rasm limiti tugadi ({plan.images} ta)"
        )
        raise QuotaError(_limit_text(user, plan, problem))
    return owner


@asynccontextmanager
async def metered(session: AsyncSession, user: User, *, requests: int = 0, images: int = 0) -> AsyncIterator[None]:
    """Claude chaqiruvi atrofida: avval limitni tekshiradi, keyin sarf va xarajatni yozadi.

    So'rov va rasm faqat muvaffaqiyatli bo'lsa sanaladi, Claude xarajati esa har doim.
    """
    owner = await check(session, user, requests=requests, images=images)
    token = llm.spent_micro.set(0)
    ok = False
    try:
        yield
        ok = True
    finally:
        cost = llm.spent_micro.get()
        llm.spent_micro.reset(token)
        if owner is not None and (ok or cost):
            await repo.add_usage(
                session, owner.id, month_key(),
                requests=requests if ok else 0, images=images if ok else 0, cost_micro=cost,
            )


async def record_voice(session: AsyncSession, user: User, seconds: int) -> None:
    owner = await account_owner(session, user)
    if owner is not None and seconds > 0:
        cost = int(seconds * STT_USD_PER_HOUR / 3600 * 1_000_000)
        await repo.add_usage(session, owner.id, month_key(), voice_sec=seconds, cost_micro=cost)


async def worker_limit_error(session: AsyncSession, owner: User) -> str | None:
    """Yangi (yoki qayta yoqilgan) ishchi tarifga sig'masa — xabar."""
    plan = plan_of(owner)
    if plan is None or plan.workers is None:
        return None
    if await repo.count_workers(session, owner.id) >= plan.workers:
        return (
            f"«{plan.title}» tarifida ko'pi bilan {plan.workers} ta ishchi. "
            f"Tarifni oshirish uchun bosh admin bilan bog'laning.{contact_line()}"
        )
    return None


# --- Obuna muddati ----------------------------------------------------------

def extend(owner: User, days: int = PERIOD_DAYS) -> None:
    """Muddatni uzaytiradi: tugamagan bo'lsa — oxiridan, tugagan yoki muddatsiz bo'lsa — bugundan."""
    base = today()
    if owner.paid_until is not None and owner.paid_until > base:
        base = owner.paid_until
    owner.paid_until = base + timedelta(days=days)


def start_on_link(owner: User) -> None:
    """Ega taklif havolasini bosganda: tarifli bo'lsa, muddat shu kundan boshlanadi."""
    if owner.plan in PLANS and owner.paid_until is None:
        extend(owner)


def status_text(owner: User, usage: Usage, workers: int) -> str:
    """Tarif, muddat va shu oylik sarf — egaga ham, bosh adminga ham."""
    if is_admin(owner):
        return "👑 Bosh admin — cheklovsiz"
    plan = plan_of(owner)
    lines = [f"Tarif: <b>{plan.title}</b> — {fmt_number(plan.price)} so'm/oy" if plan else "Tarif: <b>cheklovsiz</b>"]
    if not owner.is_active:
        lines.append("Holat: ⏸ to'xtatilgan")
    elif owner.tg_id is None:
        lines.append("Holat: 🔗 havola hali bosilmagan")
    elif owner.paid_until is None:
        lines.append("Muddat: ♾ muddatsiz")
    else:
        left = (owner.paid_until - today()).days
        mark = "⛔ tugagan" if left < 0 else f"✅ {left} kun qoldi" if left > 3 else f"⚠️ {left} kun qoldi"
        lines.append(f"Muddat: {owner.paid_until.strftime('%d.%m.%Y')} gacha — {mark}")

    def limit(value: int, cap: int | None) -> str:
        return f"{value} / {cap}" if cap is not None else str(value)

    lines.append(
        f"Shu oy: AI so'rov {limit(usage.requests, plan.requests if plan else None)}, "
        f"rasm {limit(usage.images, plan.images if plan else None)}, "
        f"ovoz {usage.voice_sec // 60} daq"
    )
    lines.append(f"Ishchilar: {limit(workers, plan.workers if plan else None)}")
    return "\n".join(lines)
