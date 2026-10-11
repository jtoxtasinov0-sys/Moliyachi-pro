"""Egasi uchun: ishchilar, modellar va sozlamalar."""
import re
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kbs
from bot.db import repo
from bot.db.models import ItemModel, User
from bot.middlewares import IsOwner
from bot.services.utils import CURRENCY_LABELS, fmt_money

router = Router(name="admin")
router.message.filter(IsOwner())
router.callback_query.filter(IsOwner())


class AdminSG(StatesGroup):
    worker_name = State()
    model_name = State()
    model_price = State()


# --- Ishchilar --------------------------------------------------------------

async def _invite_link(bot: Bot, token: str) -> str:
    me = await bot.me()
    return f"https://t.me/{me.username}?start=inv_{token}"


async def _workers_view(session: AsyncSession) -> tuple[str, InlineKeyboardMarkup]:
    workers = await repo.list_workers(session, active_only=False)
    kb = InlineKeyboardBuilder()
    lines = []
    for w in workers:
        if not w.is_active:
            status = "⛔ o'chirilgan"
        elif w.tg_id:
            status = "✅ ulangan"
        else:
            status = "🔗 havola kutilmoqda"
        lines.append(f"• <b>{escape(w.name)}</b> — {status}")
        if w.is_active and not w.tg_id:
            kb.button(text=f"🔗 {w.name}", callback_data=kbs.WorkerCb(action="link", user_id=w.id))
        kb.button(
            text=f"{'⛔ O‘chirish' if w.is_active else '✅ Qaytarish'}: {w.name}",
            callback_data=kbs.WorkerCb(action="toggle", user_id=w.id),
        )
    kb.button(text="➕ Ishchi qo'shish", callback_data=kbs.WorkerCb(action="add"))
    kb.adjust(1)
    text = "👷 <b>Ishchilar</b>\n\n" + ("\n".join(lines) if lines else "Hali ishchi yo'q.")
    return text, kb.as_markup()


@router.message(F.text == kbs.BTN_WORKERS)
async def workers(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    text, kb = await _workers_view(session)
    await message.answer(text, reply_markup=kb)


@router.callback_query(kbs.WorkerCb.filter(F.action == "add"))
async def worker_add(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminSG.worker_name)
    await call.message.answer("Yangi ishchining ismini yozing (masalan: <i>Alisher</i>):")
    await call.answer()


@router.message(AdminSG.worker_name, F.text, ~F.text.in_(kbs.MENU_TEXTS))
async def worker_add_name(message: Message, bot: Bot, state: FSMContext, session: AsyncSession) -> None:
    name = message.text.strip()[:100]
    if not name or name.startswith("/"):
        await message.answer("Ism bo'sh bo'lmasin. Qaytadan yozing:")
        return
    worker = await repo.create_worker(session, name)
    await state.clear()
    link = await _invite_link(bot, worker.invite_token)
    await message.answer(
        f"✅ <b>{escape(name)}</b> qo'shildi.\n\n"
        f"Ushbu havolani ishchiga yuboring — u bosganda bot unga ulanadi:\n{link}\n\n"
        "Havola bir martalik."
    )


@router.callback_query(kbs.WorkerCb.filter(F.action == "link"))
async def worker_link(call: CallbackQuery, callback_data: kbs.WorkerCb, bot: Bot, session: AsyncSession) -> None:
    worker = await session.get(User, callback_data.user_id)
    if worker is None or worker.is_owner:
        await call.answer()
        return
    token = await repo.new_invite_token(session, worker)
    link = await _invite_link(bot, token)
    await call.message.answer(f"🔗 <b>{escape(worker.name)}</b> uchun yangi havola:\n{link}")
    await call.answer()


@router.callback_query(kbs.WorkerCb.filter(F.action == "toggle"))
async def worker_toggle(call: CallbackQuery, callback_data: kbs.WorkerCb, session: AsyncSession) -> None:
    worker = await session.get(User, callback_data.user_id)
    if worker is None or worker.is_owner:
        await call.answer()
        return
    worker.is_active = not worker.is_active
    await session.flush()
    text, kb = await _workers_view(session)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer("Faollashtirildi" if worker.is_active else "O'chirildi — endi botdan foydalana olmaydi")


# --- Modellar ---------------------------------------------------------------

async def _models_view(session: AsyncSession) -> tuple[str, InlineKeyboardMarkup]:
    models = await repo.list_models(session, active_only=False)
    kb = InlineKeyboardBuilder()
    lines = []
    for m in models:
        price = fmt_money(m.price, m.currency) if m.price is not None else "narx yo'q"
        mark = "" if m.is_active else " ⛔"
        lines.append(f"• <b>{escape(m.name)}</b> — {price}{mark}")
        kb.button(text=f"💰 {m.name}", callback_data=kbs.ModelCb(action="price", model_id=m.id))
        kb.button(
            text="⛔ O‘chirish" if m.is_active else "✅ Qaytarish",
            callback_data=kbs.ModelCb(action="toggle", model_id=m.id),
        )
    kb.button(text="➕ Model qo'shish", callback_data=kbs.ModelCb(action="add"))
    kb.adjust(*([2] * len(models)), 1)
    text = "🧵 <b>Modellar</b> (narx — bir dona uchun ish haqi)\n\n" + (
        "\n".join(lines) if lines else "Hali model yo'q."
    )
    return text, kb.as_markup()


@router.message(F.text == kbs.BTN_MODELS)
async def models(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    text, kb = await _models_view(session)
    await message.answer(text, reply_markup=kb)


@router.callback_query(kbs.ModelCb.filter(F.action == "add"))
async def model_add(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminSG.model_name)
    await call.message.answer("Model nomini yozing (masalan: <i>A-12</i>):")
    await call.answer()


@router.message(AdminSG.model_name, F.text, ~F.text.in_(kbs.MENU_TEXTS))
async def model_add_name(message: Message, state: FSMContext, session: AsyncSession) -> None:
    name = message.text.strip()[:100]
    if not name or name.startswith("/"):
        await message.answer("Nom bo'sh bo'lmasin. Qaytadan yozing:")
        return
    if await repo.get_model_by_name(session, name):
        await message.answer("Bunday model allaqachon bor. Boshqa nom yozing:")
        return
    model = await repo.create_model(session, name)
    await _ask_price(message, state, session, model)


async def _ask_price(message: Message, state: FSMContext, session: AsyncSession, model: ItemModel) -> None:
    await state.set_state(AdminSG.model_price)
    await state.update_data(model_id=model.id)
    currencies = await repo.enabled_currencies(session)
    examples = ", ".join(f"<i>5000 {CURRENCY_LABELS[c]}</i>" for c in currencies)
    kb = InlineKeyboardBuilder()
    kb.button(text="Narxsiz qoldirish", callback_data=kbs.ModelCb(action="noprice", model_id=model.id))
    await message.answer(
        f"🧵 <b>{escape(model.name)}</b>\n\nBir dona uchun ish haqini yozing.\n"
        f"Masalan: {examples}. Valyuta yozilmasa — {CURRENCY_LABELS[currencies[0]]}.",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(kbs.ModelCb.filter(F.action == "price"))
async def model_price(call: CallbackQuery, callback_data: kbs.ModelCb, state: FSMContext, session: AsyncSession) -> None:
    model = await session.get(ItemModel, callback_data.model_id)
    if model is None:
        await call.answer()
        return
    await _ask_price(call.message, state, session, model)
    await call.answer()


@router.callback_query(kbs.ModelCb.filter(F.action == "noprice"))
async def model_noprice(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    text, kb = await _models_view(session)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


_CURRENCY_WORDS = {
    "UZS": ("so'm", "som", "sum", "сўм", "сум", "uzs"),
    "USD": ("$", "usd", "dollar", "доллар"),
    "KRW": ("won", "von", "krw", "₩", "вон"),
}
_MULTIPLIERS = {"ming": 1_000, "минг": 1_000, "k": 1_000, "mln": 1_000_000, "million": 1_000_000, "млн": 1_000_000}


def parse_price(text: str, default_currency: str) -> tuple[Decimal, str] | None:
    """"5000", "5 000 so'm", "2.5$", "3 ming", "1,5 mln" -> (summa, valyuta)."""
    t = text.lower().replace("‘", "'").replace("’", "'").replace("ʻ", "'")
    currency = default_currency
    for code, words in _CURRENCY_WORDS.items():
        if any(w in t for w in words):
            currency = code
            for w in words:
                t = t.replace(w, " ")
    multiplier = 1
    for word, mult in _MULTIPLIERS.items():
        if re.search(rf"\d\s*{re.escape(word)}\b", t):
            multiplier = mult
            t = re.sub(rf"\s*{re.escape(word)}\b", " ", t)
            break
    m = re.search(r"\d[\d\s]*(?:[.,]\d+)?", t)
    if not m:
        return None
    number = m.group(0).replace(" ", "").replace(",", ".")
    try:
        value = Decimal(number) * multiplier
    except InvalidOperation:
        return None
    if value < 0:
        return None
    return value, currency


@router.message(AdminSG.model_price, F.text, ~F.text.in_(kbs.MENU_TEXTS))
async def model_price_value(message: Message, state: FSMContext, session: AsyncSession) -> None:
    model_id = (await state.get_data()).get("model_id")
    model = await session.get(ItemModel, model_id) if model_id else None
    if model is None:
        await state.clear()
        return
    currencies = await repo.enabled_currencies(session)
    parsed = parse_price(message.text, currencies[0])
    if parsed is None:
        await message.answer("Narxni tushunmadim. Masalan: <i>5000</i> yoki <i>2 $</i>")
        return
    model.price, model.currency = parsed
    await session.flush()
    await state.clear()
    await message.answer(
        f"✅ <b>{escape(model.name)}</b>: {fmt_money(model.price, model.currency)}\n"
        "Yangi narx shu paytdan keyingi yozuvlarga qo'llanadi."
    )
    text, kb = await _models_view(session)
    await message.answer(text, reply_markup=kb)


@router.callback_query(kbs.ModelCb.filter(F.action == "toggle"))
async def model_toggle(call: CallbackQuery, callback_data: kbs.ModelCb, session: AsyncSession) -> None:
    model = await session.get(ItemModel, callback_data.model_id)
    if model is None:
        await call.answer()
        return
    model.is_active = not model.is_active
    await session.flush()
    text, kb = await _models_view(session)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


# --- Sozlamalar -------------------------------------------------------------

async def _settings_view(session: AsyncSession) -> tuple[str, InlineKeyboardMarkup]:
    approval = await repo.require_approval(session)
    enabled = await repo.enabled_currencies(session)
    kb = InlineKeyboardBuilder()
    kb.button(
        text=f"{'✅' if approval else '⬜'} Ishchi yozuvlarini tasdiqlash",
        callback_data=kbs.SettingsCb(action="approval"),
    )
    for code in repo.ALL_CURRENCIES:
        kb.button(
            text=f"{'✅' if code in enabled else '⬜'} {code} ({CURRENCY_LABELS[code]})",
            callback_data=kbs.SettingsCb(action="cur", value=code),
        )
    kb.adjust(1, 3)
    text = (
        "⚙️ <b>Sozlamalar</b>\n\n"
        f"• Ishchi yozuvlari: {'egasi tasdiqlagandan keyin' if approval else 'darhol'} hisobotga tushadi\n"
        f"• Valyutalar: {', '.join(enabled)}"
    )
    return text, kb.as_markup()


@router.message(F.text == kbs.BTN_SETTINGS)
async def settings_menu(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    text, kb = await _settings_view(session)
    await message.answer(text, reply_markup=kb)


@router.callback_query(kbs.SettingsCb.filter())
async def settings_toggle(call: CallbackQuery, callback_data: kbs.SettingsCb, session: AsyncSession) -> None:
    if callback_data.action == "approval":
        current = await repo.require_approval(session)
        await repo.set_setting(session, "require_approval", "0" if current else "1")
    elif callback_data.action == "cur":
        enabled = await repo.enabled_currencies(session)
        code = callback_data.value
        if code in enabled:
            if len(enabled) == 1:
                await call.answer("Kamida bitta valyuta yoqilgan bo'lishi kerak.", show_alert=True)
                return
            enabled.remove(code)
        else:
            enabled.append(code)
        ordered = [c for c in repo.ALL_CURRENCIES if c in enabled]
        await repo.set_setting(session, "currencies", ",".join(ordered))
    text, kb = await _settings_view(session)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()
