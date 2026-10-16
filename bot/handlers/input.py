"""Ovoz/matn/rasm -> LLM -> qoralama -> «To'g'rimi?» -> saqlash (mantiq — bot/intake.py da)."""
import asyncio
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
from bot.monitor import heard
from bot.services import stt

log = logging.getLogger(__name__)

router = Router(name="input")
router.message.filter(Registered())
router.callback_query.filter(Registered())

MAX_VOICE_SECONDS = 300


class DraftSG(StatesGroup):
    active = State()


# --- Kirish: ovoz va matn -------------------------------------------------

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

    heard(message, text)
    await process_text(status, text, state, session, user)


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    status = await message.answer("⏳ Tahlil qilinmoqda…")
    await process_text(status, message.text, state, session, user)


# --- Rasm: bank ilovasi skrinshoti, chek yoki daftar sahifasi ----------------

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
# Claude rasmni base64 holida 5 MB gacha qabul qiladi
MAX_IMAGE_BYTES = 3_750_000
ALBUM_WAIT = 1.5

# Albom (bir nechta rasm birga yuborilgan): media_group_id -> xabarlar
_albums: dict[str, list[Message]] = {}


def _image_of(message: Message):
    """(fayl, media turi). Fayl ko'rinishida yuborilgan rasmda tur va hajm tekshiriladi."""
    if message.photo:
        return message.photo[-1], "image/jpeg"
    doc = message.document
    if doc.mime_type not in IMAGE_TYPES:
        raise intake.IntakeError("Bu rasm formatini o'qib bo'lmaydi. Rasmni fayl emas, oddiy rasm qilib yuboring.")
    if doc.file_size and doc.file_size > MAX_IMAGE_BYTES:
        raise intake.IntakeError("Fayl juda katta. Rasmni fayl emas, oddiy rasm qilib yuboring.")
    return doc, doc.mime_type


@router.message(F.photo | F.document.mime_type.startswith("image/"))
async def on_image(message: Message, bot: Bot, state: FSMContext, session: AsyncSession, user: User) -> None:
    if message.media_group_id:
        group = _albums.setdefault(message.media_group_id, [])
        group.append(message)
        if len(group) > 1:
            return  # albomni birinchi rasm qabul qilgan handler o'qiydi
        await asyncio.sleep(ALBUM_WAIT)
        messages = sorted(_albums.pop(message.media_group_id), key=lambda m: m.message_id)
    else:
        messages = [message]

    status = await message.answer("🖼 Rasm o'qilmoqda…" if len(messages) == 1 else "🖼 Rasmlar o'qilmoqda…")
    try:
        images = []
        for m in messages[: intake.MAX_IMAGES]:
            file, media_type = _image_of(m)
            images.append(((await bot.download(file)).read(), media_type))
        caption = "\n".join(m.caption for m in messages if m.caption)
        draft = await intake.analyze_images(session, user, images, caption)
        if len(messages) > intake.MAX_IMAGES:
            await message.answer(f"ℹ️ Bir martada {intake.MAX_IMAGES} tagacha rasm o'qiladi — qolganini alohida yuboring.")
        if await intake.autosave(session, user, draft):
            saved = await intake.save(session, bot, user, draft)
            await send_saved(status, saved, compact=True)
            return
    except intake.IntakeError as e:
        await status.edit_text(e.message)
        return

    await show_draft(status, draft, state, session, user)


@router.message()
async def on_other(message: Message) -> None:
    await message.answer("Ovozli, matnli xabar yoki rasm (bank skrinshoti, chek) yuboring.")


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
    await show_draft(status, new_draft, state, session, user)


async def show_draft(status: Message, draft: dict, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.set_state(DraftSG.active)
    await state.update_data(draft=draft)
    text_out, kb = await drafts.present(session, draft, user)
    await status.edit_text(text_out, reply_markup=kb)


MAX_CARDS = 5
MESSAGE_LIMIT = 3800


async def send_saved(status: Message, saved: intake.Saved, compact: bool = False) -> None:
    """Saqlangan yozuvlar — har biri o'z tugmasi bilan. compact (rasmdan, bank tarixi) va yozuv ko'p bo'lsa —
    bitta ro'yxat qilib, xabarlar ko'payib ketmasin."""
    if not compact or len(saved.cards) <= MAX_CARDS:
        await status.edit_text(saved.header)
        for text, kb in saved.cards:
            await status.answer(text, reply_markup=kb)
        return
    chunks = [f"{saved.header}\n\n<b>{len(saved.cards)} ta yozuv:</b>"]
    for text, _ in saved.cards:
        if len(chunks[-1]) + len(text) + 2 > MESSAGE_LIMIT:
            chunks.append(text)
        else:
            chunks[-1] += "\n\n" + text
    chunks[-1] += "\n\n<i>Xatosi bo'lsa — ilovada o'chiring.</i>"
    kb = kbs.app_kb("wallet")
    for i, chunk in enumerate(chunks):
        markup = kb if i == len(chunks) - 1 else None
        if i == 0:
            await status.edit_text(chunk, reply_markup=markup)
        else:
            await status.answer(chunk, reply_markup=markup)


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
    if draft["section"] == "client":
        examples = "• <i>12 komplekt</i>\n• <i>150 mingdan</i>\n• <i>mijoz Tohir emas, Toxir aka</i>\n• <i>kecha edi</i>"
    elif draft["section"] == "money":
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


@router.callback_query(kbs.ClientKindCb.filter())
async def on_client_kind(
    call: CallbackQuery, callback_data: kbs.ClientKindCb, state: FSMContext, session: AsyncSession, user: User
) -> None:
    draft = await _load_draft(call, state)
    if draft is None:
        return
    if not intake.set_client_kind(draft, callback_data):
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
    await send_saved(call.message, saved, compact=draft.get("source") == "image")
    await call.answer()
