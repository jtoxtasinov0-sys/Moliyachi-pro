"""Saqlangan yozuvlar: ko'rish, tahrirlash, o'chirish, tasdiqlash, hisobot va ishchi hisob-kitobi."""
import logging
from collections import defaultdict
from decimal import Decimal
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message, ReplyKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from bot import approvals, drafts, pdf_report
from bot import keyboards as kbs
from bot.db import repo
from bot.db.models import Client, EntryStatus, PayKind, RequestStatus, Supplier, User, WorkEntry, WorkerPayment
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


# Model aytilmagan ish (kroy, brsovka) hisobotda shu nom bilan
NO_MODEL = "Modelsiz"


def entry_text(entry: WorkEntry) -> str:
    job = f" · {escape(entry.job.name)}" if entry.job else ""
    model = f"{escape(entry.model.name)} — " if entry.model else ""
    text = (
        f"<b>#{entry.id}</b> · {fmt_date(entry.work_date)}\n"
        f"👷 {escape(entry.worker.name)}{job}\n"
        f"🧵 {model}<b>{entry.quantity} ta</b>"
    )
    if entry.unit_price is not None and entry.price_currency:
        text += f"\n💰 {fmt_money(entry.unit_price * entry.quantity, entry.price_currency)}"
    text += f"\n{STATUS_LABELS.get(entry.status, entry.status)}"
    return text


def pay_text(entry: WorkerPayment, viewer: User) -> str:
    """Ishchi bilan pul yozuvi: egasiga — ishchi ismi bilan, ishchining o'ziga — «Oldingiz»."""
    amount = f"<b>{fmt_money(abs(entry.amount), entry.currency)}</b>"
    who = entry.worker.name if viewer.is_owner else None
    line = drafts.pay_line(entry.kind, amount, who, entry.amount < 0, not entry.amount, viewer.is_owner)
    text = f"<b>#{entry.id}</b> · {fmt_date(entry.entry_date)}\n{line}"
    if entry.note:
        text += f"\n<i>{escape(entry.note)}</i>"
    if entry.status != EntryStatus.APPROVED:
        text += f"\n{STATUS_LABELS.get(entry.status, entry.status)}"
    return text


async def pay_summary(session: AsyncSession, payments: list[WorkerPayment], viewer: User) -> str:
    """Bitta ishchining pul yozuvlari: har bir kuni qancha olgani («📅 20.10.2026 (bugun) — oldingiz: 50 000 so'm»)
    va hozirgi qoldiq («💰 Qoldi — olishingiz kerak: 330 000 so'm»). Egasiga — ishchi ismi bilan.
    Tasdiq kutayotgan pul qoldiqqa kirmaydi — tasdiqlansa qancha qolishi alohida qatorda."""
    first = payments[0]
    name = first.worker.name if viewer.is_owner else None
    days = sorted({p.entry_date for p in payments if p.kind in (PayKind.PAY, PayKind.RETURN)})
    totals = await repo.worker_day_payments(session, first.owner_id, first.worker_id, days)
    rows = [drafts.day_pay_line(day, *totals[day], name) for day in days if any(totals[day])]
    balance = (await repo.worker_balance(session, first.owner_id, first.worker_id)).balance
    rows.append(drafts.balance_text(balance, name, left=bool(rows)))
    waiting = [p for p in payments if p.status == EntryStatus.PENDING and p.kind in (PayKind.PAY, PayKind.RETURN)]
    if waiting:
        who = "Tasdiqlasangiz" if viewer.is_owner else "Boshlig'ingiz tasdiqlasa"
        rows.append(f"⏳ {who} — qoldi: <b>{drafts.signed_hint(repo.with_pending(balance, waiting))}</b>")
    return "\n".join(rows)


async def notify_user(
    bot: Bot, chat_id: int, text: str, markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None
) -> bool:
    """Xabar yuborish. Yetmasa (botni bloklagan) — faqat logga yoziladi va False."""
    try:
        await bot.send_message(chat_id, text, reply_markup=markup)
    except TelegramAPIError:
        log.warning("Xabar yuborilmadi (chat %s)", chat_id, exc_info=True)
        return False
    return True


async def notify_owner(
    bot: Bot,
    session: AsyncSession,
    owner_id: int | None,
    text: str,
    entry: WorkEntry | None = None,
    markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Kabinet egasiga xabar. Yetmasa (botni bloklagan) — faqat logga yoziladi."""
    owner = await session.get(User, owner_id) if owner_id else None
    if owner is None or owner.tg_id is None:
        return
    if entry is not None:
        markup = kbs.entry_kb(entry, owner)
    await notify_user(bot, owner.tg_id, text, markup)


async def notify_owner_linked(
    bot: Bot,
    session: AsyncSession,
    item: User | Client | Supplier,
    kind: str,
    previous: int | None,
    tg_id: int,
    first_text: str,
) -> None:
    """Doimiy havola bosilganda egaga xabar: birinchi marta ulansa — first_text, boshqa Telegram akkauntdan
    kirsa — ogohlantirish va «🔒 Bu u emas» tugmasi, o'sha odamning o'zi qayta kirsa — xabar yo'q.
    kind — havola prefiksi: inv, sup, cli."""
    if previous == tg_id:
        return
    markup = None
    if previous is not None:
        first_text = (
            f"🔁 {escape(item.name)} boshqa Telegram akkauntdan qayta kirdi — eski akkauntiga endi xabar bormaydi.\n"
            "Agar bu u bo'lmasa — «🔒 Bu u emas» ni bosing: bu akkaunt uziladi va havola almashadi."
        )
        markup = kbs.not_them_kb(kind, item.id, tg_id)
    await notify_owner(bot, session, item.owner_id, first_text, markup=markup)


STALE_LINK = (
    "Bu havola eski — endi ishlamaydi. Havolani yuborgan odamdan ilovada ismingizni bosib, kirish havolasini qayta "
    "yuborishini so'rang (yangi havola doimiy, eskirmaydi)."
)


async def refuse_link(
    message: Message, session: AsyncSession, model: type[User] | type[Client] | type[Supplier], token: str
) -> None:
    """Havola bilan kirib bo'lmadi. Havola egasi o'chirilgan (⛔) bo'lsa — unga ham, egaga ham nima qilish kerakligi
    aytiladi; topilmasa — bu doimiy havoladan oldingi, bekor qilingan havola."""
    item = await repo.by_invite_token(session, model, token)
    if item is None or item.is_active:
        await message.answer(STALE_LINK)
        return
    await message.answer(
        "⛔ Siz ro'yxatda o'chirilgansiz. Havolani yuborgan odamga ayting — sizni qayta faollashtirsa, shu havola "
        "bilan kirasiz."
    )
    await notify_owner(
        message.bot, session, item.owner_id,
        f"⛔ <b>{escape(item.name)}</b> havola bilan botga kirmoqchi bo'ldi, lekin u o'chirilgan. Kirishi uchun ismini "
        "bosib «✅ Qayta faollashtirish» ni bosing — keyin o'sha havola ishlaydi.",
    )


async def notify_owner_pending(bot: Bot, session: AsyncSession, entry: WorkEntry) -> None:
    text = "🔔 <b>Yangi yozuv tasdiq kutmoqda</b>\n\n" + entry_text(entry)
    await notify_owner(bot, session, entry.owner_id, text, entry)


def _can_touch(user: User, entry: WorkEntry) -> bool:
    return user.manages or entry.worker_id == user.id


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
    entries = await repo.recent_work_entries(session, user.account_id, worker_id=None if user.manages else user.id)
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
    payments = await repo.list_payments(session, user.id, status=EntryStatus.PENDING, limit=30)
    requests = await repo.list_requests(session, user.id, status=RequestStatus.PENDING, limit=30)
    if not entries and not payments and not requests:
        await message.answer("✅ Tasdiq kutayotgan yozuv yo'q.")
        return
    await message.answer(f"⏳ Tasdiq kutayotganlar: {len(entries) + len(payments) + len(requests)} ta")
    cards = [(entry_text(e), kbs.entry_kb(e, user)) for e in entries[:30]]
    cards += [(pay_text(p, user), kbs.pay_entry_kb(p, user)) for p in reversed(payments)]
    # Ish boshqaruvchining so'rovlari
    cards += [(approvals.request_text(r, True), kbs.request_kb(r, user)) for r in reversed(requests)]
    for text, kb in cards:
        await message.answer(text, reply_markup=kb)


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
    if user.is_manager:
        await _ask_owner(call, session, user, text, "delete_entry", entry.id)
        return
    await repo.soft_delete(session, entry)
    await call.message.edit_text(f"<s>{text}</s>\n\n🗑 O'chirildi")
    await call.answer("O'chirildi")

    # Ishchi tasdiqlangan yozuvni o'chirsa — egalarga xabar
    if not user.is_owner and entry.status == EntryStatus.APPROVED:
        await notify_owner(
            call.bot, session, user.owner_id, f"🗑 {escape(user.name)} tasdiqlangan yozuvni o'chirdi:\n\n{text}"
        )


async def _ask_owner(call: CallbackQuery, session: AsyncSession, user: User, text: str, op: str, entry_id: int) -> None:
    """Ish boshqaruvchi o'chirmoqchi: darhol o'chmaydi, egasining tasdig'iga boradi."""
    from bot.web.api import ApiError  # aylanma importdan qochish uchun

    try:
        req = await approvals.ask(session, call.bot, user, op, {"id": str(entry_id)})
    except ApiError as e:
        await call.answer(e.message, show_alert=True)
        return
    await call.message.edit_text(
        f"{text}\n\n📨 O'chirish boshliq tasdig'iga yuborildi — tasdiqlasa o'chadi.", reply_markup=kbs.request_kb(req, user)
    )
    await call.answer("Boshliq tasdig'iga yuborildi")


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
    worker_id = None if user.manages else user.id
    rows = await repo.work_report(session, user.account_id, start, end, worker_id=worker_id)
    title = (
        f"📊 <b>{PERIOD_LABELS[period]}</b> "
        f"({start.strftime('%d.%m')}{'' if start == end else ' – ' + end.strftime('%d.%m')})"
    )

    # Boshqaruvchiga — butun ishxona, lekin ishchilarning qoldig'i (haqqi) emas: faqat o'ziniki
    balances = await repo.worker_balances(session, user.account_id, None if user.is_owner else [user.id])
    if not rows:
        body = "Bu davrda tasdiqlangan ish yo'q."
    elif user.manages:
        body = _owner_report(rows, balances if user.is_owner else {})
    else:
        body = _worker_report(rows)
    if not user.is_owner:
        mine = balances.get(user.id)
        body += "\n\n🧾 <b>Hisob-kitob</b>\n" + drafts.balance_text(mine.balance if mine else {})

    pending_count = await repo.count_pending(session, user.account_id, worker_id=worker_id)
    if pending_count:
        body += f"\n\n⏳ Tasdiq kutayotgan yozuvlar: {pending_count} ta (hisobotga kirmagan)"

    await call.message.edit_text(f"{title}\n\n{body}", reply_markup=kbs.report_kb())
    await call.answer()


def _pdf_prompt(user: User) -> str:
    inside = (
        "ishxona (ishchilar, modellar), daromad va xarajatlar, mijozlar va qarzlar"
        if user.is_owner
        else "topshirgan ishlaringiz va ish haqingiz"
    )
    return (
        "📄 <b>PDF hisobot</b> — qaysi davr uchun?\n\n"
        f"Ichida: {inside}. Fayl telefoningizga saqlanadi — ilova yopilsa ham, "
        "internet bo'lmasa ham ochiladi."
    )


@router.message(Command("pdf"))
async def pdf_menu(message: Message, state: FSMContext, user: User) -> None:
    await state.clear()
    await message.answer(_pdf_prompt(user), reply_markup=kbs.pdf_kb())


@router.callback_query(kbs.PdfCb.filter())
async def on_pdf(call: CallbackQuery, callback_data: kbs.PdfCb, session: AsyncSession, user: User) -> None:
    period = callback_data.period
    if period not in pdf_report.PERIODS:
        await call.message.answer(_pdf_prompt(user), reply_markup=kbs.pdf_kb())
        await call.answer()
        return
    await call.answer("📄 Tayyorlanmoqda…")
    await call.bot.send_chat_action(call.message.chat.id, "upload_document")
    pdf, name = await pdf_report.build(session, user, period)
    await call.message.answer_document(BufferedInputFile(pdf, filename=name), caption=pdf_report.caption(period))


def _add(totals: dict[str, Decimal], row: repo.WorkReportRow) -> None:
    if row.pay is not None and row.currency:
        totals[row.currency] = totals.get(row.currency, Decimal(0)) + row.pay


def _model_label(r: repo.WorkReportRow, with_job: bool) -> str:
    """«A-12» yoki «A-12 (Brsovkachi)»; model aytilmagan ishda — kasbi («Kroychi»)."""
    if r.model_name is None:
        return escape(r.job_name or NO_MODEL)
    name = escape(r.model_name)
    return f"{name} ({escape(r.job_name)})" if with_job and r.job_name else name


def _worker_report(rows: list[repo.WorkReportRow]) -> str:
    totals: dict[str, Decimal] = {}
    lines = []
    # Ishchi bir nechta kasbda ishlagan bo'lsa — har bir qatorda kasbi
    with_job = len({r.job_name for r in rows}) > 1
    for r in rows:
        line = f"• {_model_label(r, with_job)} — {r.quantity} ta"
        if r.pay is not None and r.currency:
            line += f" — {fmt_money(r.pay, r.currency)}"
        lines.append(line)
        _add(totals, r)
    lines.append(f"\nJami: <b>{sum(r.quantity for r in rows)} ta</b>")
    if totals:
        lines.append(f"Ish haqi: <b>{fmt_totals(totals)}</b>")
    return "\n".join(lines)


def tracked(b: repo.WorkerBalance | None) -> bool:
    """Ishchi bilan pul yoki hisob holati yozilganmi — yozilmagan bo'lsa qoldiq hisobotda ko'rsatilmaydi."""
    return b is not None and bool(b.since or b.paid or b.returned)


def _owner_report(rows: list[repo.WorkReportRow], balances: dict[int, repo.WorkerBalance]) -> str:
    by_worker: dict[int, list[repo.WorkReportRow]] = defaultdict(list)
    for r in rows:
        by_worker[r.worker_id].append(r)

    blocks = []
    for worker_rows in by_worker.values():
        totals: dict[str, Decimal] = {}
        for r in worker_rows:
            _add(totals, r)
        qty = sum(r.quantity for r in worker_rows)
        # Ishchi bitta kasbda ishlagan bo'lsa — kasbi ismi yonida, aks holda har bir model yonida
        jobs = {r.job_name for r in worker_rows}
        one_job = jobs.pop() if len(jobs) == 1 else None
        who = escape(worker_rows[0].worker_name) + (f" ({escape(one_job)})" if one_job else "")
        models = ", ".join(f"{_model_label(r, one_job is None)}: {r.quantity}" for r in worker_rows)
        block = f"👷 <b>{who}</b> — {qty} ta\n   {models}"
        if totals:
            block += f"\n   💰 {fmt_totals(totals)}"
        b = balances.get(worker_rows[0].worker_id)
        if tracked(b):
            block += f"\n   🧾 Qoldiq: {drafts.fmt_signed(b.balance)}"
        blocks.append(block)

    # Model bo'yicha: kasblar bo'lsa — har bir bosqichda (kroy, brsovka, tikuv...) nechta
    by_model: dict[str, dict[str | None, int]] = defaultdict(lambda: defaultdict(int))
    grand: dict[str, Decimal] = {}
    for r in rows:
        by_model[r.model_name][r.job_name] += r.quantity
        _add(grand, r)
    model_lines = []
    # Model aytilmagan ishlar (kroy, brsovka) — oxirida
    for name, jobs in sorted(by_model.items(), key=lambda kv: (kv[0] is None, kv[0] or "")):
        name = name or NO_MODEL
        if set(jobs) == {None}:
            model_lines.append(f"• {escape(name)} — {jobs[None]} ta")
        else:
            parts = ", ".join(f"{escape(job or 'kasbsiz')}: {qty}" for job, qty in jobs.items())
            model_lines.append(f"• {escape(name)} — {parts}")

    text = "\n\n".join(blocks)
    text += "\n\n🧵 <b>Modellar bo'yicha</b>\n" + "\n".join(model_lines)
    text += f"\n\nJami: <b>{sum(r.quantity for r in rows)} ta</b>"
    if grand:
        text += f"\nIsh haqi fondi: <b>{fmt_totals(grand)}</b>"
    return text


# --- Ishchi bilan pul: tasdiqlash va o'chirish -------------------------------

async def _get_payment(
    call: CallbackQuery, session: AsyncSession, user: User, entry_id: int
) -> WorkerPayment | None:
    entry = await repo.get_payment(session, user.account_id, entry_id)
    if entry is None or not (user.is_owner or entry.worker_id == user.id):
        await call.answer("Yozuv topilmadi yoki o'chirilgan.", show_alert=True)
        return None
    return entry


async def _pay_card(session: AsyncSession, entry: WorkerPayment, viewer: User) -> str:
    return f"{pay_text(entry, viewer)}\n\n{await pay_summary(session, [entry], viewer)}"


@router.callback_query(kbs.PayCb.filter(F.action.in_({"approve", "reject", "lg_ok", "lg_no"})), IsOwner())
async def on_pay_review(call: CallbackQuery, callback_data: kbs.PayCb, session: AsyncSession, user: User) -> None:
    """Pul yozuvini tasdiqlash yoki rad etish: yozuv kartasida yoki hisob-kitob ostidagi tugmada (lg_ — hisob-kitob
    shu xabarda yangilanadi)."""
    entry = await _get_payment(call, session, user, callback_data.entry_id)
    if entry is None:
        return
    from_ledger = callback_data.action.startswith("lg_")
    if from_ledger and entry.status != EntryStatus.PENDING:
        # Eski hisob-kitob xabaridagi tugma: yozuv boshqa joyda allaqachon ko'rib chiqilgan
        await call.answer("Bu yozuv allaqachon ko'rib chiqilgan.", show_alert=True)
        return
    approve = callback_data.action in ("approve", "lg_ok")
    await repo.review_payment(session, entry, user, approve)
    if from_ledger:
        text, kb = await ledger_message(session, user, entry.worker)
        await call.message.edit_text(text, reply_markup=kb)
    else:
        text = await _pay_card(session, entry, user)
        await call.message.edit_text(text, reply_markup=kbs.pay_entry_kb(entry, user))
    await call.answer("Tasdiqlandi" if approve else "Rad etildi")
    worker = entry.worker
    if worker.tg_id and worker.id != user.id:
        verdict = "✅ tasdiqlandi" if approve else "🚫 rad etildi"
        await notify_user(call.bot, worker.tg_id, f"Yozuvingiz {verdict}:\n\n" + await _pay_card(session, entry, worker))


@router.callback_query(kbs.PayCb.filter(F.action.in_({"del", "del_no", "del_yes"})))
async def on_pay_delete(call: CallbackQuery, callback_data: kbs.PayCb, session: AsyncSession, user: User) -> None:
    entry = await _get_payment(call, session, user, callback_data.entry_id)
    if entry is None:
        return
    if not user.is_owner and entry.created_by != user.id:
        await call.answer("Bu yozuvni boshlig'ingiz kiritgan — faqat u o'chira oladi.", show_alert=True)
        return
    if user.is_manager and callback_data.action == "del_yes":
        await _ask_owner(call, session, user, pay_text(entry, user), "delete_payment", entry.id)
        return
    if callback_data.action == "del":
        await call.message.edit_reply_markup(reply_markup=kbs.pay_confirm_delete_kb(entry.id))
        await call.answer("Rostdan o'chirilsinmi?")
        return
    if callback_data.action == "del_no":
        await call.message.edit_reply_markup(reply_markup=kbs.pay_entry_kb(entry, user))
        await call.answer()
        return
    text = pay_text(entry, user)
    await repo.soft_delete(session, entry)
    balance = (await repo.worker_balance(session, entry.owner_id, entry.worker_id)).balance
    name = entry.worker.name if user.is_owner else None
    await call.message.edit_text(f"<s>{text}</s>\n\n🗑 O'chirildi\n\n{drafts.balance_text(balance, name)}")
    await call.answer("O'chirildi")
    if not user.is_owner and entry.status == EntryStatus.APPROVED:
        owner = await session.get(User, user.owner_id)
        if owner is not None:
            await notify_owner(
                call.bot, session, user.owner_id,
                f"🗑 {escape(user.name)} pul yozuvini o'chirdi:\n\n<s>{pay_text(entry, owner)}</s>\n\n"
                + drafts.balance_text(balance, user.name),
            )


# --- Hisob-kitob: ishlar va olingan pul, qoldiq bilan --------------------------

LEDGER_ROWS = 25
LEDGER_BUDGET = 3300


def _ledger_row(r: repo.LedgerRow, owner_view: bool) -> str:
    e = r.entry
    if r.kind == "work":
        job = f" · {escape(e.job.name)}" if e.job else ""
        pay = f" · +{fmt_totals(r.delta)}" if r.delta else " · narx yo'q"
        model = f"{escape(e.model.name)} — " if e.model else ""
        text = f"🧵 {model}{e.quantity} ta{job}{pay}"
    else:
        amount = fmt_money(abs(e.amount), e.currency)
        text = drafts.pay_line(e.kind, amount, None, e.amount < 0, not e.amount, owner_view)
        if e.note:
            text += f" <i>({escape(e.note)})</i>"
    # Tasdiqlanmagan yozuv qoldiqqa kirmaydi — kun oxiridagi qoldiqda yo'q
    return text + ("" if r.counted else " ⏳ <i>tasdiqlanmagan — hisobga kirmagan</i>")


def _ledger_formula(b: repo.WorkerBalance, owner_view: bool) -> str | None:
    """Qoldiq qayerdan chiqdi: «23.10 dagi hisob 1 870 000 so'm + ishlar 560 000 so'm − berildi 1 390 000 so'm
    = 1 040 000 so'm» (hisob holati aytilmagan bo'lsa — «boshidan: ishlar …»)."""
    terms = []
    if b.earned:
        terms.append(("+", f"ishlar {fmt_totals(b.earned)}"))
    if b.paid:
        terms.append(("−", f"{'berildi' if owner_view else 'oldingiz'} {fmt_totals(b.paid)}"))
    if b.returned:
        terms.append(("+", f"qaytdi {fmt_totals(b.returned)}"))
    if not terms:
        return None
    if b.since:
        text = f"{b.since.strftime('%d.%m')} dagi hisob {drafts.fmt_signed(b.start)}"
    else:
        sign, first = terms.pop(0)
        text = "boshidan: " + ("" if sign == "+" else "− ") + first
    text += "".join(f" {sign} {term}" for sign, term in terms)
    return f"{text} = {drafts.fmt_signed(b.balance)}"


def _pending_note(balance: dict[str, Decimal], waiting: list[WorkerPayment], name: str | None) -> str:
    """Tasdiq kutayotgan pul: nega qoldiqdan ayrilmagani va tasdiqlansa qancha qolishi. name — egasiga (ishchi
    ismi), None — ishchining o'ziga."""
    owner_view = name is not None
    paid: dict[str, Decimal] = {}
    returned: dict[str, Decimal] = {}
    for p in waiting:
        part = paid if p.kind == PayKind.PAY else returned
        part[p.currency] = part.get(p.currency, Decimal(0)) + abs(p.amount)
    what = []
    if paid:
        what.append(f"{'berildi' if owner_view else 'oldingiz'} {fmt_totals(paid)}")
    if returned:
        what.append(f"qaytdi {fmt_totals(returned)}")
    after = drafts.signed_hint(repo.with_pending(balance, waiting))
    if owner_view:
        return (
            f"⏳ <b>Tasdiqlanmagan pul:</b> {', '.join(what)} — {escape(name)} o'zi yozgan, siz tasdiqlamaguningizcha "
            f"yuqoridagi qoldiqqa kirmaydi.\n👉 Tasdiqlasangiz qoldi: <b>{after}</b>"
        )
    return (
        f"⏳ <b>Tasdiq kutmoqda:</b> {', '.join(what)} — boshlig'ingiz tasdiqlagach hisobga kiradi.\n"
        f"👉 Tasdiqlasa qoldi: <b>{after}</b>"
    )


async def ledger_message(
    session: AsyncSession, viewer: User, worker: User
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Hisob-kitob va uning tugmalari: egasiga — tasdiq kutayotgan pulni shu yerning o'zida tasdiqlash."""
    waiting = await repo.pending_worker_pays(session, worker.owner_id, worker.id)
    text = await ledger_text(session, viewer, worker, waiting)
    kb = kbs.ledger_kb(waiting) if viewer.is_owner and waiting else kbs.app_kb("ledger")
    return text, kb


async def ledger_text(session: AsyncSession, viewer: User, worker: User, waiting: list[WorkerPayment]) -> str:
    """SMS'dagidek: kunma-kun ishlar va pul, har kun oxirida qoldiq (eskisi tepada). waiting — tasdiq kutayotgan
    pul (qoldiqqa kirmagan), pastda alohida ko'rsatiladi."""
    rows, more = await repo.worker_ledger(session, worker.owner_id, worker.id, limit=LEDGER_ROWS)
    b = await repo.worker_balance(session, worker.owner_id, worker.id)
    owner_view = viewer.is_owner
    title = f"🧾 <b>Hisob-kitob · {escape(worker.name)}</b>" if owner_view else "🧾 <b>Hisob-kitobingiz</b>"
    example = (
        f"«{worker.name}ga 500 ming berdim», «{worker.name} bilan hisob yopildi», «{worker.name}ga 900 ming qarzman»"
        if owner_view
        else "«500 ming oldim», «200 ming qaytardim»"
    )
    if not rows:
        return f"{title}\n\nHali yozuv yo'q.\n\n💡 Ovoz yoki matn bilan ayting: <i>{escape(example)}</i>"

    days: list[tuple[object, list[repo.LedgerRow]]] = []
    for r in reversed(rows):
        if days and days[-1][0] == r.day:
            days[-1][1].append(r)
        else:
            days.append((r.day, [r]))
    blocks: list[str] = []
    used = 0
    for day, items in reversed(days):  # yangisidan boshlab, joy yetguncha
        block = f"📅 <b>{fmt_date(day)}</b>\n" + "\n".join(_ledger_row(r, owner_view) for r in items)
        block += f"\n   <i>= qoldiq {drafts.signed_hint(items[-1].balance)}</i>"
        if blocks and used + len(block) > LEDGER_BUDGET:
            more = True
            break
        blocks.append(block)
        used += len(block)
    blocks.reverse()

    parts = [title]
    if more:
        parts.append("<i>… oldingi yozuvlar — ilovada</i>")
    parts.extend(blocks)
    summary = [drafts.balance_text(b.balance, worker.name if owner_view else None)]
    if formula := _ledger_formula(b, owner_view):
        summary.append(f"<i>({formula})</i>")
    parts.append("\n".join(summary))
    if waiting:
        parts.append(_pending_note(b.balance, waiting, worker.name if owner_view else None))
    parts.append(f"💡 Yozish: <i>{escape(example)}</i>")
    return "\n\n".join(parts)


@router.message(F.text == kbs.BTN_LEDGER)
@router.message(Command("hisob"))
async def ledger(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    if user.is_owner:
        await message.answer("🧾 Ishchi hisob-kitobi — «👷 Ishchilar» bo'limida ishchini bosing.")
        return
    text, kb = await ledger_message(session, user, user)
    await message.answer(text, reply_markup=kb)
