"""Egasi uchun: ishchilar, kasblar, modellar, ta'minotchilar, mijozlar va sozlamalar."""
from collections import defaultdict
from decimal import Decimal
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import drafts
from bot import jobs as jobs_mod
from bot import keyboards as kbs
from bot.db import repo
from bot.db.models import Client, ItemModel, Supplier, User
from bot.handlers.entries import ledger_text, tracked
from bot.handlers.money import client_balance_text, client_ledger_blocks, supplier_month_line
from bot.middlewares import IsOwner
from bot.reminders import PAY_REMINDER_HOUR
from bot.services.utils import CURRENCY_LABELS, fmt_money, fmt_totals, parse_price, today

router = Router(name="admin")
router.message.filter(IsOwner())
router.callback_query.filter(IsOwner())


class AdminSG(StatesGroup):
    worker_name = State()
    job_name = State()  # yangi kasb yoki kasb nomini o'zgartirish
    job_price = State()  # kasbning o'z narxi (hamma modellar uchun)
    job_model_price = State()  # kasbning modellar bo'yicha narxi
    model_name = State()
    model_price = State()
    supplier_name = State()
    client_name = State()


# --- Ishchilar --------------------------------------------------------------

async def invite_link(bot: Bot, token: str, prefix: str = "inv") -> str:
    """Taklif havolasi: ishchiga — inv_, ta'minotchiga — sup_, mijozga — cli_."""
    me = await bot.me()
    return f"https://t.me/{me.username}?start={prefix}_{token}"


def _status(w: User | Supplier) -> str:
    if not w.is_active:
        return "⛔ o'chirilgan"
    return "✅ ulangan" if w.tg_id else "🔗 havola kutilmoqda"


def _rows(count: int, size: int) -> list[int]:
    """count ta tugmani size tadan qatorga: oxirgi qator to'lmasa ham keyingi tugmalar unga qo'shilmaydi."""
    return [size] * (count // size) + ([count % size] if count % size else [])


async def _workers_view(session: AsyncSession, owner_id: int) -> tuple[str, InlineKeyboardMarkup]:
    workers = await repo.list_workers(session, owner_id, active_only=False)
    jobs = {j.id: j.name for j in await repo.list_jobs(session, owner_id)}
    balances = await repo.worker_balances(session, owner_id)
    kb = InlineKeyboardBuilder()
    lines = []
    for w in workers:
        job = jobs.get(w.job_id)
        what = escape(job) if job else "kasbi tanlanmagan"
        line = f"• <b>{escape(w.name)}</b> — {what} · {_status(w)}"
        if tracked(balances.get(w.id)):
            line += f"\n   🧾 qoldiq: {drafts.fmt_signed(balances[w.id].balance)}"
        lines.append(line)
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
    kb.button(text="🧾 Hisob-kitob", callback_data=kbs.WorkerCb(action="ledger", user_id=worker.id))
    if worker.is_active and not worker.tg_id:
        kb.button(text="🔗 Yangi taklif havolasi", callback_data=kbs.WorkerCb(action="link", user_id=worker.id))
    kb.button(
        text="⛔ O‘chirish (botdan foydalana olmaydi)" if worker.is_active else "✅ Qayta faollashtirish",
        callback_data=kbs.WorkerCb(action="toggle", user_id=worker.id),
    )
    kb.button(text="↩️ Ishchilar", callback_data=kbs.WorkerCb(action="list"))
    kb.adjust(*_rows(len(jobs) + 2, 2), 1)
    job = f"<b>{escape(current.name)}</b>" if current else "tanlanmagan"
    b = await repo.worker_balance(session, worker.owner_id, worker.id)
    text = (
        f"👷 <b>{escape(worker.name)}</b>\n\n"
        f"🏷 Kasbi: {job}\n"
        f"Holati: {_status(worker)}\n"
        f"{drafts.balance_text(b.balance, worker.name)}\n\n"
        "Kasbini tanlang. Ish topshirganda kasb aytilmasa — shu kasbning narxi olinadi."
    )
    if not tracked(b):
        text += (
            "\n\n💡 Qoldiq — hamma ishlari summasi (pul yozilmagan). Avval hisobni boshlang: "
            f"<i>«{escape(worker.name)} bilan hisob yopildi»</i> yoki <i>«{escape(worker.name)}ga 900 ming "
            "qarzman»</i>, keyin pul berganingizda ayting: "
            f"<i>«{escape(worker.name)}ga 500 ming berdim»</i>."
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


@router.callback_query(kbs.WorkerCb.filter(F.action == "ledger"))
async def worker_ledger(call: CallbackQuery, callback_data: kbs.WorkerCb, session: AsyncSession, user: User) -> None:
    worker = await _get_worker(call, session, user, callback_data.user_id)
    if worker is None:
        return
    await call.message.answer(await ledger_text(session, user, worker), reply_markup=kbs.app_kb("ledger"))
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
    link = await invite_link(bot, worker.invite_token)
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
    link = await invite_link(bot, token)
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


# --- Ta'minotchilar: xom ashyo kimdan olinadi ---------------------------------

SUPPLIERS_BUDGET = 3300


async def _suppliers_view(session: AsyncSession, owner_id: int) -> tuple[str, InlineKeyboardMarkup]:
    suppliers = await repo.list_suppliers(session, owner_id, active_only=False)
    stats = await repo.supplier_stats(session, owner_id, today().replace(day=1))
    kb = InlineKeyboardBuilder()
    lines = []
    budget = SUPPLIERS_BUDGET
    for sp in suppliers:
        line = f"• <b>{escape(sp.name)}</b> · {_status(sp)}"
        st = stats.get(sp.id)
        if st is not None and st.month_count:
            line += f"\n   {supplier_month_line(st)}"
        if len(line) <= budget:
            budget -= len(line) + 1
            lines.append(line)
        kb.button(text=f"🏪 {sp.name}", callback_data=kbs.SupplierCb(action="open", supplier_id=sp.id))
    if len(lines) < len(suppliers):
        lines.append(f"… yana {len(suppliers) - len(lines)} ta — ko'rish uchun tugmasini bosing")
    kb.button(text="➕ Ta'minotchi qo'shish", callback_data=kbs.SupplierCb(action="add"))
    kb.button(text="↩️ Xom ashyo", callback_data=kbs.MaterialReportCb(period="month"))
    kb.adjust(*([1] * len(suppliers)), 2)
    text = "🏪 <b>Ta'minotchilar</b> — xom ashyo kimdan olinadi\n\n" + (
        "\n".join(lines) if lines else "Hali ta'minotchi yo'q."
    )
    text += (
        "\n\n💡 Ta'minotchini qo'shib, taklif havolasini unga yuboring. U botga ulangach, undan xom ashyo "
        "olganingizda unga xabar boradi: <i>«Xayrulloh Uz charmdan 1 millionlik material oldim»</i>."
    )
    return text, kb.as_markup()


async def _supplier_card(session: AsyncSession, supplier: Supplier) -> tuple[str, InlineKeyboardMarkup]:
    st = (await repo.supplier_stats(session, supplier.owner_id, today().replace(day=1))).get(supplier.id)
    recent = await repo.list_material_entries(session, supplier.owner_id, supplier_id=supplier.id, limit=5)
    kb = InlineKeyboardBuilder()
    if supplier.is_active and not supplier.tg_id:
        kb.button(text="🔗 Yangi taklif havolasi", callback_data=kbs.SupplierCb(action="link", supplier_id=supplier.id))
    kb.button(
        text="⛔ O‘chirish (xabar bormaydi)" if supplier.is_active else "✅ Qayta faollashtirish",
        callback_data=kbs.SupplierCb(action="toggle", supplier_id=supplier.id),
    )
    kb.button(text="↩️ Ta'minotchilar", callback_data=kbs.SupplierCb(action="list"))
    kb.adjust(1)
    parts = [f"🏪 <b>{escape(supplier.name)}</b>\n\nHolati: {_status(supplier)}"]
    if st is not None:
        total = fmt_totals(st.total) if st.total else "narxi aytilmagan"
        parts.append(f"{supplier_month_line(st)}\nHammasi: <b>{total}</b> ({st.count} ta xarid)")
    if recent:
        rows = [
            f"• {e.entry_date.strftime('%d.%m')} — "
            + drafts.material_line(e.kind, e.name, e.quantity, e.unit, e.unit_price, e.amount, e.currency)
            for e in recent
        ]
        parts.append("🕘 <b>Oxirgi xaridlar</b>\n" + "\n".join(rows))
    if not supplier.is_active:
        parts.append("O'chirilgan: undan olinganda xabar bormaydi.")
    elif supplier.tg_id:
        parts.append(
            f"Undan xom ashyo olganingizda ayting: <i>«{escape(supplier.name)}dan 1 millionlik material oldim»</i> — "
            "unga xabar boradi."
        )
    else:
        parts.append("Taklif havolasini unga yuboring — u bosganda botga ulanadi va undan olinganda xabar oladi.")
    return "\n\n".join(parts), kb.as_markup()


async def _get_supplier(call: CallbackQuery, session: AsyncSession, user: User, supplier_id: int) -> Supplier | None:
    supplier = await repo.get_supplier(session, user.id, supplier_id)
    if supplier is None:
        await call.answer("Ta'minotchi topilmadi.", show_alert=True)
    return supplier


@router.callback_query(kbs.SupplierCb.filter(F.action == "list"))
async def suppliers_list(call: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    text, kb = await _suppliers_view(session, user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(kbs.SupplierCb.filter(F.action == "open"))
async def supplier_open(
    call: CallbackQuery, callback_data: kbs.SupplierCb, session: AsyncSession, user: User
) -> None:
    supplier = await _get_supplier(call, session, user, callback_data.supplier_id)
    if supplier is None:
        return
    text, kb = await _supplier_card(session, supplier)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(kbs.SupplierCb.filter(F.action == "add"))
async def supplier_add(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminSG.supplier_name)
    await call.message.answer(
        "Ta'minotchining ismi yoki do'koni nomini yozing (masalan: <i>Xayrulloh Uz charm</i>):"
    )
    await call.answer()


@router.message(AdminSG.supplier_name, F.text, ~F.text.in_(kbs.MENU_TEXTS))
async def supplier_add_name(message: Message, bot: Bot, state: FSMContext, session: AsyncSession, user: User) -> None:
    name = message.text.strip()[:100]
    if not name or name.startswith("/"):
        await message.answer("Nom bo'sh bo'lmasin. Qaytadan yozing:")
        return
    if await repo.get_supplier_by_name(session, user.id, name):
        await message.answer("Bunday ta'minotchi bor. Boshqa nom yozing:")
        return
    supplier = await repo.create_supplier(session, user.id, name)
    await state.clear()
    link = await invite_link(bot, supplier.invite_token, "sup")
    await message.answer(
        f"✅ <b>{escape(supplier.name)}</b> qo'shildi.\n\n"
        "Ushbu havolani unga yuboring — u bosganda bot unga ulanadi va undan xom ashyo olganingizda "
        f"xabar oladi:\n{link}\n\nHavola bir martalik."
    )
    text, kb = await _supplier_card(session, supplier)
    await message.answer(text, reply_markup=kb)


@router.callback_query(kbs.SupplierCb.filter(F.action == "link"))
async def supplier_link(
    call: CallbackQuery, callback_data: kbs.SupplierCb, bot: Bot, session: AsyncSession, user: User
) -> None:
    supplier = await _get_supplier(call, session, user, callback_data.supplier_id)
    if supplier is None:
        return
    link = await invite_link(bot, await repo.new_supplier_token(session, supplier), "sup")
    await call.message.answer(f"🔗 <b>{escape(supplier.name)}</b> uchun yangi havola:\n{link}")
    await call.answer()


@router.callback_query(kbs.SupplierCb.filter(F.action == "toggle"))
async def supplier_toggle(
    call: CallbackQuery, callback_data: kbs.SupplierCb, session: AsyncSession, user: User
) -> None:
    supplier = await _get_supplier(call, session, user, callback_data.supplier_id)
    if supplier is None:
        return
    supplier.is_active = not supplier.is_active
    await session.flush()
    text, kb = await _supplier_card(session, supplier)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer("Faollashtirildi" if supplier.is_active else "O'chirildi — endi xabar bormaydi")


# --- Mijozlar: optom savdo ------------------------------------------------------

CLIENTS_BUDGET = 3000
CLIENT_BUTTONS = 40
CLIENT_HOWTO = (
    "Ulangan mijoz siz bilan oldi-berdini botda ko'rib turadi: siz yozganingiz unga boradi, u "
    "<i>«10 komplekt oldim»</i>, <i>«2 million berdim»</i> desa — hisobga yoziladi va sizga xabar keladi (✍️)."
)


def _client_status(c: Client) -> str:
    if not c.is_active:
        return "⛔ o'chirilgan"
    return "✅ ulangan" if c.tg_id else "🔗 ulanmagan"


async def _clients_view(session: AsyncSession, owner_id: int) -> tuple[str, InlineKeyboardMarkup]:
    stats = await repo.client_stats(session, owner_id)
    kb = InlineKeyboardBuilder()
    lines = []
    budget = CLIENTS_BUDGET
    owes: dict[str, Decimal] = defaultdict(Decimal)
    for st in stats:
        balance = {c: v for c, v in st.balance.items() if v}
        for currency, value in balance.items():
            if value > 0:
                owes[currency] += value
        line = f"• {client_balance_text(st.client.name, balance)} · {_client_status(st.client)}"
        if len(line) <= budget:
            budget -= len(line) + 1
            lines.append(line)
    if len(lines) < len(stats):
        lines.append(f"… yana {len(stats) - len(lines)} ta — ilovada")
    buttons = stats[:CLIENT_BUTTONS]
    for st in buttons:
        kb.button(text=f"👤 {st.client.name}", callback_data=kbs.ClientCb(action="open", client_id=st.client.id))
    kb.button(text="➕ Mijoz qo'shish", callback_data=kbs.ClientCb(action="add"))
    kb.adjust(*_rows(len(buttons), 2), 1)
    kbs.add_app_button(kb, "clients")
    if not stats:
        text = (
            "🛍 <b>Mijozlar</b>\n\nHali mijoz yo'q.\n\n«➕ Mijoz qo'shish» — ismini yozing, bot taklif havolasini "
            "beradi. Yoki ovoz bilan ayting: <i>«Tohirga 10 komplekt maksima berdim, 140 mingdan»</i>, "
            "<i>«Tohir 2 million to'ladi»</i>."
        )
        return text, kb.as_markup()
    text = "🛍 <b>Mijozlar</b>\n\n" + "\n".join(lines)
    if owes:
        text += f"\n\nJami mijozlar qarzi: <b>{fmt_totals(dict(owes))}</b>"
    text += "\n\n💡 Mijozni bosing — oldi-berdi va taklif havolasi. " + CLIENT_HOWTO
    return text, kb.as_markup()


async def _client_card(bot: Bot, session: AsyncSession, client: Client) -> tuple[str, InlineKeyboardMarkup]:
    balance = await repo.client_balance(session, client.owner_id, client.id)
    blocks, more = await client_ledger_blocks(session, client, client_side=False)
    kb = InlineKeyboardBuilder()
    if client.is_active and not client.tg_id:
        label = "🔗 Yangi taklif havolasi" if client.invite_token else "🔗 Taklif havolasi"
        kb.button(text=label, callback_data=kbs.ClientCb(action="link", client_id=client.id))
    kb.button(
        text="⛔ O‘chirish (xabar bormaydi)" if client.is_active else "✅ Qayta faollashtirish",
        callback_data=kbs.ClientCb(action="toggle", client_id=client.id),
    )
    kb.button(text="↩️ Mijozlar", callback_data=kbs.ClientCb(action="list"))
    kb.adjust(1)
    kbs.add_app_button(kb, "clients")

    head = f"👤 <b>{escape(client.name)}</b>"
    if client.phone:
        head += f"\n📞 {escape(client.phone)}"
    parts = [head, f"Holati: {_client_status(client)}\n{client_balance_text(client.name, balance)}"]
    if blocks:
        parts.append("🕘 <b>Oldi-berdi</b>" + ("\n<i>… oldingilari — ilovada</i>" if more else ""))
        parts.extend(blocks)
    if not client.is_active:
        parts.append("O'chirilgan: mijozga xabar bormaydi, u botda yoza olmaydi.")
    elif client.tg_id:
        parts.append(CLIENT_HOWTO)
    elif client.invite_token:
        link = await invite_link(bot, client.invite_token, "cli")
        parts.append(f"Ushbu havolani mijozga yuboring — bosganda botga ulanadi:\n{link}\n\n{CLIENT_HOWTO}")
    else:
        parts.append(f"«🔗 Taklif havolasi»ni bosing va havolani mijozga yuboring. {CLIENT_HOWTO}")
    return "\n\n".join(parts), kb.as_markup()


async def _get_client(call: CallbackQuery, session: AsyncSession, user: User, client_id: int) -> Client | None:
    client = await repo.get_client(session, user.id, client_id)
    if client is None:
        await call.answer("Mijoz topilmadi.", show_alert=True)
    return client


@router.message(F.text == kbs.BTN_CLIENTS)
async def clients(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    text, kb = await _clients_view(session, user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(kbs.ClientCb.filter(F.action == "list"))
async def clients_list(call: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    text, kb = await _clients_view(session, user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(kbs.ClientCb.filter(F.action == "open"))
async def client_open(
    call: CallbackQuery, callback_data: kbs.ClientCb, bot: Bot, session: AsyncSession, user: User
) -> None:
    client = await _get_client(call, session, user, callback_data.client_id)
    if client is None:
        return
    text, kb = await _client_card(bot, session, client)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(kbs.ClientCb.filter(F.action == "add"))
async def client_add(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminSG.client_name)
    await call.message.answer("Mijozning ismini yozing (masalan: <i>Tohir</i>):")
    await call.answer()


@router.message(AdminSG.client_name, F.text, ~F.text.in_(kbs.MENU_TEXTS))
async def client_add_name(message: Message, bot: Bot, state: FSMContext, session: AsyncSession, user: User) -> None:
    name = message.text.strip()[:100]
    if not name or name.startswith("/"):
        await message.answer("Ism bo'sh bo'lmasin. Qaytadan yozing:")
        return
    await state.clear()
    client = await repo.get_client_by_name(session, user.id, name)
    if client is not None:
        if not client.is_active:
            client.is_active = True
        if not client.tg_id and not client.invite_token:
            await repo.new_client_token(session, client)
        await message.answer(f"ℹ️ <b>{escape(client.name)}</b> ro'yxatda bor:")
    else:
        client = await repo.create_client(session, user.id, name)
        link = await invite_link(bot, client.invite_token, "cli")
        await message.answer(
            f"✅ <b>{escape(client.name)}</b> qo'shildi.\n\n"
            "Ushbu havolani mijozga yuboring — u bosganda botga ulanadi va siz bilan oldi-berdini ko'rib turadi. "
            f"U «10 komplekt oldim», «2 million berdim» desa ham — hisobga yoziladi:\n{link}\n\nHavola bir martalik."
        )
    text, kb = await _client_card(bot, session, client)
    await message.answer(text, reply_markup=kb)


@router.callback_query(kbs.ClientCb.filter(F.action == "link"))
async def client_link(
    call: CallbackQuery, callback_data: kbs.ClientCb, bot: Bot, session: AsyncSession, user: User
) -> None:
    client = await _get_client(call, session, user, callback_data.client_id)
    if client is None:
        return
    link = await invite_link(bot, await repo.new_client_token(session, client), "cli")
    await call.message.answer(
        f"🔗 <b>{escape(client.name)}</b> uchun taklif havolasi (bir martalik):\n{link}\n\n"
        "Mijozga yuboring — bosganda botga ulanadi va siz bilan oldi-berdini ko'radi."
    )
    await call.answer()


@router.callback_query(kbs.ClientCb.filter(F.action == "toggle"))
async def client_toggle(
    call: CallbackQuery, callback_data: kbs.ClientCb, bot: Bot, session: AsyncSession, user: User
) -> None:
    client = await _get_client(call, session, user, callback_data.client_id)
    if client is None:
        return
    client.is_active = not client.is_active
    await session.flush()
    text, kb = await _client_card(bot, session, client)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer("Faollashtirildi" if client.is_active else "O'chirildi — endi xabar bormaydi")


# --- Kasblar ----------------------------------------------------------------

def _job_price(j) -> str:
    return fmt_money(j.price, j.currency) if j.price is not None else "narx yo'q"


async def _jobs_view(session: AsyncSession, owner_id: int) -> tuple[str, InlineKeyboardMarkup]:
    jobs = await repo.list_jobs(session, owner_id)
    counts = await repo.job_worker_counts(session, owner_id)
    priced = await repo.priced_jobs(session, owner_id)
    kb = InlineKeyboardBuilder()
    lines = []
    for j in jobs:
        n = counts.get(j.id)
        if not j.model_free:
            mode = "model tanlanadi"
        else:
            mode = "modellar narxi yoki modelsiz" if j.id in priced else "modelsiz"
        lines.append(f"• <b>{escape(j.name)}</b> — {_job_price(j)} · {mode}" + (f" · {n} ishchi" if n else ""))
        kb.button(text=f"🏷 {j.name}", callback_data=kbs.JobCb(action="open", job_id=j.id))
    kb.button(text="➕ Kasb qo'shish", callback_data=kbs.JobCb(action="add"))
    kb.button(text="↩️ Ishchilar", callback_data=kbs.WorkerCb(action="list"))
    kb.adjust(*_rows(len(jobs), 2), 2)
    text = (
        "🏷 <b>Kasblar</b>\n\n"
        + ("\n".join(lines) if lines else "Hali kasb yo'q.")
        + "\n\nKasbni bosing — umumiy narx va modellar bo'yicha narx (tikuvchi va kroychining narxi alohida). "
        "«✂️ Modelsiz ham bo'ladi» (kroy, brsovka): modellarga narx qo'yilmagan bo'lsa — <i>«50 ta ish "
        "qildim»</i> umumiy narx bilan darhol yoziladi; qo'yilgan bo'lsa — modellar narxi bilan variant chiqadi."
    )
    return text, kb.as_markup()


JOB_CARD_MODELS = 8
JOB_PRICE_MODELS = 40


async def _job_model_prices(session: AsyncSession, job) -> list[tuple[ItemModel, object]]:
    """Faol modellar va ularning shu kasb narxi (bo'lmasa None)."""
    models = await repo.list_models(session, job.owner_id)
    prices = await repo.model_prices(session, [m.id for m in models])
    return [(m, prices.get(m.id, {}).get(job.id)) for m in models]


async def _job_card(session: AsyncSession, job) -> tuple[str, InlineKeyboardMarkup]:
    n = (await repo.job_worker_counts(session, job.owner_id)).get(job.id, 0)
    priced = [(m, row) for m, row in await _job_model_prices(session, job) if row is not None]
    kb = InlineKeyboardBuilder()
    kb.button(text="💰 Umumiy narx", callback_data=kbs.JobCb(action="price", job_id=job.id))
    kb.button(text="🧵 Modellar narxi", callback_data=kbs.JobPriceCb(action="list", job_id=job.id))
    kb.button(
        text=f"{'✅' if job.model_free else '⬜'} ✂️ Modelsiz ham bo'ladi",
        callback_data=kbs.JobCb(action="modelfree", job_id=job.id),
    )
    kb.button(text="✏️ Nomi", callback_data=kbs.JobCb(action="rename", job_id=job.id))
    kb.button(text="🗑 O'chirish", callback_data=kbs.JobCb(action="del", job_id=job.id))
    kb.button(text="↩️ Kasblar", callback_data=kbs.JobCb(action="list"))
    kb.adjust(2, 1, 2, 1)
    price = f"<b>{_job_price(job)}</b>" if job.price is not None else "yozilmagan"
    if priced:
        shown = ", ".join(f"{escape(m.name)} {fmt_money(row.price, row.currency)}" for m, row in priced[:JOB_CARD_MODELS])
        more = f" va yana {len(priced) - JOB_CARD_MODELS} ta" if len(priced) > JOB_CARD_MODELS else ""
        models_line = f"🧵 Modellar bo'yicha: {shown}{more}"
    else:
        models_line = "🧵 Modellar bo'yicha: qo'yilmagan"
    if not job.model_free:
        mode = "Har bir ishda model so'raladi (variantlar chiqadi), narx — modelning shu kasb narxi."
    elif priced:
        mode = (
            f"<i>«10 ta {escape(jobs_mod.price_example(job.name).split()[0])} qildim»</i> deyilsa — modellar narxi "
            "bilan variant bo'lib chiqadi, tanlangani o'z narxida hisoblanadi; «✂️ Modelsiz» — umumiy narx bilan."
        )
    else:
        mode = "Model so'ralmaydi: <i>«50 ta ish qildim»</i> deyilsa — umumiy narx bilan darhol hisoblanadi."
    text = (
        f"🏷 <b>{escape(job.name)}</b>" + (f" · {n} ishchi" if n else "") + "\n\n"
        f"💰 Umumiy narx (bir dona, hamma modellar uchun): {price}\n{models_line}\n\n{mode}\n\n"
        "Narx tartibi: modelning shu kasb narxi, u bo'lmasa — umumiy narx. Tikuvchi va kroychining narxi "
        "alohida: har bir kasbga o'z narxi qo'yiladi."
    )
    return text, kb.as_markup()


async def _job_prices_card(session: AsyncSession, job, target: int | None) -> tuple[str, InlineKeyboardMarkup]:
    """Kasbning modellar bo'yicha narxi: modelni bosib yoki «Loafer 1500, Sapog 2000» deb yozib qo'yiladi."""
    items = await _job_model_prices(session, job)
    shown = items[:JOB_PRICE_MODELS]
    kb = InlineKeyboardBuilder()
    rows = []
    for m, row in shown:
        value = f"<b>{fmt_money(row.price, row.currency)}</b>" if row is not None else "—"
        rows.append(f"• {escape(m.name)} — {value}")
        mark = "✏️ " if target == m.id else ""
        kb.button(text=f"{mark}{m.name}", callback_data=kbs.JobPriceCb(action="model", job_id=job.id, model_id=m.id))
    kb.button(text=f"↩️ {job.name}", callback_data=kbs.JobCb(action="open", job_id=job.id))
    kb.adjust(*_rows(len(shown), 2), 1)
    fallback = f"umumiy narx ({_job_price(job)})" if job.price is not None else "modelning umumiy narxi"
    text = f"🏷 <b>{escape(job.name)}</b> — modellar bo'yicha narx (bir dona)\n\n"
    text += ("\n".join(rows) if rows else "Hali model yo'q — «🧵 Modellar»da qo'shing.")
    if len(items) > len(shown):
        text += f"\n… yana {len(items) - len(shown)} ta model — ilovada"
    text += f"\n\n«—» — {fallback} olinadi."
    if target is not None:
        name = next((m.name for m, _ in shown if m.id == target), "")
        text += (
            f"\n\n✏️ <b>{escape(name)}</b> — {escape(job.name)} uchun bir donasi qancha? Yozing, masalan: "
            "<i>1500</i>. «0» — narxni olib tashlash."
        )
    else:
        text += (
            "\n\nModelni bosing yoki bir yo'la yozing: <i>«Loafer 1500, Sapog 2000»</i>. Ovoz bilan ham bo'ladi: "
            f"<i>«Loafer {escape(jobs_mod.price_example(job.name).split()[0])} 1500dan»</i>."
        )
    return text, kb.as_markup()


@router.callback_query(kbs.JobPriceCb.filter())
async def job_prices(
    call: CallbackQuery, callback_data: kbs.JobPriceCb, state: FSMContext, session: AsyncSession, user: User
) -> None:
    job = await repo.get_job(session, user.id, callback_data.job_id)
    if job is None:
        await call.answer("Kasb topilmadi.", show_alert=True)
        return
    target = None
    if callback_data.action == "model":
        model = await repo.get_model(session, user.id, callback_data.model_id)
        if model is None:
            await call.answer("Model topilmadi.", show_alert=True)
            return
        target = model.id
    await state.set_state(AdminSG.job_model_price)
    await state.update_data(job_id=job.id, model_id=target)
    text, kb = await _job_prices_card(session, job, target)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.message(AdminSG.job_model_price, F.text, ~F.text.in_(kbs.MENU_TEXTS))
async def job_model_price_value(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    job = await repo.get_job(session, user.id, data.get("job_id"))
    if job is None:
        await state.clear()
        return
    currency = (await repo.enabled_currencies(session, user.id))[0]
    models = {m.id: m for m in await repo.list_models(session, user.id)}
    target = models.get(data.get("model_id"))
    aliases = await repo.get_aliases(session, user.id, "model")
    # Avval «Loafer 1500» (model nomi bilan), bo'lmasa — faqat son: tanlangan model narxi
    items, error = jobs_mod.parse_model_prices(
        message.text, [(m.id, m.name) for m in models.values()], currency, aliases
    )
    if error and target is not None and (parsed := parse_price(message.text, currency)) is not None:
        items, error = [(target.id, parsed[0] if parsed[0] > 0 else None, parsed[1])], None
    if error:
        await message.answer(f"🤔 {escape(error)}")
        return
    done = []
    for model_id, value, cur in items:
        model = models[model_id]
        await repo.set_model_price(session, model, job.id, value, cur)
        name = f"{escape(model.name)} — {escape(job.name)}"
        done.append(f"✅ {name}: {fmt_money(value, cur)}" if value is not None else f"🗑 {name}: narx olib tashlandi")
    await state.update_data(model_id=None)
    text, kb = await _job_prices_card(session, job, None)
    await message.answer("\n".join(done) + "\n\n" + text, reply_markup=kb)


@router.callback_query(kbs.JobCb.filter(F.action == "list"))
async def jobs_list(call: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    text, kb = await _jobs_view(session, user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(kbs.JobCb.filter(F.action == "open"))
async def job_open(
    call: CallbackQuery, callback_data: kbs.JobCb, state: FSMContext, session: AsyncSession, user: User
) -> None:
    await state.clear()
    job = await repo.get_job(session, user.id, callback_data.job_id)
    if job is None:
        await call.answer("Kasb topilmadi.", show_alert=True)
        return
    text, kb = await _job_card(session, job)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(kbs.JobCb.filter(F.action == "modelfree"))
async def job_model_free(call: CallbackQuery, callback_data: kbs.JobCb, session: AsyncSession, user: User) -> None:
    job = await repo.get_job(session, user.id, callback_data.job_id)
    if job is None:
        await call.answer("Kasb topilmadi.", show_alert=True)
        return
    job.model_free = not job.model_free
    await session.flush()
    text, kb = await _job_card(session, job)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer("Modelsiz ham bo'ladi" if job.model_free else "Har doim model so'raladi")


@router.callback_query(kbs.JobCb.filter(F.action == "price"))
async def job_ask_price(
    call: CallbackQuery, callback_data: kbs.JobCb, state: FSMContext, session: AsyncSession, user: User
) -> None:
    job = await repo.get_job(session, user.id, callback_data.job_id)
    if job is None:
        await call.answer("Kasb topilmadi.", show_alert=True)
        return
    await state.set_state(AdminSG.job_price)
    await state.update_data(job_id=job.id)
    await call.message.answer(
        f"💰 <b>{escape(job.name)}</b> — bir dona ish narxi qancha? Yozing, masalan: <i>2000</i> yoki <i>2 ming</i>. "
        "«0» — narxni olib tashlash."
    )
    await call.answer()


@router.message(AdminSG.job_price, F.text, ~F.text.in_(kbs.MENU_TEXTS))
async def job_price_value(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    job = await repo.get_job(session, user.id, data.get("job_id"))
    if job is None:
        await state.clear()
        return
    currency = (await repo.enabled_currencies(session, user.id))[0]
    parsed = parse_price(message.text, currency)
    if parsed is None:
        await message.answer("🤔 Narxni tushunmadim. Masalan: <i>2000</i> yoki <i>2 ming</i>")
        return
    value, cur = parsed
    await repo.set_job_price(session, job, value if value > 0 else None, cur)
    await state.clear()
    text, kb = await _job_card(session, job)
    done = f"✅ {escape(job.name)}: {fmt_money(value, cur)}" if value > 0 else f"🗑 {escape(job.name)}: narx olib tashlandi"
    await message.answer(f"{done}\n\n{text}", reply_markup=kb)


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
    elif data.get("job_id"):
        text, kb = await _job_card(session, job)
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
    pay_approval = await repo.require_pay_approval(session, owner_id)
    auto_save = await repo.auto_save(session, owner_id)
    auto_images = await repo.auto_save_images(session, owner_id)
    enabled = await repo.enabled_currencies(session, owner_id)
    remind = await repo.reminder_hour(session, owner_id)
    kb = InlineKeyboardBuilder()
    kb.button(
        text=f"{'✅' if approval else '⬜'} Ishchi yozuvlarini tasdiqlash",
        callback_data=kbs.SettingsCb(action="approval"),
    )
    kb.button(
        text=f"{'✅' if pay_approval else '⬜'} Pul yozuvlarini tasdiqlash",
        callback_data=kbs.SettingsCb(action="payapproval"),
    )
    kb.button(
        text=f"{'✅' if auto_save else '⬜'} Ovoz va matnni darhol saqlash",
        callback_data=kbs.SettingsCb(action="autosave"),
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
    kb.adjust(1, 1, 1, 1, 3, 3, 1 + len(repo.REMINDER_HOURS))
    images_mode = (
        "tasdiqlamasdan darhol hisobga qo'shiladi"
        if auto_images
        else "o'qilgani ko'rsatiladi, «✅ Tasdiqlash» bosilgach hisobga qo'shiladi"
    )
    text = (
        "⚙️ <b>Sozlamalar</b>\n\n"
        f"• Ishchi yozuvlari: {'egasi tasdiqlagandan keyin' if approval else 'darhol'} hisobotga tushadi\n"
        f"• 💵 Ishchi yozgan pul («oldim», «qaytardim»): {'egasi tasdiqlagandan keyin' if pay_approval else 'darhol'} "
        f"hisob-kitobga kiradi; tasdiqlanmagani bo'lsa — har kuni {PAY_REMINDER_HOUR}:00 da eslatiladi\n"
        "• ⚡ Ovoz yoki matn to'liq tushunilsa (ishchi, model, mijoz topildi, summa aytildi): "
        + ("«To'g'rimi?» so'ralmay darhol saqlanadi, ikkinchi tomonga xabar boradi" if auto_save
           else "«To'g'rimi?» deb so'raladi, «✅ Saqlash» bosilgach saqlanadi")
        + "\n"
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
    elif callback_data.action == "payapproval":
        current = await repo.require_pay_approval(session, user.id)
        await repo.set_setting(session, user.id, "require_pay_approval", "0" if current else "1")
    elif callback_data.action == "autosave":
        current = await repo.auto_save(session, user.id)
        await repo.set_setting(session, user.id, "auto_save", "0" if current else "1")
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
