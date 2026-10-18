"""Egasi uchun: ishchilar, kasblar, modellar va sozlamalar."""
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import jobs as jobs_mod
from bot import keyboards as kbs
from bot.db import repo
from bot.db.models import ItemModel, User
from bot.middlewares import IsOwner
from bot.services.utils import CURRENCY_LABELS, fmt_money, parse_price

router = Router(name="admin")
router.message.filter(IsOwner())
router.callback_query.filter(IsOwner())


class AdminSG(StatesGroup):
    worker_name = State()
    job_name = State()  # yangi kasb yoki kasb nomini o'zgartirish
    model_name = State()
    model_price = State()


# --- Ishchilar --------------------------------------------------------------

async def _invite_link(bot: Bot, token: str) -> str:
    me = await bot.me()
    return f"https://t.me/{me.username}?start=inv_{token}"


def _status(w: User) -> str:
    if not w.is_active:
        return "⛔ o'chirilgan"
    return "✅ ulangan" if w.tg_id else "🔗 havola kutilmoqda"


def _rows(count: int, size: int) -> list[int]:
    """count ta tugmani size tadan qatorga: oxirgi qator to'lmasa ham keyingi tugmalar unga qo'shilmaydi."""
    return [size] * (count // size) + ([count % size] if count % size else [])


async def _workers_view(session: AsyncSession, owner_id: int) -> tuple[str, InlineKeyboardMarkup]:
    workers = await repo.list_workers(session, owner_id, active_only=False)
    jobs = {j.id: j.name for j in await repo.list_jobs(session, owner_id)}
    kb = InlineKeyboardBuilder()
    lines = []
    for w in workers:
        job = jobs.get(w.job_id)
        what = escape(job) if job else "kasbi tanlanmagan"
        lines.append(f"• <b>{escape(w.name)}</b> — {what} · {_status(w)}")
        label = f"👷 {w.name} · {job}" if job else f"👷 {w.name}"
        kb.button(text=label, callback_data=kbs.WorkerCb(action="open", user_id=w.id))
    kb.button(text="➕ Ishchi qo'shish", callback_data=kbs.WorkerCb(action="add"))
    kb.button(text="🏷 Kasblar", callback_data=kbs.JobCb(action="list"))
    kb.adjust(*([1] * len(workers)), 2)
    text = "👷 <b>Ishchilar</b>\n\n" + ("\n".join(lines) if lines else "Hali ishchi yo'q.")
    if workers:
        text += "\n\nIshchini bosing — kasbini tanlash, taklif havolasi yoki o'chirish."
    return text, kb.as_markup()


async def _worker_card(session: AsyncSession, worker: User) -> tuple[str, InlineKeyboardMarkup]:
    jobs = await repo.list_jobs(session, worker.owner_id)
    current = next((j for j in jobs if j.id == worker.job_id), None)
    kb = InlineKeyboardBuilder()
    for j in jobs:
        mark = "✅ " if current is not None and j.id == current.id else ""
        kb.button(text=f"{mark}{j.name}", callback_data=kbs.WorkerCb(action="job", user_id=worker.id, value=j.id))
    kb.button(
        text=f"{'✅ ' if current is None else ''}Kasbsiz",
        callback_data=kbs.WorkerCb(action="job", user_id=worker.id, value=0),
    )
    kb.button(text="➕ Yangi kasb", callback_data=kbs.WorkerCb(action="newjob", user_id=worker.id))
    if worker.is_active and not worker.tg_id:
        kb.button(text="🔗 Yangi taklif havolasi", callback_data=kbs.WorkerCb(action="link", user_id=worker.id))
    kb.button(
        text="⛔ O‘chirish (botdan foydalana olmaydi)" if worker.is_active else "✅ Qayta faollashtirish",
        callback_data=kbs.WorkerCb(action="toggle", user_id=worker.id),
    )
    kb.button(text="↩️ Ishchilar", callback_data=kbs.WorkerCb(action="list"))
    kb.adjust(*_rows(len(jobs) + 2, 2), 1)
    job = f"<b>{escape(current.name)}</b>" if current else "tanlanmagan"
    text = (
        f"👷 <b>{escape(worker.name)}</b>\n\n"
        f"🏷 Kasbi: {job}\n"
        f"Holati: {_status(worker)}\n\n"
        "Kasbini tanlang. Ish topshirganda kasb aytilmasa — shu kasbning narxi olinadi."
    )
    return text, kb.as_markup()


async def _get_worker(call: CallbackQuery, session: AsyncSession, user: User, worker_id: int) -> User | None:
    worker = await repo.get_worker(session, user.id, worker_id)
    if worker is None:
        await call.answer("Ishchi topilmadi.", show_alert=True)
    return worker


@router.message(F.text == kbs.BTN_WORKERS)
async def workers(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    text, kb = await _workers_view(session, user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(kbs.WorkerCb.filter(F.action == "list"))
async def workers_list(call: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    text, kb = await _workers_view(session, user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(kbs.WorkerCb.filter(F.action == "open"))
async def worker_open(call: CallbackQuery, callback_data: kbs.WorkerCb, session: AsyncSession, user: User) -> None:
    worker = await _get_worker(call, session, user, callback_data.user_id)
    if worker is None:
        return
    text, kb = await _worker_card(session, worker)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(kbs.WorkerCb.filter(F.action == "add"))
async def worker_add(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminSG.worker_name)
    await call.message.answer("Yangi ishchining ismini yozing (masalan: <i>Alisher</i>):")
    await call.answer()


@router.message(AdminSG.worker_name, F.text, ~F.text.in_(kbs.MENU_TEXTS))
async def worker_add_name(message: Message, bot: Bot, state: FSMContext, session: AsyncSession, user: User) -> None:
    name = message.text.strip()[:100]
    if not name or name.startswith("/"):
        await message.answer("Ism bo'sh bo'lmasin. Qaytadan yozing:")
        return
    worker = await repo.create_worker(session, user.id, name)
    await state.clear()
    link = await _invite_link(bot, worker.invite_token)
    await message.answer(
        f"✅ <b>{escape(name)}</b> qo'shildi.\n\n"
        f"Ushbu havolani ishchiga yuboring — u bosganda bot unga ulanadi:\n{link}\n\n"
        "Havola bir martalik."
    )
    text, kb = await _worker_card(session, worker)
    await message.answer(text, reply_markup=kb)


@router.callback_query(kbs.WorkerCb.filter(F.action == "link"))
async def worker_link(
    call: CallbackQuery, callback_data: kbs.WorkerCb, bot: Bot, session: AsyncSession, user: User
) -> None:
    worker = await _get_worker(call, session, user, callback_data.user_id)
    if worker is None:
        return
    token = await repo.new_invite_token(session, worker)
    link = await _invite_link(bot, token)
    await call.message.answer(f"🔗 <b>{escape(worker.name)}</b> uchun yangi havola:\n{link}")
    await call.answer()


@router.callback_query(kbs.WorkerCb.filter(F.action == "toggle"))
async def worker_toggle(call: CallbackQuery, callback_data: kbs.WorkerCb, session: AsyncSession, user: User) -> None:
    worker = await _get_worker(call, session, user, callback_data.user_id)
    if worker is None:
        return
    worker.is_active = not worker.is_active
    await session.flush()
    text, kb = await _worker_card(session, worker)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer("Faollashtirildi" if worker.is_active else "O'chirildi — endi botdan foydalana olmaydi")


@router.callback_query(kbs.WorkerCb.filter(F.action == "job"))
async def worker_job(call: CallbackQuery, callback_data: kbs.WorkerCb, session: AsyncSession, user: User) -> None:
    worker = await _get_worker(call, session, user, callback_data.user_id)
    if worker is None:
        return
    job = await repo.get_job(session, user.id, callback_data.value)
    if callback_data.value and job is None:
        await call.answer("Kasb topilmadi.", show_alert=True)
        return
    if worker.job_id == (job.id if job else None):
        await call.answer()
        return
    worker.job_id = job.id if job else None
    await session.flush()
    text, kb = await _worker_card(session, worker)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer(f"{worker.name}: {job.name}" if job else f"{worker.name}: kasbsiz")


@router.callback_query(kbs.WorkerCb.filter(F.action == "newjob"))
async def worker_new_job(call: CallbackQuery, callback_data: kbs.WorkerCb, state: FSMContext) -> None:
    await state.set_state(AdminSG.job_name)
    await state.update_data(worker_id=callback_data.user_id, job_id=None)
    await call.message.answer("Yangi kasb nomini yozing (masalan: <i>Upakovkachi</i>):")
    await call.answer()


# --- Kasblar ----------------------------------------------------------------

async def _jobs_view(session: AsyncSession, owner_id: int) -> tuple[str, InlineKeyboardMarkup]:
    jobs = await repo.list_jobs(session, owner_id)
    counts = await repo.job_worker_counts(session, owner_id)
    kb = InlineKeyboardBuilder()
    lines = []
    for j in jobs:
        n = counts.get(j.id)
        lines.append(f"• <b>{escape(j.name)}</b>" + (f" — {n} ishchi" if n else ""))
        kb.button(text=f"✏️ {j.name}", callback_data=kbs.JobCb(action="rename", job_id=j.id))
        kb.button(text="🗑", callback_data=kbs.JobCb(action="del", job_id=j.id))
    kb.button(text="➕ Kasb qo'shish", callback_data=kbs.JobCb(action="add"))
    kb.button(text="↩️ Ishchilar", callback_data=kbs.WorkerCb(action="list"))
    kb.adjust(*([2] * len(jobs)), 2)
    text = (
        "🏷 <b>Kasblar</b>\n\n"
        + ("\n".join(lines) if lines else "Hali kasb yo'q.")
        + "\n\nHar bir ishchiga kasb beriladi («👷 Ishchilar»), har bir modelga esa kasb bo'yicha narx "
        "qo'yiladi («🧵 Modellar»). Ish topshirilganda kasb aytilsa — o'sha kasb, aytilmasa — ishchining "
        "o'z kasbi narxi olinadi."
    )
    return text, kb.as_markup()


@router.callback_query(kbs.JobCb.filter(F.action == "list"))
async def jobs_list(call: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    text, kb = await _jobs_view(session, user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(kbs.JobCb.filter(F.action.in_({"add", "rename"})))
async def job_ask_name(
    call: CallbackQuery, callback_data: kbs.JobCb, state: FSMContext, session: AsyncSession, user: User
) -> None:
    job = await repo.get_job(session, user.id, callback_data.job_id) if callback_data.action == "rename" else None
    if callback_data.action == "rename" and job is None:
        await call.answer("Kasb topilmadi.", show_alert=True)
        return
    await state.set_state(AdminSG.job_name)
    await state.update_data(job_id=job.id if job else None, worker_id=None)
    if job:
        await call.message.answer(f"✏️ <b>{escape(job.name)}</b> — yangi nomini yozing:")
    else:
        await call.message.answer("Yangi kasb nomini yozing (masalan: <i>Upakovkachi</i>):")
    await call.answer()


@router.message(AdminSG.job_name, F.text, ~F.text.in_(kbs.MENU_TEXTS))
async def job_name_value(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    name = jobs_mod.clean_name(message.text)
    if not name or name.startswith("/"):
        await message.answer("Nom bo'sh bo'lmasin. Qaytadan yozing:")
        return
    data = await state.get_data()
    if data.get("job_id"):
        job = await repo.get_job(session, user.id, data["job_id"])
        if job is None:
            await state.clear()
            return
        if not await repo.rename_job(session, job, name):
            await message.answer("Bunday kasb bor. Boshqa nom yozing:")
            return
    else:
        job = await repo.create_job(session, user.id, name)
    await state.clear()

    worker = await repo.get_worker(session, user.id, data["worker_id"]) if data.get("worker_id") else None
    if worker is not None:
        worker.job_id = job.id
        await session.flush()
        text, kb = await _worker_card(session, worker)
    else:
        text, kb = await _jobs_view(session, user.id)
    await message.answer(f"✅ Kasb: <b>{escape(job.name)}</b>\n\n{text}", reply_markup=kb)


@router.callback_query(kbs.JobCb.filter(F.action == "del"))
async def job_delete(call: CallbackQuery, callback_data: kbs.JobCb, session: AsyncSession, user: User) -> None:
    job = await repo.get_job(session, user.id, callback_data.job_id)
    if job is None:
        await call.answer()
        return
    kb = InlineKeyboardBuilder()
    kb.button(text=f"🗑 Ha, «{job.name}» o'chirilsin", callback_data=kbs.JobCb(action="del_yes", job_id=job.id))
    kb.button(text="↩️ Yo'q", callback_data=kbs.JobCb(action="list"))
    kb.adjust(1)
    await call.message.edit_reply_markup(reply_markup=kb.as_markup())
    await call.answer("Bu kasbdagi ishchilar kasbsiz qoladi. Eski yozuvlar o'zgarmaydi.", show_alert=True)


@router.callback_query(kbs.JobCb.filter(F.action == "del_yes"))
async def job_delete_yes(call: CallbackQuery, callback_data: kbs.JobCb, session: AsyncSession, user: User) -> None:
    job = await repo.get_job(session, user.id, callback_data.job_id)
    if job is not None:
        await repo.delete_job(session, job)
    text, kb = await _jobs_view(session, user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer("O'chirildi" if job else None)


# --- Modellar ---------------------------------------------------------------

# Telegram xabari 4096 belgigacha: modellar ko'p bo'lsa ro'yxat qisqartiriladi
MODELS_BUDGET = 3300


async def _models_view(session: AsyncSession, owner_id: int) -> tuple[str, InlineKeyboardMarkup]:
    models = await repo.list_models(session, owner_id, active_only=False)
    jobs = await repo.list_jobs(session, owner_id)
    prices = await repo.model_prices(session, [m.id for m in models])
    kb = InlineKeyboardBuilder()
    lines = []
    budget = MODELS_BUDGET
    for m in models:
        mark = "" if m.is_active else " ⛔"
        by_job = [
            f"{escape(j.name)} {fmt_money(prices[m.id][j.id].price, prices[m.id][j.id].currency)}"
            for j in jobs
            if j.id in prices.get(m.id, {})
        ]
        line = f"• <b>{escape(m.name)}</b>{mark}"
        if m.price is not None:
            line += f" — umumiy: {fmt_money(m.price, m.currency)}"
        elif not by_job:
            line += " — narx yo'q"
        if by_job:
            line += "\n   " + " · ".join(by_job)
        if len(line) <= budget:
            budget -= len(line) + 1
            lines.append(line)
        kb.button(text=f"💰 {m.name}", callback_data=kbs.ModelCb(action="price", model_id=m.id))
        kb.button(
            text="⛔ O‘chirish" if m.is_active else "✅ Qaytarish",
            callback_data=kbs.ModelCb(action="toggle", model_id=m.id),
        )
    if len(lines) < len(models):
        lines.append(f"… yana {len(models) - len(lines)} ta model — narxini ko'rish uchun tugmasini bosing")
    kb.button(text="➕ Model qo'shish", callback_data=kbs.ModelCb(action="add"))
    kb.adjust(*([2] * len(models)), 1)
    text = "🧵 <b>Modellar</b> — bir dona uchun ish haqi\n\n" + (
        "\n".join(lines) if lines else "Hali model yo'q."
    )
    text += "\n\n💡 Narxni ovoz yoki matn bilan ham aytish mumkin: <i>«A-12 modelga brsovka 2000dan»</i>."
    return text, kb.as_markup()


@router.message(F.text == kbs.BTN_MODELS)
async def models(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    text, kb = await _models_view(session, user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(kbs.ModelCb.filter(F.action == "add"))
async def model_add(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminSG.model_name)
    await call.message.answer("Model nomini yozing (masalan: <i>A-12</i>):")
    await call.answer()


@router.message(AdminSG.model_name, F.text, ~F.text.in_(kbs.MENU_TEXTS))
async def model_add_name(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    name = message.text.strip()[:100]
    if not name or name.startswith("/"):
        await message.answer("Nom bo'sh bo'lmasin. Qaytadan yozing:")
        return
    if await repo.get_model_by_name(session, user.id, name):
        await message.answer("Bunday model allaqachon bor. Boshqa nom yozing:")
        return
    model = await repo.create_model(session, user.id, name)
    await state.set_state(AdminSG.model_price)
    await state.update_data(model_id=model.id, target=None)
    text, kb = await _price_card(session, model, None)
    await message.answer(text, reply_markup=kb)


async def _price_card(session: AsyncSession, model: ItemModel, target: int | None) -> tuple[str, InlineKeyboardMarkup]:
    """Model narxlari: umumiy va har bir kasb. target — narxi so'ralayotgan kasb (0 — umumiy)."""
    jobs = await repo.list_jobs(session, model.owner_id)
    prices = (await repo.model_prices(session, [model.id])).get(model.id, {})
    rows = [f"• Umumiy: {fmt_money(model.price, model.currency) if model.price is not None else '—'}"]
    for j in jobs:
        row = prices.get(j.id)
        rows.append(f"• {escape(j.name)}: {fmt_money(row.price, row.currency) if row else '—'}")
    kb = InlineKeyboardBuilder()
    for j in jobs:
        mark = "✏️ " if target == j.id else ""
        kb.button(text=f"{mark}{j.name}", callback_data=kbs.ModelCb(action="pjob", model_id=model.id, value=j.id))
    kb.button(
        text=f"{'✏️ ' if target == 0 else ''}Umumiy",
        callback_data=kbs.ModelCb(action="pjob", model_id=model.id, value=0),
    )
    kb.button(text="✅ Tayyor", callback_data=kbs.ModelCb(action="done", model_id=model.id))
    kb.adjust(*_rows(len(jobs) + 1, 3), 1)

    text = f"🧵 <b>{escape(model.name)}</b> — bir dona uchun ish haqi\n\n" + "\n".join(rows) + "\n\n"
    if target is None:
        text += (
            "Kasbni bosing yoki narxlarni yozing: <i>«brsovka 2000»</i>, <i>«kroy 1500, tikuv 5000»</i>.\n"
            "Faqat son yozsangiz — umumiy narx (kasbining narxi yozilmagan ishlarga olinadi)."
        )
    else:
        name = "Umumiy narx" if target == 0 else next((j.name for j in jobs if j.id == target), "Kasb")
        text += f"✏️ <b>{escape(name)}</b> — bir donasi qancha? Yozing, masalan: <i>2000</i>. «0» — narxni olib tashlash."
    return text, kb.as_markup()


@router.callback_query(kbs.ModelCb.filter(F.action == "price"))
async def model_price(
    call: CallbackQuery, callback_data: kbs.ModelCb, state: FSMContext, session: AsyncSession, user: User
) -> None:
    model = await repo.get_model(session, user.id, callback_data.model_id)
    if model is None:
        await call.answer()
        return
    await state.set_state(AdminSG.model_price)
    await state.update_data(model_id=model.id, target=None)
    text, kb = await _price_card(session, model, None)
    await call.message.answer(text, reply_markup=kb)
    await call.answer()


@router.callback_query(kbs.ModelCb.filter(F.action == "pjob"))
async def model_price_target(
    call: CallbackQuery, callback_data: kbs.ModelCb, state: FSMContext, session: AsyncSession, user: User
) -> None:
    model = await repo.get_model(session, user.id, callback_data.model_id)
    if model is None or (callback_data.value and await repo.get_job(session, user.id, callback_data.value) is None):
        await call.answer("Topilmadi.", show_alert=True)
        return
    await state.set_state(AdminSG.model_price)
    await state.update_data(model_id=model.id, target=callback_data.value)
    text, kb = await _price_card(session, model, callback_data.value)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(kbs.ModelCb.filter(F.action.in_({"done", "noprice"})))
async def model_price_done(call: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    text, kb = await _models_view(session, user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.message(AdminSG.model_price, F.text, ~F.text.in_(kbs.MENU_TEXTS))
async def model_price_value(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    model = await repo.get_model(session, user.id, data["model_id"]) if data.get("model_id") else None
    if model is None:
        await state.clear()
        return
    currency = (await repo.enabled_currencies(session, user.id))[0]
    jobs = await repo.list_jobs(session, user.id)
    target = data.get("target")

    pairs = jobs_mod.split_prices(message.text)
    if target is not None and len(pairs) == 1 and not pairs[0][0]:
        # Kasb tugmasi bosilgan va faqat son yozilgan — shu kasb narxi
        parsed = parse_price(pairs[0][1], currency)
        if parsed is None:
            await message.answer("🤔 Narxni tushunmadim. Masalan: <i>2000</i> yoki <i>2 ming</i>")
            return
        job = next((j for j in jobs if j.id == target), None)
        items = [(job, parsed[0] if parsed[0] > 0 else None, parsed[1])]
    else:
        saved = await repo.get_aliases(session, user.id, "job")
        items, error = jobs_mod.parse_prices(message.text, jobs, currency, saved)
        if error:
            await message.answer(f"🤔 {escape(error)}")
            return

    done = []
    for job, value, cur in items:
        await repo.set_model_price(session, model, job.id if job else None, value, cur)
        name = escape(job.name) if job else "Umumiy"
        done.append(f"✅ {name}: {fmt_money(value, cur)}" if value is not None else f"🗑 {name}: narx olib tashlandi")
    await state.update_data(target=None)
    text, kb = await _price_card(session, model, None)
    await message.answer("\n".join(done) + "\n\n" + text, reply_markup=kb)


@router.callback_query(kbs.ModelCb.filter(F.action == "toggle"))
async def model_toggle(call: CallbackQuery, callback_data: kbs.ModelCb, session: AsyncSession, user: User) -> None:
    model = await repo.get_model(session, user.id, callback_data.model_id)
    if model is None:
        await call.answer()
        return
    model.is_active = not model.is_active
    await session.flush()
    text, kb = await _models_view(session, user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


# --- Sozlamalar -------------------------------------------------------------

async def _settings_view(session: AsyncSession, owner_id: int) -> tuple[str, InlineKeyboardMarkup]:
    approval = await repo.require_approval(session, owner_id)
    auto_images = await repo.auto_save_images(session, owner_id)
    enabled = await repo.enabled_currencies(session, owner_id)
    remind = await repo.reminder_hour(session, owner_id)
    kb = InlineKeyboardBuilder()
    kb.button(
        text=f"{'✅' if approval else '⬜'} Ishchi yozuvlarini tasdiqlash",
        callback_data=kbs.SettingsCb(action="approval"),
    )
    kb.button(
        text=f"{'✅' if auto_images else '⬜'} Rasmdagi xarajatlarni darhol saqlash",
        callback_data=kbs.SettingsCb(action="autoimg"),
    )
    for code in repo.ALL_CURRENCIES:
        kb.button(
            text=f"{'✅' if code in enabled else '⬜'} {code} ({CURRENCY_LABELS[code]})",
            callback_data=kbs.SettingsCb(action="cur", value=code),
        )
    for code in repo.ALL_CURRENCIES:
        kb.button(
            text=f"{'⭐' if code == enabled[0] else '☆'} {code}",
            callback_data=kbs.SettingsCb(action="main", value=code),
        )
    kb.button(text=f"{'✅ ' if remind is None else ''}🔕 O'chiq", callback_data=kbs.SettingsCb(action="remind", value="off"))
    for hour in repo.REMINDER_HOURS:
        kb.button(
            text=f"{'✅ ' if hour == remind else ''}🔔 {hour}:00",
            callback_data=kbs.SettingsCb(action="remind", value=str(hour)),
        )
    kb.adjust(1, 1, 3, 3, 1 + len(repo.REMINDER_HOURS))
    images_mode = (
        "tasdiqlamasdan darhol hisobga qo'shiladi"
        if auto_images
        else "o'qilgani ko'rsatiladi, «✅ Tasdiqlash» bosilgach hisobga qo'shiladi"
    )
    text = (
        "⚙️ <b>Sozlamalar</b>\n\n"
        f"• Ishchi yozuvlari: {'egasi tasdiqlagandan keyin' if approval else 'darhol'} hisobotga tushadi\n"
        f"• 🖼 Bank skrinshoti yoki chek rasmi: {images_mode}\n"
        f"• Valyutalar: {', '.join(enabled)}\n"
        f"• ⭐ Asosiy valyuta: <b>{enabled[0]}</b> — summa valyutasiz aytilsa shu olinadi\n"
        "• 🔔 Kundalik eslatma («bugungi pulingizni kiriting»): "
        + (f"har kuni <b>{remind}:00</b> da" if remind is not None else "o'chiq")
    )
    return text, kb.as_markup()


@router.message(F.text == kbs.BTN_SETTINGS)
async def settings_menu(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    text, kb = await _settings_view(session, user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(kbs.SettingsCb.filter())
async def settings_toggle(
    call: CallbackQuery, callback_data: kbs.SettingsCb, session: AsyncSession, user: User
) -> None:
    if callback_data.action == "approval":
        current = await repo.require_approval(session, user.id)
        await repo.set_setting(session, user.id, "require_approval", "0" if current else "1")
    elif callback_data.action == "autoimg":
        current = await repo.auto_save_images(session, user.id)
        await repo.set_setting(session, user.id, "auto_save_images", "0" if current else "1")
    elif callback_data.action == "cur":
        enabled = await repo.enabled_currencies(session, user.id)
        code = callback_data.value
        if code in enabled:
            if len(enabled) == 1:
                await call.answer("Kamida bitta valyuta yoqilgan bo'lishi kerak.", show_alert=True)
                return
            enabled.remove(code)
        else:
            enabled.append(code)
        await repo.set_currencies(session, user.id, enabled)
    elif callback_data.action == "remind":
        value = callback_data.value
        if value == "off" or (value.isdigit() and int(value) in repo.REMINDER_HOURS):
            await repo.set_setting(session, user.id, "reminder_hour", value)
    elif callback_data.action == "main" and callback_data.value in repo.ALL_CURRENCIES:
        await repo.set_main_currency(session, user.id, callback_data.value)
    text, kb = await _settings_view(session, user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()
