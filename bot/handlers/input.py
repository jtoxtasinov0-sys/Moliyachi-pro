"""Ovoz/matn -> LLM -> qoralama -> «To'g'rimi?» -> saqlash."""
import logging
from datetime import date, timedelta
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import drafts
from bot import keyboards as kbs
from bot.db import repo
from bot.db.models import EntryStatus, ItemModel, User
from bot.handlers.entries import entry_text, notify_owner_pending
from bot.middlewares import Registered
from bot.services import llm, stt
from bot.services.utils import today

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
    data = await state.get_data()
    draft = data.get("draft")
    correcting = (
        await state.get_state() == DraftSG.active.state and draft is not None and not drafts.is_stale(draft)
    )

    known = await drafts.known_names(session, user)
    worker_name = None if user.is_owner else user.name
    try:
        if correcting:
            ext = await llm.correct(drafts.for_llm(draft), text, today(), known, worker_name)
        else:
            ext = await llm.extract(text, today(), known, worker_name)
    except llm.LlmError:
        await status.edit_text("⚠️ Xabarni tahlil qilib bo'lmadi. Birozdan keyin qayta urinib ko'ring.")
        return

    if ext.section in ("client", "personal") and user.is_owner:
        await status.edit_text(
            f"🗣 <i>«{escape(text)}»</i>\n\nBu {'mijozlar' if ext.section == 'client' else 'shaxsiy'} "
            "bo'limiga tegishli — u keyingi bosqichda qo'shiladi."
        )
        return

    if ext.section != "work" or not ext.work:
        hint = ext.question or "Tushunmadim. Masalan: «A-12 modeldan 50 ta topshirdim»."
        await status.edit_text(f"🗣 <i>«{escape(text)}»</i>\n\n🤔 {escape(hint)}")
        return

    raw = f"{draft['raw_text']} → {text}" if correcting and draft.get("raw_text") else text
    entry_id = draft["entry_id"] if correcting else None
    new_draft = drafts.from_extraction(ext, raw, entry_id=entry_id)
    if entry_id and len(new_draft["lines"]) != 1:
        await status.edit_text("Tahrirlashda faqat bitta yozuv bo'lishi kerak. Qaytadan ayting.")
        return

    await drafts.resolve(session, new_draft, user)
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


@router.callback_query(kbs.ResolveCb.filter())
async def on_resolve(
    call: CallbackQuery, callback_data: kbs.ResolveCb, state: FSMContext, session: AsyncSession, user: User
) -> None:
    draft = await _load_draft(call, state)
    if draft is None:
        return
    if callback_data.line >= len(draft["lines"]):
        await call.answer()
        return
    line = draft["lines"][callback_data.line]

    if callback_data.kind == "worker" and user.is_owner:
        workers = {w.id: w for w in await repo.list_workers(session)}
        worker = workers.get(callback_data.item_id)
        if worker is None:
            await call.answer("Ishchi topilmadi.", show_alert=True)
            return
        if line["worker_q"] and line["worker_q"].lower() != worker.name.lower():
            await repo.save_alias(session, "worker", worker.id, line["worker_q"])
        line["worker_id"], line["worker_name"] = worker.id, worker.name

    elif callback_data.kind == "model":
        if callback_data.item_id == 0:
            if not user.is_owner or not line["model_q"]:
                await call.answer()
                return
            model = await repo.get_model_by_name(session, line["model_q"]) or await repo.create_model(
                session, line["model_q"].strip()
            )
        else:
            models = {m.id: m for m in await repo.list_models(session)}
            model = models.get(callback_data.item_id)
            if model is None:
                await call.answer("Model topilmadi.", show_alert=True)
                return
            if line["model_q"] and line["model_q"].lower() != model.name.lower():
                await repo.save_alias(session, "model", model.id, line["model_q"])
        drafts.set_model(line, model)

    await state.update_data(draft=draft)
    text_out, kb = await drafts.present(session, draft, user)
    await call.message.edit_text(text_out, reply_markup=kb)
    await call.answer()


@router.callback_query(kbs.DraftCb.filter(F.action == "cancel"))
async def on_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text("❌ Bekor qilindi.")
    await call.answer()


@router.callback_query(kbs.DraftCb.filter(F.action == "fix"))
async def on_fix(call: CallbackQuery, state: FSMContext) -> None:
    if await _load_draft(call, state) is None:
        return
    await call.answer()
    await call.message.answer(
        "✏️ Nimani o'zgartiramiz? Ovoz yoki matn bilan ayting, masalan:\n"
        "• <i>soni 45 ta</i>\n• <i>model B-12</i>\n• <i>kecha edi</i>\n• <i>ishchi Vali</i>"
    )


@router.callback_query(kbs.DraftCb.filter(F.action.in_({"today", "yesterday"})))
async def on_date(
    call: CallbackQuery, callback_data: kbs.DraftCb, state: FSMContext, session: AsyncSession, user: User
) -> None:
    draft = await _load_draft(call, state)
    if draft is None:
        return
    d = today() if callback_data.action == "today" else today() - timedelta(days=1)
    if draft["date"] == d.isoformat():
        await call.answer("Sana o'zgarmadi.")
        return
    draft["date"] = d.isoformat()
    await state.update_data(draft=draft)
    text_out, kb = await drafts.present(session, draft, user)
    await call.message.edit_text(text_out, reply_markup=kb)
    await call.answer()


@router.callback_query(kbs.DraftCb.filter(F.action == "save"))
async def on_save(call: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    draft = await _load_draft(call, state)
    if draft is None:
        return
    if drafts.next_missing(draft) is not None:
        await call.answer("Avval yetishmagan ma'lumotni to'ldiring.", show_alert=True)
        return

    needs_approval = not user.is_owner and await repo.require_approval(session)
    work_date = date.fromisoformat(draft["date"])

    if draft["entry_id"]:
        entry = await repo.get_work_entry(session, draft["entry_id"])
        if entry is None or (not user.is_owner and entry.worker_id != user.id):
            await state.clear()
            await call.message.edit_text("⚠️ Yozuv topilmadi (o'chirilgan bo'lishi mumkin).")
            await call.answer()
            return
        line = draft["lines"][0]
        entry.worker_id = line["worker_id"]
        if entry.model_id != line["model_id"]:
            repo.apply_model_price(entry, await session.get(ItemModel, line["model_id"]))
        entry.quantity = line["quantity"]
        entry.work_date = work_date
        entry.raw_text = draft["raw_text"]
        if needs_approval:
            entry.status = EntryStatus.PENDING
        await session.flush()
        await session.refresh(entry, ["worker", "model"])
        saved = [entry]
        header = "✅ <b>Yozuv yangilandi</b>"
    else:
        saved = []
        for line in draft["lines"]:
            saved.append(
                await repo.create_work_entry(
                    session,
                    worker_id=line["worker_id"],
                    model_id=line["model_id"],
                    quantity=line["quantity"],
                    work_date=work_date,
                    status=EntryStatus.PENDING if needs_approval else EntryStatus.APPROVED,
                    raw_text=draft["raw_text"],
                    created_by=user.id,
                )
            )
        header = "✅ <b>Saqlandi</b>"

    await state.clear()
    if needs_approval:
        header += "\n⏳ Egasining tasdig'ini kutmoqda."
    await call.message.edit_text(header)
    for entry in saved:
        await call.message.answer(entry_text(entry), reply_markup=kbs.entry_kb(entry, user))
    await call.answer()

    if needs_approval:
        for entry in saved:
            await notify_owner_pending(call.bot, session, entry)
