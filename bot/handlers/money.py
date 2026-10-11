"""Egasining shaxsiy hamyoni: xarajat/daromad hisoboti, qarzlar va ularni o'chirish."""
from collections import defaultdict
from decimal import Decimal
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kbs
from bot.db import repo
from bot.db.models import DebtEntry, PersonalEntry, PersonalKind, User
from bot.drafts import debt_line
from bot.middlewares import IsOwner
from bot.services.utils import fmt_date, fmt_money, fmt_totals, period_range

router = Router(name="money")
router.message.filter(IsOwner())
router.callback_query.filter(IsOwner())

PERIOD_LABELS = {"day": "Bugun", "week": "Shu hafta", "month": "Shu oy"}


# --- Matnlar ----------------------------------------------------------------

def personal_text(e: PersonalEntry) -> str:
    icon = "💰" if e.kind == PersonalKind.INCOME else "💸"
    text = f"{icon} <b>{escape(e.category)}</b> — {fmt_money(e.amount, e.currency)}\n📅 {fmt_date(e.entry_date)}"
    if e.note:
        text += f"\n<i>{escape(e.note)}</i>"
    return text


def debt_text(e: DebtEntry) -> str:
    text = debt_line(e.person, e.direction, f"<b>{fmt_money(e.amount, e.currency)}</b>")
    text += f"\n📅 {fmt_date(e.entry_date)}"
    if e.note:
        text += f"\n<i>{escape(e.note)}</i>"
    return text


def balance_lines(b: repo.DebtBalance) -> list[str]:
    """«Abbos sizga qarzdor: 50 $» yoki «Siz Abbosga qarzdorsiz: 100 000 won»."""
    person = escape(b.person)
    lines = []
    for currency, value in b.balance.items():
        if value > 0:
            lines.append(f"📤 {person} sizga qarzdor: <b>{fmt_money(value, currency)}</b>")
        elif value < 0:
            lines.append(f"📥 Siz {person}ga qarzdorsiz: <b>{fmt_money(-value, currency)}</b>")
    return lines or [f"✅ {person} bilan hisob-kitob yopilgan"]


async def person_balance(session: AsyncSession, owner_id: int, person: str) -> list[str]:
    key = repo.person_key(person)
    for b in await repo.debt_balances(session, owner_id):
        if b.key == key:
            return balance_lines(b)
    return []


# --- Xarajatlar hisoboti ----------------------------------------------------

async def _personal_report(session: AsyncSession, owner_id: int, period: str) -> str:
    start, end = period_range(period)
    rows = await repo.personal_stat_rows(session, owner_id, start, end)
    title = (
        f"💸 <b>{PERIOD_LABELS[period]}</b> "
        f"({start.strftime('%d.%m')}{'' if start == end else ' – ' + end.strftime('%d.%m')})"
    )
    if not rows:
        return f"{title}\n\nBu davrda xarajat yoki daromad yozilmagan.\n\n" + _hint()

    blocks = [title]
    totals: dict[str, dict[str, Decimal]] = {}
    for kind, label in ((PersonalKind.EXPENSE, "💸 Xarajat"), (PersonalKind.INCOME, "💰 Daromad")):
        by_cat: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
        total: dict[str, Decimal] = defaultdict(Decimal)
        for r in rows:
            if r.kind == kind:
                by_cat[r.category][r.currency] += r.amount
                total[r.currency] += r.amount
        totals[kind] = total
        if not by_cat:
            continue
        ranked = sorted(by_cat.items(), key=lambda kv: max(kv[1].values()), reverse=True)
        lines = "\n".join(f"• {escape(cat)} — {fmt_totals(dict(v))}" for cat, v in ranked)
        blocks.append(f"{label}: <b>{fmt_totals(dict(total))}</b>\n{lines}")

    expense, income = totals[PersonalKind.EXPENSE], totals[PersonalKind.INCOME]
    if expense and income:
        net = []
        for currency in dict.fromkeys([*income, *expense]):
            value = income.get(currency, Decimal(0)) - expense.get(currency, Decimal(0))
            net.append(("+" if value >= 0 else "−") + fmt_money(abs(value), currency))
        blocks.append("📊 Qoldiq: <b>" + "  ".join(net) + "</b>")
    return "\n\n".join(blocks)


def _hint() -> str:
    return "Yozish uchun: <i>«bozorga 50 ming won»</i>, <i>«benzinga 40 ming»</i>, <i>«oylik tushdi 3 million»</i>."


@router.message(F.text == kbs.BTN_EXPENSES)
async def expenses(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    text = await _personal_report(session, user.id, "month")
    await message.answer(text, reply_markup=kbs.personal_report_kb())


@router.callback_query(kbs.PersonalReportCb.filter())
async def on_personal_report(
    call: CallbackQuery, callback_data: kbs.PersonalReportCb, session: AsyncSession, user: User
) -> None:
    if callback_data.period == "recent":
        items = await repo.list_personal_entries(session, user.id, limit=10)
        await call.answer()
        if not items:
            await call.message.answer("Hali xarajat yoki daromad yozilmagan.")
            return
        await call.message.answer(f"🕘 Oxirgi {len(items)} ta yozuv:")
        for i, e in enumerate(reversed(items)):
            kb = kbs.money_entry_kb("p", e.id, app=i == len(items) - 1)
            await call.message.answer(personal_text(e), reply_markup=kb)
        return
    if callback_data.period not in PERIOD_LABELS:
        await call.answer()
        return
    text = await _personal_report(session, user.id, callback_data.period)
    await call.message.edit_text(text, reply_markup=kbs.personal_report_kb())
    await call.answer()


# --- Qarzlar ----------------------------------------------------------------

async def _debts_view(session: AsyncSession, owner_id: int) -> str:
    balances = await repo.debt_balances(session, owner_id)
    if not balances:
        return (
            "🤝 <b>Qarzlar</b>\n\nHali qarz yozilmagan.\n\n"
            "Yozish uchun: <i>«Abbosdan 100 ming won oldim»</i>, <i>«Valiga 50 dollar qarz berdim»</i>, "
            "<i>«Abbosga 30 mingini qaytardim»</i>."
        )
    owed_rows, owe_rows, settled = [], [], []
    for b in balances:
        if b.settled:
            settled.append(escape(b.person))
            continue
        for currency, value in b.balance.items():
            if value > 0:
                owed_rows.append(f"• {escape(b.person)} — {fmt_money(value, currency)}")
            elif value < 0:
                owe_rows.append(f"• {escape(b.person)} — {fmt_money(-value, currency)}")

    owed_to_me, i_owe = repo.debt_totals(balances)
    blocks = ["🤝 <b>Qarzlar</b>"]
    if owed_rows:
        blocks.append("📤 <b>Sizga qarzdorlar</b>\n" + "\n".join(owed_rows))
    if owe_rows:
        blocks.append("📥 <b>Siz qarzdorsiz</b>\n" + "\n".join(owe_rows))
    totals = []
    if owed_to_me:
        totals.append(f"Jami sizga qarz: <b>{fmt_totals(owed_to_me)}</b>")
    if i_owe:
        totals.append(f"Jami qarzingiz: <b>{fmt_totals(i_owe)}</b>")
    if totals:
        blocks.append("\n".join(totals))
    if settled:
        blocks.append("✅ Yopilgan: " + ", ".join(settled[:15]))
    return "\n\n".join(blocks)


@router.message(F.text == kbs.BTN_DEBTS)
async def debts(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    await message.answer(await _debts_view(session, user.id), reply_markup=kbs.debts_kb())


@router.callback_query(kbs.DebtsCb.filter())
async def on_debts(call: CallbackQuery, callback_data: kbs.DebtsCb, session: AsyncSession, user: User) -> None:
    if callback_data.action == "recent":
        items = await repo.list_debt_entries(session, user.id, limit=10)
        await call.answer()
        if not items:
            await call.message.answer("Hali qarz yozilmagan.")
            return
        await call.message.answer(f"🕘 Oxirgi {len(items)} ta qarz yozuvi:")
        for i, e in enumerate(reversed(items)):
            kb = kbs.money_entry_kb("d", e.id, app=i == len(items) - 1)
            await call.message.answer(debt_text(e), reply_markup=kb)
        return
    await call.message.edit_text(await _debts_view(session, user.id), reply_markup=kbs.debts_kb())
    await call.answer("Yangilandi")


# --- O'chirish --------------------------------------------------------------

async def _get(session: AsyncSession, owner_id: int, kind: str, entry_id: int) -> PersonalEntry | DebtEntry | None:
    if kind == "p":
        return await repo.get_personal_entry(session, owner_id, entry_id)
    if kind == "d":
        return await repo.get_debt_entry(session, owner_id, entry_id)
    return None


def _text(entry: PersonalEntry | DebtEntry) -> str:
    return personal_text(entry) if isinstance(entry, PersonalEntry) else debt_text(entry)


@router.callback_query(kbs.MoneyCb.filter())
async def on_money_entry(call: CallbackQuery, callback_data: kbs.MoneyCb, session: AsyncSession, user: User) -> None:
    entry = await _get(session, user.id, callback_data.kind, callback_data.entry_id)
    if entry is None:
        await call.answer("Yozuv topilmadi yoki o'chirilgan.", show_alert=True)
        return
    kind, action = callback_data.kind, callback_data.action
    if action == "del":
        await call.message.edit_reply_markup(reply_markup=kbs.money_confirm_delete_kb(kind, entry.id))
        await call.answer("Rostdan o'chirilsinmi?")
    elif action == "del_no":
        await call.message.edit_reply_markup(reply_markup=kbs.money_entry_kb(kind, entry.id))
        await call.answer()
    elif action == "del_yes":
        text = _text(entry)
        await repo.soft_delete(session, entry)
        extra = ""
        if isinstance(entry, DebtEntry):
            lines = await person_balance(session, user.id, entry.person)
            extra = "\n\n" + "\n".join(lines) if lines else ""
        await call.message.edit_text(f"<s>{text}</s>\n\n🗑 O'chirildi{extra}")
        await call.answer("O'chirildi")
    else:
        await call.answer()
