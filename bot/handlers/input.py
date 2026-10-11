"""Ovoz/matn -> LLM -> qoralama -> «To'g'rimi?» -> saqlash."""
import logging
from datetime import date, timedelta
from decimal import Decimal
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
from bot.db.models import EntryStatus, User
from bot.handlers.entries import entry_text, notify_owner_pending
from bot.handlers.money import debt_text, person_balance, personal_text
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

    money = user.is_owner and drafts.is_money(ext)
    if ext.section == "client" and user.is_owner:
        await status.edit_text(
            f"🗣 <i>«{escape(text)}»</i>\n\nBu mijozlar bo'limiga tegishli — u keyingi bosqichda qo'shiladi."
        )
        return

    if not money and (ext.section != "work" or not ext.work):
        example = (
            "«bozorga 50 ming won» yoki «A-12 modeldan 50 ta»"
            if user.is_owner
            else "«A-12 modeldan 50 ta topshirdim»"
        )
        hint = ext.question or f"Tushunmadim. Masalan: {example}."
        await status.edit_text(f"🗣 <i>«{escape(text)}»</i>\n\n🤔 {escape(hint)}")
        return

    raw = f"{draft['raw_text']} → {text}" if correcting and draft.get("raw_text") else text
    entry_id = draft["entry_id"] if correcting else None
    currency = (await repo.enabled_currencies(session, user.account_id))[0]
    new_draft = drafts.from_extraction(ext, raw, entry_id=entry_id, default_currency=currency)
    if entry_id and (new_draft["section"] != "work" or len(new_draft["lines"]) != 1):
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
    if draft["section"] != "work" or callback_data.line >= len(draft["lines"]):
        await call.answer()
        return
    line = draft["lines"][callback_data.line]

    if callback_data.kind == "worker" and user.is_owner:
        workers = {w.id: w for w in await repo.list_workers(session, user.account_id)}
        worker = workers.get(callback_data.item_id)
        if worker is None:
            await call.answer("Ishchi topilmadi.", show_alert=True)
            return
        if line["worker_q"] and line["worker_q"].lower() != worker.name.lower():
            await repo.save_alias(session, user.account_id, "worker", worker.id, line["worker_q"])
        line["worker_id"], line["worker_name"] = worker.id, worker.name

    elif callback_data.kind == "model":
        if callback_data.item_id == 0:
            if not user.is_owner or not line["model_q"]:
                await call.answer()
                return
            acc = user.account_id
            model = await repo.get_model_by_name(session, acc, line["model_q"]) or await repo.create_model(
                session, acc, line["model_q"].strip()
            )
        else:
            models = {m.id: m for m in await repo.list_models(session, user.account_id)}
            model = models.get(callback_data.item_id)
            if model is None:
                await call.answer("Model topilmadi.", show_alert=True)
                return
            if line["model_q"] and line["model_q"].lower() != model.name.lower():
                await repo.save_alias(session, user.account_id, "model", model.id, line["model_q"])
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
    if draft["section"] != "money" or callback_data.line >= len(draft["debt"]):
        await call.answer()
        return
    draft["debt"][callback_data.line]["direction"] = callback_data.direction
    await state.update_data(draft=draft)
    text_out, kb = await drafts.present(session, draft, user)
    await call.message.edit_text(text_out, reply_markup=kb)
    await call.answer()


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
    if draft["section"] == "money":
        if user.is_owner:
            await _save_money(call, state, session, user, draft)
        else:
            await call.answer()
        return

    acc = user.account_id
    needs_approval = not user.is_owner and await repo.require_approval(session, acc)
    work_date = date.fromisoformat(draft["date"])

    if draft["entry_id"]:
        entry = await repo.get_work_entry(session, acc, draft["entry_id"])
        if entry is None or (not user.is_owner and entry.worker_id != user.id):
            await state.clear()
            await call.message.edit_text("⚠️ Yozuv topilmadi (o'chirilgan bo'lishi mumkin).")
            await call.answer()
            return
        line = draft["lines"][0]
        entry.worker_id = line["worker_id"]
        if entry.model_id != line["model_id"]:
            model = await repo.get_model(session, acc, line["model_id"])
            if model is not None:
                repo.apply_model_price(entry, model)
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
                    owner_id=acc,
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


async def _save_money(call: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, draft: dict) -> None:
    entry_date = date.fromisoformat(draft["date"])
    cards: list[tuple[str, str, int]] = []
    for p in draft["personal"]:
        e = await repo.create_personal_entry(
            session,
            owner_id=user.id,
            kind=p["kind"],
            category=p["category"],
            amount=Decimal(p["amount"]),
            currency=p["currency"],
            note=p["note"],
            entry_date=entry_date,
            raw_text=draft["raw_text"],
        )
        cards.append((personal_text(e), "p", e.id))
    people: list[str] = []
    for d in draft["debt"]:
        e = await repo.create_debt_entry(
            session,
            owner_id=user.id,
            person=d["person"],
            direction=d["direction"],
            amount=Decimal(d["amount"]),
            currency=d["currency"],
            note=d["note"],
            entry_date=entry_date,
            raw_text=draft["raw_text"],
        )
        cards.append((debt_text(e), "d", e.id))
        if e.person not in people:
            people.append(e.person)

    await state.clear()
    header = "✅ <b>Saqlandi</b>"
    balance = [line for person in people for line in await person_balance(session, user.id, person)]
    if balance:
        header += "\n\n" + "\n".join(balance)
    await call.message.edit_text(header)
    for text, kind, entry_id in cards:
        await call.message.answer(text, reply_markup=kbs.money_entry_kb(kind, entry_id))
    await call.answer()
