"""Bosh admin: egalar (botdan foydalanuvchi mijozlar) — qo'shish, obuna muddati, tarif, to'xtatish.

Egaga esa — /obuna: o'z tarifi, muddati va shu oylik sarfi.
"""
import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import billing
from bot import keyboards as kbs
from bot.db import repo
from bot.db.models import Role, User
from bot.handlers.admin import _invite_link
from bot.middlewares import IsAdmin, IsOwner
from bot.services.utils import fmt_number, today

log = logging.getLogger(__name__)

router = Router(name="owners")


class OwnersSG(StatesGroup):
    name = State()


async def tell(bot: Bot, tg_id: int | None, text: str) -> None:
    """Xabar yuborib bo'lmasa (botni bloklagan) — jim o'tib ketadi."""
    if not tg_id:
        return
    try:
        await bot.send_message(tg_id, text)
    except TelegramAPIError:
        log.info("Xabar yuborilmadi: %s", tg_id)


def _icon(owner: User) -> str:
    if not owner.is_active:
        return "⏸"
    if owner.tg_id is None:
        return "🔗"
    if owner.paid_until is None:
        return "♾"
    left = (owner.paid_until - today()).days
    return "⛔" if left < 0 else "⚠️" if left <= 3 else "✅"


# --- Ro'yxat ----------------------------------------------------------------

async def _list_view(session: AsyncSession) -> tuple[str, InlineKeyboardMarkup]:
    month = billing.month_key()
    kb = InlineKeyboardBuilder()
    lines = []
    income = 0
    total_cost = 0
    for owner in await repo.list_owners(session):
        usage = await repo.get_usage(session, owner.id, month)
        total_cost += usage.cost_micro
        if billing.is_admin(owner):
            continue
        plan = billing.plan_of(owner)
        until = owner.paid_until.strftime("%d.%m") if owner.paid_until else "muddatsiz"
        lines.append(
            f"{_icon(owner)} <b>{escape(owner.name)}</b> — {plan.title if plan else 'cheklovsiz'}, {until}, "
            f"so'rov {usage.requests}, xarajat {billing.fmt_cost(usage.cost_micro)}"
        )
        kb.button(text=f"{_icon(owner)} {owner.name}", callback_data=kbs.OwnerCb(action="view", owner_id=owner.id))
        if plan and owner.is_active and owner.tg_id and not billing.expired(owner):
            income += plan.price
    kb.button(text="➕ Yangi ega qo'shish", callback_data=kbs.OwnerCb(action="new"))
    kb.adjust(1)
    text = (
        "👑 <b>Egalar</b> — botdan foydalanadigan mijozlaringiz\n\n"
        + ("\n".join(lines) if lines else "Hali ega qo'shilmagan.")
        + f"\n\n💰 Faol obunalar: <b>{fmt_number(income)} so'm/oy</b>"
        + f"\n🧾 Shu oy API xarajati (hammasi, siz ham): {billing.fmt_cost(total_cost)}"
        + "\n\n✅ faol · ⚠️ 3 kundan kam qoldi · ⛔ tugagan · ⏸ to'xtatilgan · 🔗 havola bosilmagan · ♾ muddatsiz"
    )
    return text, kb.as_markup()


@router.message(Command("egalar"), IsAdmin())
@router.message(F.text == kbs.BTN_OWNERS, IsAdmin())
async def owners_list(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    text, kb = await _list_view(session)
    await message.answer(text, reply_markup=kb)


@router.callback_query(kbs.OwnerCb.filter(F.action == "list"), IsAdmin())
async def owners_back(call: CallbackQuery, session: AsyncSession) -> None:
    text, kb = await _list_view(session)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


# --- Bitta ega --------------------------------------------------------------

async def _get_owner(session: AsyncSession, owner_id: int) -> User | None:
    owner = await session.get(User, owner_id)
    if owner is None or owner.role != Role.OWNER or billing.is_admin(owner):
        return None
    return owner


async def _card(session: AsyncSession, bot: Bot, owner: User) -> tuple[str, InlineKeyboardMarkup]:
    usage = await repo.get_usage(session, owner.id, billing.month_key())
    workers = await repo.count_workers(session, owner.id)
    text = (
        f"👤 <b>{escape(owner.name)}</b>\n\n"
        f"{billing.status_text(owner, usage, workers)}\n"
        f"API xarajati shu oy: {billing.fmt_cost(usage.cost_micro)}"
    )
    if owner.tg_id is None and owner.invite_token and owner.is_active:
        text += f"\n\n🔗 Taklif havolasi (bir martalik):\n{await _invite_link(bot, owner.invite_token)}"

    def cb(action: str, value: str = "") -> kbs.OwnerCb:
        return kbs.OwnerCb(action=action, owner_id=owner.id, value=value)

    kb = InlineKeyboardBuilder()
    kb.button(text="➕ 30 kun", callback_data=cb("extend", "30"))
    kb.button(text="➕ 90 kun", callback_data=cb("extend", "90"))
    kb.button(text="📦 Tarifni o'zgartirish", callback_data=cb("plans"))
    kb.button(text="⏸ To'xtatish" if owner.is_active else "▶️ Yoqish", callback_data=cb("toggle"))
    sizes = [2, 1, 1]
    if owner.paid_until is not None:
        kb.button(text="♾ Muddatsiz qilish", callback_data=cb("unlimited"))
        sizes.append(1)
    if owner.tg_id is None:
        kb.button(text="🔗 Yangi havola", callback_data=cb("link"))
        sizes.append(1)
    kb.button(text="⬅️ Ro'yxat", callback_data=kbs.OwnerCb(action="list"))
    kb.adjust(*sizes, 1)
    return text, kb.as_markup()


async def _show(call: CallbackQuery, session: AsyncSession, bot: Bot, owner: User, note: str = "") -> None:
    text, kb = await _card(session, bot, owner)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer(note)


@router.callback_query(kbs.OwnerCb.filter(F.action == "view"), IsAdmin())
async def owner_view(call: CallbackQuery, callback_data: kbs.OwnerCb, session: AsyncSession, bot: Bot) -> None:
    owner = await _get_owner(session, callback_data.owner_id)
    if owner is None:
        await call.answer("Topilmadi")
        return
    await _show(call, session, bot, owner)


@router.callback_query(kbs.OwnerCb.filter(F.action == "extend"), IsAdmin())
async def owner_extend(call: CallbackQuery, callback_data: kbs.OwnerCb, session: AsyncSession, bot: Bot) -> None:
    owner = await _get_owner(session, callback_data.owner_id)
    if owner is None:
        await call.answer("Topilmadi")
        return
    billing.extend(owner, int(callback_data.value or billing.PERIOD_DAYS))
    owner.is_active = True
    await session.flush()
    until = owner.paid_until.strftime("%d.%m.%Y")
    await tell(bot, owner.tg_id, f"✅ To'lov qabul qilindi. Obunangiz <b>{until}</b> gacha uzaytirildi. Rahmat!")
    await _show(call, session, bot, owner, f"Muddat: {until} gacha")


@router.callback_query(kbs.OwnerCb.filter(F.action == "unlimited"), IsAdmin())
async def owner_unlimited(call: CallbackQuery, callback_data: kbs.OwnerCb, session: AsyncSession, bot: Bot) -> None:
    owner = await _get_owner(session, callback_data.owner_id)
    if owner is None:
        await call.answer("Topilmadi")
        return
    owner.paid_until = None
    await session.flush()
    await _show(call, session, bot, owner, "Muddatsiz")


@router.callback_query(kbs.OwnerCb.filter(F.action == "toggle"), IsAdmin())
async def owner_toggle(call: CallbackQuery, callback_data: kbs.OwnerCb, session: AsyncSession, bot: Bot) -> None:
    owner = await _get_owner(session, callback_data.owner_id)
    if owner is None:
        await call.answer("Topilmadi")
        return
    owner.is_active = not owner.is_active
    await session.flush()
    if owner.is_active:
        await tell(bot, owner.tg_id, "▶️ Kabinetingiz qayta yoqildi.")
    else:
        await tell(bot, owner.tg_id, f"⏸ Kabinetingiz to'xtatildi.{billing.contact_line()}")
    await _show(call, session, bot, owner, "Yoqildi" if owner.is_active else "To'xtatildi — ishchilari ham kira olmaydi")


@router.callback_query(kbs.OwnerCb.filter(F.action == "link"), IsAdmin())
async def owner_link(call: CallbackQuery, callback_data: kbs.OwnerCb, session: AsyncSession, bot: Bot) -> None:
    owner = await _get_owner(session, callback_data.owner_id)
    if owner is None or owner.tg_id is not None:
        await call.answer()
        return
    await repo.new_invite_token(session, owner)
    await _show(call, session, bot, owner, "Yangi havola tayyor, eskisi ishlamaydi")


def _plans_kb(action: str, owner_id: int = 0) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for plan in billing.PLANS.values():
        workers = f"{plan.workers} ishchi" if plan.workers else "cheksiz ishchi"
        kb.button(
            text=f"{plan.title} — {fmt_number(plan.price)} so'm: {plan.requests} so'rov, {plan.images} rasm, {workers}",
            callback_data=kbs.OwnerCb(action=action, owner_id=owner_id, value=plan.key),
        )
    kb.button(text="🎁 Cheklovsiz (bepul)", callback_data=kbs.OwnerCb(action=action, owner_id=owner_id, value="-"))
    if owner_id:
        kb.button(text="⬅️ Orqaga", callback_data=kbs.OwnerCb(action="view", owner_id=owner_id))
    kb.adjust(1)
    return kb.as_markup()


@router.callback_query(kbs.OwnerCb.filter(F.action == "plans"), IsAdmin())
async def owner_plans(call: CallbackQuery, callback_data: kbs.OwnerCb, session: AsyncSession) -> None:
    owner = await _get_owner(session, callback_data.owner_id)
    if owner is None:
        await call.answer("Topilmadi")
        return
    await call.message.edit_text(
        f"📦 <b>{escape(owner.name)}</b> uchun tarifni tanlang:", reply_markup=_plans_kb("plan", owner.id)
    )
    await call.answer()


@router.callback_query(kbs.OwnerCb.filter(F.action == "plan"), IsAdmin())
async def owner_plan(call: CallbackQuery, callback_data: kbs.OwnerCb, session: AsyncSession, bot: Bot) -> None:
    owner = await _get_owner(session, callback_data.owner_id)
    if owner is None:
        await call.answer("Topilmadi")
        return
    owner.plan = callback_data.value if callback_data.value in billing.PLANS else None
    await session.flush()
    plan = billing.plan_of(owner)
    await tell(bot, owner.tg_id, f"📦 Tarifingiz: <b>{plan.title if plan else 'cheklovsiz'}</b>. Batafsil: /obuna")
    await _show(call, session, bot, owner, "Tarif o'zgardi")


# --- Yangi ega --------------------------------------------------------------

@router.callback_query(kbs.OwnerCb.filter(F.action == "new"), IsAdmin())
async def owner_new(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OwnersSG.name)
    await call.message.answer("Yangi eganing ismi yoki korxona nomini yozing (masalan: <i>Dilshod — tikuv sexi</i>):")
    await call.answer()


@router.message(OwnersSG.name, F.text, ~F.text.in_(kbs.MENU_TEXTS), IsAdmin())
async def owner_new_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()[:100]
    if not name or name.startswith("/"):
        await message.answer("Ism bo'sh bo'lmasin. Qaytadan yozing:")
        return
    await state.update_data(owner_name=name)
    await message.answer(f"<b>{escape(name)}</b> uchun tarifni tanlang:", reply_markup=_plans_kb("newplan"))


@router.callback_query(kbs.OwnerCb.filter(F.action == "newplan"), IsAdmin())
async def owner_new_plan(
    call: CallbackQuery, callback_data: kbs.OwnerCb, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    name = (await state.get_data()).get("owner_name")
    if not name:
        await call.answer("Qaytadan boshlang: ➕ Yangi ega qo'shish", show_alert=True)
        return
    await state.clear()
    plan = callback_data.value if callback_data.value in billing.PLANS else None
    owner = await repo.create_owner(session, name, plan)
    link = await _invite_link(bot, owner.invite_token)
    days = f"{billing.PERIOD_DAYS} kunlik muddat havola bosilgan kundan boshlanadi." if plan else "Muddatsiz."
    await call.message.edit_text(
        f"✅ <b>{escape(name)}</b> qo'shildi.\n\n"
        f"Ushbu havolani unga yuboring — bosganda bot uning alohida kabinetini ochadi:\n{link}\n\n"
        f"Havola bir martalik. {days}\n\n"
        "U o'z ishchilarini «👷 Ishchilar» bo'limidan o'zi qo'shadi — Render'ga kirish shart emas."
    )
    await call.answer()


# --- Ega uchun: o'z obunasi ----------------------------------------------------

@router.message(Command("obuna"), IsOwner())
async def my_subscription(message: Message, session: AsyncSession, user: User) -> None:
    usage = await repo.get_usage(session, user.id, billing.month_key())
    workers = await repo.count_workers(session, user.id)
    text = f"💳 <b>Obuna</b>\n\n{billing.status_text(user, usage, workers)}"
    if not billing.is_admin(user):
        text += (
            "\n\nAI so'rov — har bir matn yoki ovozli xabar, tuzatish va shaxsiy maslahat (ishchilarniki ham). "
            "Limit har oy 1-sanada yangilanadi." + billing.contact_line()
        )
    await message.answer(text)
