"""Ovoz/matn -> LLM -> qoralama -> «To'g'rimi?» -> saqlash (mantiq — bot/intake.py da)."""
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import drafts, intake
from bot import keyboards as kbs
from bot.db.models import User
from bot.middlewares import Registered
from bot.services import stt

log = logging.getLogger(__name__)

router = Router(name="input")
router.message.filter(Registered())
router.callback_query.filter(Registered())

MAX_VOICE_SECONDS = 300


class DraftSG(StatesGroup):
    active = State()


# --- Kirish: ovoz va matn ---------------------------------------------------

@router.message(F.voice | F.audio)
async def on_voice(message: Message, bot: Bot, state: FSMContext, session: AsyncSession, user: User) -> None:
    media = message.voice or message.audio
    if media.duration and media.duration > MAX_VOICE_SECONDS:
        await message.answer("Ovozli xabar juda uzun (5 daqiqagacha bo'lsin).")
        return

    status = await message.answer("🎧 Eshitilmoqda…")
    try:
        buf = await bot.download(media)
        text = await stt.transcribe(buf.read())
    except stt.SttError:
        await status.edit_text("⚠️ Ovozni matnga aylantirib bo'lmadi. Qayta yuboring yoki matn bilan yozing.")
        return

    if not text:
        await status.edit_text("🤷 Ovozda gap eshitilmadi. Qayta yuboring.")
        return

    await process_text(status, text, state, session, user)


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    status = await message.answer("⏳ Tahlil qilinmoqda…")
    await process_text(status, message.text, state, session, user)


@router.message()
async def on_other(message: Message) -> None:
    await message.answer("Ovozli yoki matnli xabar yuboring.")


async def process_text(status: Message, text: str, state: FSMContext, session: AsyncSession, user: User) -> None:
    """status — «Eshitilmoqda…» xabari, natija shu xabarga yoziladi."""
    draft = None
    if await state.get_state() == DraftSG.active.state:
        draft = (await state.get_data()).get("draft")
    try:
        new_draft = await intake.analyze(session, user, text, draft)
    except intake.IntakeError as e:
        await status.edit_text(e.message)
        return

    await state.set_state(DraftSG.active)
    await state.update_data(draft=new_draft)
    text_out, kb = await drafts.present(session, new_draft, user)
    await status.edit_text(text_out, reply_markup=kb)


# --- Qoralama tugmalari -----------------------------------------------------

async def _load_draft(call: CallbackQuery, state: FSMContext) -> dict | None:
    draft = (await state.get_data()).get("draft")
    if draft is None or await state.get_state() != DraftSG.active.state:
        await call.answer("Bu qoralama eskirgan.", show_alert=True)
        await call.message.edit_reply_markup(reply_markup=None)
        return None
    return draft


async def _show(call: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, draft: dict) -> None:
    await state.update_data(draft=draft)
    text_out, kb = await drafts.present(session, draft, user)
    await call.message.edit_text(text_out, reply_markup=kb)
    await call.answer()


@router.callback_query(kbs.ResolveCb.filter())
async def on_resolve(
    call: CallbackQuery, callback_data: kbs.ResolveCb, state: FSMContext, session: AsyncSession, user: User
) -> None:
    draft = await _load_draft(call, state)
    if draft is None:
        return
    try:
        changed = await intake.choose(session, user, draft, callback_data)
    except intake.IntakeError as e:
        await call.answer(e.message, show_alert=True)
        return
    if not changed:
        await call.answer()
        return
    await _show(call, state, session, user, draft)


@router.callback_query(kbs.DraftCb.filter(F.action == "cancel"))
async def on_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text("❌ Bekor qilindi.")
    await call.answer()


@router.callback_query(kbs.DraftCb.filter(F.action == "fix"))
async def on_fix(call: CallbackQuery, state: FSMContext) -> None:
    draft = await _load_draft(call, state)
    if draft is None:
        return
    await call.answer()
    if draft["section"] == "money":
        examples = "• <i>summa 45 ming</i>\n• <i>dollarda</i>\n• <i>kategoriya Mashina</i>\n• <i>kecha edi</i>"
    else:
        examples = "• <i>soni 45 ta</i>\n• <i>model B-12</i>\n• <i>kecha edi</i>\n• <i>ishchi Vali</i>"
    await call.message.answer(f"✏️ Nimani o'zgartiramiz? Ovoz yoki matn bilan ayting, masalan:\n{examples}")


@router.callback_query(kbs.DebtDirCb.filter())
async def on_debt_direction(
    call: CallbackQuery, callback_data: kbs.DebtDirCb, state: FSMContext, session: AsyncSession, user: User
) -> None:
    draft = await _load_draft(call, state)
    if draft is None:
        return
    if not intake.set_direction(draft, callback_data):
        await call.answer()
        return
    await _show(call, state, session, user, draft)


@router.callback_query(kbs.DraftCb.filter(F.action.in_({"today", "yesterday"})))
async def on_date(
    call: CallbackQuery, callback_data: kbs.DraftCb, state: FSMContext, session: AsyncSession, user: User
) -> None:
    draft = await _load_draft(call, state)
    if draft is None:
        return
    if not intake.set_date(draft, callback_data.action):
        await call.answer("Sana o'zgarmadi.")
        return
    await _show(call, state, session, user, draft)


@router.callback_query(kbs.DraftCb.filter(F.action == "save"))
async def on_save(call: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    draft = await _load_draft(call, state)
    if draft is None:
        return
    try:
        saved = await intake.save(session, call.bot, user, draft)
    except intake.IntakeError as e:
        if e.drop:
            await state.clear()
            await call.message.edit_text(e.message)
            await call.answer()
        else:
            await call.answer(e.message, show_alert=True)
        return

    await state.clear()
    await call.message.edit_text(saved.header)
    for text, kb in saved.cards:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()
