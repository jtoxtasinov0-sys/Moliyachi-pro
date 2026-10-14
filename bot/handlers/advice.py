"""Moliyaviy maslahat: daromadni taqsimlash rejasi, kunlik limit, saboqlar va Claude'dan shaxsiy maslahat."""
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import advisor, billing
from bot import keyboards as kbs
from bot.db import repo
from bot.db.models import User
from bot.middlewares import IsOwner
from bot.services import llm

router = Router(name="advice")
router.message.filter(IsOwner())
router.callback_query.filter(IsOwner())


async def _view(session: AsyncSession, owner_id: int) -> tuple[str, InlineKeyboardMarkup]:
    text = await advisor.advice_text(session, owner_id)
    pct = await repo.savings_pct(session, owner_id)
    return text, kbs.advice_kb(pct, advisor.lesson_of_day())


@router.message(Command("maslahat"))
@router.message(F.text == kbs.BTN_ADVICE)
async def advice(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    text, kb = await _view(session, user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(kbs.AdviceCb.filter(F.action == "show"))
async def advice_show(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    text, kb = await _view(session, user.id)
    if call.message.text and call.message.text.startswith("💡 Moliyaviy maslahat"):
        await call.message.edit_text(text, reply_markup=kb)
    else:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()


@router.callback_query(kbs.AdviceCb.filter(F.action == "pct"))
async def advice_pct(call: CallbackQuery, callback_data: kbs.AdviceCb, session: AsyncSession, user: User) -> None:
    if callback_data.value in repo.SAVINGS_CHOICES:
        await repo.set_setting(session, user.id, "savings_pct", str(callback_data.value))
    text, kb = await _view(session, user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer(f"Jamg'arma: daromadning {callback_data.value}%")


@router.callback_query(kbs.AdviceCb.filter(F.action == "lesson"))
async def advice_lesson(call: CallbackQuery, callback_data: kbs.AdviceCb) -> None:
    index = callback_data.value % len(advisor.LESSONS)
    await call.message.answer(advisor.lesson_text(index), reply_markup=kbs.lesson_kb(index))
    await call.answer()


@router.callback_query(kbs.AdviceCb.filter(F.action == "ai"))
async def advice_ai(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    await call.answer()
    status = await call.message.answer("🤖 Daromad va xarajatlaringiz tahlil qilinmoqda…")
    context = await advisor.advice_context(session, user.id)
    try:
        async with billing.metered(session, user, requests=1):
            text = await llm.advise(context)
    except billing.QuotaError as e:
        await status.edit_text(e.message)
        return
    except llm.LlmError:
        await status.edit_text("⚠️ Hozir maslahat tayyorlab bo'lmadi. Birozdan keyin qayta urinib ko'ring.")
        return
    await status.edit_text(f"🤖 <b>Shaxsiy maslahat</b>\n\n{escape(text)}", reply_markup=kbs.lesson_kb(advisor.lesson_of_day()))
