"""Saqlangan yozuvlar: ko'rish, tahrirlash, o'chirish, tasdiqlash va hisobot."""
import logging
from collections import defaultdict
from decimal import Decimal
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import drafts
from bot import keyboards as kbs
from bot.db import repo
from bot.db.models import EntryStatus, User, WorkEntry
from bot.middlewares import IsOwner, Registered
from bot.services.utils import fmt_date, fmt_money, fmt_totals, period_range

log = logging.getLogger(__name__)

router = Router(name="entries")
router.message.filter(Registered())
router.callback_query.filter(Registered())

STATUS_LABELS = {
    EntryStatus.PENDING: "⏳ tasdiq kutmoqda",
    EntryStatus.APPROVED: "✅ tasdiqlangan",
    EntryStatus.REJECTED: "🚫 rad etilgan",
}


def entry_text(entry: WorkEntry) -> str:
    text = (
        f"<b>#{entry.id}</b> · {fmt_date(entry.work_date)}\n"
        f"👷 {escape(entry.worker.name)}\n"
        f"🧵 {escape(entry.model.name)} — <b>{entry.quantity} ta</b>"
    )
    if entry.unit_price is not None and entry.price_currency:
        text += f"\n💰 {fmt_money(entry.unit_price * entry.quantity, entry.price_currency)}"
    text += f"\n{STATUS_LABELS.get(entry.status, entry.status)}"
    return text


async def notify_owner(
    bot: Bot, session: AsyncSession, owner_id: int | None, text: str, entry: WorkEntry | None = None
) -> None:
    """Kabinet egasiga xabar. Yetmasa (botni bloklagan) — faqat logga yoziladi."""
    owner = await session.get(User, owner_id) if owner_id else None
    if owner is None or owner.tg_id is None:
        return
    markup = kbs.entry_kb(entry, owner) if entry is not None else None
    try:
        await bot.send_message(owner.tg_id, text, reply_markup=markup)
    except TelegramAPIError:
        log.warning("Egaga xabar yuborilmadi (chat %s)", owner.tg_id, exc_info=True)


async def notify_owner_pending(bot: Bot, session: AsyncSession, entry: WorkEntry) -> None:
    text = "🔔 <b>Yangi yozuv tasdiq kutmoqda</b>\n\n" + entry_text(entry)
    await notify_owner(bot, session, entry.owner_id, text, entry)


def _can_touch(user: User, entry: WorkEntry) -> bool:
    return user.is_owner or entry.worker_id == user.id


async def _get_entry(call: CallbackQuery, session: AsyncSession, user: User, entry_id: int) -> WorkEntry | None:
    entry = await repo.get_work_entry(session, user.account_id, entry_id)
    if entry is None or not _can_touch(user, entry):
        await call.answer("Yozuv topilmadi yoki o'chirilgan.", show_alert=True)
        return None
    return entry


# --- Ro'yxatlar -------------------------------------------------------------

@router.message(F.text == kbs.BTN_RECENT)
@router.message(Command("oxirgi"))
async def recent(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    entries = await repo.recent_work_entries(session, user.account_id, worker_id=None if user.is_owner else user.id)
    if not entries:
        await message.answer("Hali yozuvlar yo'q.")
        return
    await message.answer(f"🕘 Oxirgi {len(entries)} ta yozuv:")
    for i, entry in enumerate(reversed(entries)):
        last = i == len(entries) - 1
        await message.answer(entry_text(entry), reply_markup=kbs.entry_kb(entry, user, app=last))


@router.message(F.text == kbs.BTN_PENDING, IsOwner())
async def pending(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    entries = await repo.pending_work_entries(session, user.id)
    if not entries:
        await message.answer("✅ Tasdiq kutayotgan yozuv yo'q.")
        return
    await message.answer(f"⏳ Tasdiq kutayotganlar: {len(entries)} ta")
    shown = entries[:30]
    for i, entry in enumerate(shown):
        await message.answer(entry_text(entry), reply_markup=kbs.entry_kb(entry, user, app=i == len(shown) - 1))


# --- Yozuv tugmalari --------------------------------------------------------

@router.callback_query(kbs.EntryCb.filter(F.action.in_({"approve", "reject"})), IsOwner())
async def on_review(
    call: CallbackQuery, callback_data: kbs.EntryCb, session: AsyncSession, user: User
) -> None:
    entry = await _get_entry(call, session, user, callback_data.entry_id)
    if entry is None:
        return
    approve = callback_data.action == "approve"
    await repo.review_work_entry(session, entry, user, approve)
    await call.message.edit_text(entry_text(entry), reply_markup=kbs.entry_kb(entry, user))
    await call.answer("Tasdiqlandi" if approve else "Rad etildi")

    if entry.worker.tg_id and entry.worker_id != user.id:
        verdict = "✅ tasdiqlandi" if approve else "🚫 rad etildi"
        await call.bot.send_message(entry.worker.tg_id, f"Yozuvingiz {verdict}:\n\n" + entry_text(entry))


@router.callback_query(kbs.EntryCb.filter(F.action == "edit"))
async def on_edit(
    call: CallbackQuery, callback_data: kbs.EntryCb, state: FSMContext, session: AsyncSession, user: User
) -> None:
    from bot.handlers.input import DraftSG  # aylanma importdan qochish uchun

    entry = await _get_entry(call, session, user, callback_data.entry_id)
    if entry is None:
        return
    draft = drafts.from_entry(entry)
    await state.set_state(DraftSG.active)
    await state.update_data(draft=draft)
    text_out, kb = await drafts.present(session, draft, user)
    await call.message.answer(
        text_out + "\n\n✏️ O'zgartirishni ovoz yoki matn bilan ayting, masalan: <i>«soni 45 ta»</i>.",
        reply_markup=kb,
    )
    await call.answer()


@router.callback_query(kbs.EntryCb.filter(F.action == "del"))
async def on_delete(call: CallbackQuery, callback_data: kbs.EntryCb, session: AsyncSession, user: User) -> None:
    entry = await _get_entry(call, session, user, callback_data.entry_id)
    if entry is None:
        return
    await call.message.edit_reply_markup(reply_markup=kbs.confirm_delete_kb(entry.id))
    await call.answer("Rostdan o'chirilsinmi?")


@router.callback_query(kbs.EntryCb.filter(F.action == "del_no"))
async def on_delete_no(call: CallbackQuery, callback_data: kbs.EntryCb, session: AsyncSession, user: User) -> None:
    entry = await _get_entry(call, session, user, callback_data.entry_id)
    if entry is None:
        return
    await call.message.edit_reply_markup(reply_markup=kbs.entry_kb(entry, user))
    await call.answer()


@router.callback_query(kbs.EntryCb.filter(F.action == "del_yes"))
async def on_delete_yes(call: CallbackQuery, callback_data: kbs.EntryCb, session: AsyncSession, user: User) -> None:
    entry = await _get_entry(call, session, user, callback_data.entry_id)
    if entry is None:
        return
    text = entry_text(entry)
    await repo.soft_delete(session, entry)
    await call.message.edit_text(f"<s>{text}</s>\n\n🗑 O'chirildi")
    await call.answer("O'chirildi")

    # Ishchi tasdiqlangan yozuvni o'chirsa — egalarga xabar
    if not user.is_owner and entry.status == EntryStatus.APPROVED:
        await notify_owner(
            call.bot, session, user.owner_id, f"🗑 {escape(user.name)} tasdiqlangan yozuvni o'chirdi:\n\n{text}"
        )


# --- Hisobot ----------------------------------------------------------------

PERIOD_LABELS = {"day": "Bugun", "week": "Shu hafta", "month": "Shu oy"}


@router.message(F.text == kbs.BTN_REPORT)
@router.message(Command("hisobot"))
async def report_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("📊 Qaysi davr uchun?", reply_markup=kbs.report_kb())


@router.callback_query(kbs.ReportCb.filter())
async def on_report(call: CallbackQuery, callback_data: kbs.ReportCb, session: AsyncSession, user: User) -> None:
    period = callback_data.period
    start, end = period_range(period)
    worker_id = None if user.is_owner else user.id
    rows = await repo.work_report(session, user.account_id, start, end, worker_id=worker_id)
    title = (
        f"📊 <b>{PERIOD_LABELS[period]}</b> "
        f"({start.strftime('%d.%m')}{'' if start == end else ' – ' + end.strftime('%d.%m')})"
    )

    if not rows:
        body = "Bu davrda tasdiqlangan ish yo'q."
    elif user.is_owner:
        body = _owner_report(rows)
    else:
        body = _worker_report(rows)

    pending_count = await repo.count_pending(session, user.account_id, worker_id=worker_id)
    if pending_count:
        body += f"\n\n⏳ Tasdiq kutayotgan yozuvlar: {pending_count} ta (hisobotga kirmagan)"

    await call.message.edit_text(f"{title}\n\n{body}", reply_markup=kbs.report_kb())
    await call.answer()


def _add(totals: dict[str, Decimal], row: repo.WorkReportRow) -> None:
    if row.pay is not None and row.currency:
        totals[row.currency] = totals.get(row.currency, Decimal(0)) + row.pay


def _worker_report(rows: list[repo.WorkReportRow]) -> str:
    totals: dict[str, Decimal] = {}
    lines = []
    for r in rows:
        line = f"• {escape(r.model_name)} — {r.quantity} ta"
        if r.pay is not None and r.currency:
            line += f" — {fmt_money(r.pay, r.currency)}"
        lines.append(line)
        _add(totals, r)
    lines.append(f"\nJami: <b>{sum(r.quantity for r in rows)} ta</b>")
    if totals:
        lines.append(f"Ish haqi: <b>{fmt_totals(totals)}</b>")
    return "\n".join(lines)


def _owner_report(rows: list[repo.WorkReportRow]) -> str:
    by_worker: dict[int, list[repo.WorkReportRow]] = defaultdict(list)
    for r in rows:
        by_worker[r.worker_id].append(r)

    blocks = []
    for worker_rows in by_worker.values():
        totals: dict[str, Decimal] = {}
        for r in worker_rows:
            _add(totals, r)
        qty = sum(r.quantity for r in worker_rows)
        models = ", ".join(f"{escape(r.model_name)}: {r.quantity}" for r in worker_rows)
        block = f"👷 <b>{escape(worker_rows[0].worker_name)}</b> — {qty} ta\n   {models}"
        if totals:
            block += f"\n   💰 {fmt_totals(totals)}"
        blocks.append(block)

    by_model: dict[str, int] = defaultdict(int)
    grand: dict[str, Decimal] = {}
    for r in rows:
        by_model[r.model_name] += r.quantity
        _add(grand, r)
    model_lines = "\n".join(f"• {escape(name)} — {qty} ta" for name, qty in sorted(by_model.items()))

    text = "\n\n".join(blocks)
    text += f"\n\n🧵 <b>Modellar bo'yicha</b>\n{model_lines}"
    text += f"\n\nJami: <b>{sum(by_model.values())} ta</b>"
    if grand:
        text += f"\nIsh haqi fondi: <b>{fmt_totals(grand)}</b>"
    return text
