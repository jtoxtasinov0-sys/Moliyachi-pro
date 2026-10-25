"""Egasining hamyoni, mijozlari va xom ashyosi: xarajat/daromad hisoboti, qarzlar, mijozlar bilan oldi-berdi,
xom ashyo xaridlari."""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kbs
from bot.db import repo
from bot.db.models import (
    CLIENT_MONEY,
    Client,
    ClientEntry,
    DebtEntry,
    MaterialEntry,
    PersonalEntry,
    PersonalKind,
    PriceUnit,
    User,
)
from bot.drafts import CLIENT_KIND_LABELS, CLIENT_SIDE_LABELS, debt_line, material_line, qty_text
from bot.handlers.entries import ask_delete, cancel_delete, notify_user
from bot.middlewares import IsOwner
from bot.services.utils import fmt_date, fmt_money, fmt_number, fmt_totals, period_range

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
    text = debt_line(e.person, e.direction, f"<b>{fmt_money(e.amount, e.currency)}</b>", stated=e.stated)
    text += f"\n📅 {fmt_date(e.entry_date)}"
    if e.note:
        text += f"\n<i>{escape(e.note)}</i>"
    return text


def _client_body(e: ClientEntry) -> str:
    """«Maksima — 10 komplekt (50 dona) × 140 000 so'm = <b>1 400 000 so'm</b>», to'lov va qarzda — faqat summa."""
    text = ""
    if e.kind not in CLIENT_MONEY:
        if e.product:
            text += f"{escape(e.product)} — "
        text += qty_text(e.sets, e.pieces)
        if e.unit_price is not None:
            text += f" × {fmt_money(e.unit_price, e.currency)}"
            if e.price_unit == PriceUnit.PIECE:
                text += " (dona)"
        text += " = "
    return text + f"<b>{fmt_money(e.amount or 0, e.currency)}</b>"


def client_text(e: ClientEntry) -> str:
    """«👤 Tohir · 📦 Mol berildi\nMaksima — 10 komplekt (50 dona) × 140 000 so'm = 1 400 000 so'm»"""
    text = f"👤 <b>{escape(e.client.name)}</b> · {CLIENT_KIND_LABELS.get(e.kind, e.kind)}\n"
    text += f"{_client_body(e)}\n📅 {fmt_date(e.entry_date)}"
    if e.note:
        text += f"\n<i>{escape(e.note)}</i>"
    if e.by_client:
        text += f"\n✍️ {escape(e.client.name)} o'zi yozdi"
    return text


def client_side_text(e: ClientEntry) -> str:
    """Mijozning o'ziga: «📦 Mol oldingiz\nMaksima — 10 komplekt × 140 000 so'm = 1 400 000 so'm\n📅 18.10.2026»."""
    text = f"<b>{CLIENT_SIDE_LABELS.get(e.kind, e.kind)}</b>\n{_client_body(e)}\n📅 {fmt_date(e.entry_date)}"
    if e.note:
        text += f"\n<i>{escape(e.note)}</i>"
    if e.by_client:
        text += "\n✍️ O'zingiz yozdingiz"
    return text


def client_side_balance(balance: dict) -> str:
    """Mijozning o'ziga: «📌 Qarzingiz: 1 400 000 so'm» / «✅ Hisob yopilgan»."""
    owes = {c: v for c, v in balance.items() if v > 0}
    prepaid = {c: -v for c, v in balance.items() if v < 0}
    parts = []
    if owes:
        parts.append(f"📌 Qarzingiz: <b>{fmt_totals(owes)}</b>")
    if prepaid:
        parts.append(f"💵 Oldindan to'lagansiz: <b>{fmt_totals(prepaid)}</b>")
    return "\n".join(parts) or "✅ Hisob yopilgan"


def _balance_short(balance: dict, client_side: bool) -> str:
    """Kun oxiridagi qoldiq: «qarzi 1 400 000 so'm» (mijozga — «qarzingiz …»)."""
    owes = {c: v for c, v in balance.items() if v > 0}
    prepaid = {c: -v for c, v in balance.items() if v < 0}
    parts = []
    if owes:
        parts.append(("qarzingiz " if client_side else "qarzi ") + fmt_totals(owes))
    if prepaid:
        parts.append(("oldindan to'lagansiz " if client_side else "oldindan to'lagan ") + fmt_totals(prepaid))
    return ", ".join(parts) or "hisob yopilgan"


CLIENT_LEDGER_ROWS = 25
CLIENT_LEDGER_BUDGET = 3000


async def client_ledger_blocks(session: AsyncSession, client: Client, client_side: bool) -> tuple[list[str], bool]:
    """Kunma-kun oldi-berdi (eskisi tepada), har kun oxirida qoldiq. Qaytadi: (bloklar, oldingilari ham bormi)."""
    rows, more = await repo.client_ledger(session, client.owner_id, client.id, limit=CLIENT_LEDGER_ROWS)
    labels = CLIENT_SIDE_LABELS if client_side else CLIENT_KIND_LABELS
    days: list[tuple[object, list[repo.ClientLedgerRow]]] = []
    for r in reversed(rows):
        if days and days[-1][0] == r.entry.entry_date:
            days[-1][1].append(r)
        else:
            days.append((r.entry.entry_date, [r]))
    blocks: list[str] = []
    used = 0
    for day, items in reversed(days):  # yangisidan boshlab, joy yetguncha
        lines = []
        for r in items:
            line = f"{labels.get(r.entry.kind, r.entry.kind)}: {_client_body(r.entry)}"
            if r.entry.note:
                line += f" <i>({escape(r.entry.note)})</i>"
            lines.append(line + (" ✍️" if r.entry.by_client else ""))
        rows_text = "\n".join(lines)
        if len(rows_text) > CLIENT_LEDGER_BUDGET:  # bir kunda juda ko'p yozuv — kunning oxirgilari
            rows_text = "…\n" + rows_text[-CLIENT_LEDGER_BUDGET:].split("\n", 1)[-1]
            more = True
        block = f"📅 <b>{fmt_date(day)}</b>\n" + rows_text
        block += f"\n   <i>= {_balance_short(items[-1].balance, client_side)}</i>"
        if blocks and used + len(block) > CLIENT_LEDGER_BUDGET:
            more = True
            break
        blocks.append(block)
        used += len(block)
    blocks.reverse()
    return blocks, more


async def client_side_ledger(session: AsyncSession, client: Client) -> str:
    """Mijozning o'ziga: korxona bilan hisob-kitobi."""
    owner = await session.get(User, client.owner_id)
    owner_name = escape(owner.name) if owner else "Korxona"
    title = f"🧾 <b>{owner_name} bilan hisob-kitob</b>"
    blocks, more = await client_ledger_blocks(session, client, client_side=True)
    example = "💡 Yozish: <i>«10 komplekt oldim»</i>, <i>«2 million berdim»</i>, <i>«3 komplekt qaytardim»</i>"
    if not blocks:
        return f"{title}\n\nHali oldi-berdi yo'q.\n\n{example}"
    parts = [title]
    if more:
        parts.append("<i>… oldingi yozuvlar ko'rsatilmadi</i>")
    parts.extend(blocks)
    parts.append(client_side_balance(await repo.client_balance(session, client.owner_id, client.id)))
    parts.append(f"{example}\n✍️ — o'zingiz yozgansiz")
    return "\n\n".join(parts)


async def client_deleted_message(
    session: AsyncSession, owner: User, entry: ClientEntry
) -> tuple[int, str] | None:
    """Egasi mijoz yozuvini o'chirdi — ulangan mijozga xabar: (chat, matn)."""
    client = entry.client
    if not client.is_active or not client.tg_id:
        return None
    balance = await repo.client_balance(session, owner.id, client.id)
    text = (
        f"🗑 <b>{escape(owner.name)}</b> bu yozuvni o'chirdi:\n\n<s>{client_side_text(entry)}</s>\n\n"
        + client_side_balance(balance)
    )
    return client.tg_id, text


async def client_restored_message(
    session: AsyncSession, owner: User, entry: ClientEntry
) -> tuple[int, str] | None:
    """Egasi o'chirilgan yozuvni qaytardi — ulangan mijozga xabar: (chat, matn)."""
    client = entry.client
    if not client.is_active or not client.tg_id:
        return None
    balance = await repo.client_balance(session, owner.id, client.id)
    text = (
        f"↩️ <b>{escape(owner.name)}</b> o'chirilgan yozuvni qaytardi:\n\n{client_side_text(entry)}\n\n"
        + client_side_balance(balance)
    )
    return client.tg_id, text


def signed_totals(totals: dict[str, Decimal]) -> str:
    """{'UZS': 8010000, 'USD': -5} -> «+8 010 000 so'm, −5 $»."""
    return ", ".join(("+" if v > 0 else "−") + fmt_money(abs(v), c) for c, v in totals.items() if v) or "0"


@dataclass
class MergedDebts:
    entries: list[ClientEntry]  # mijoz hisobiga o'tgan yozuvlar
    added: dict[str, Decimal]  # qoldiq qanchaga o'zgardi: musbat — mijoz qarzi ko'paydi
    balance: dict[str, Decimal]  # mijozning yangi qoldig'i
    notice: tuple[int, str] | None  # ulangan mijozga xabar: (chat, matn)


async def merge_client_debts(
    session: AsyncSession, owner: User, client: Client, keys: list[str] | None = None
) -> MergedDebts:
    """«Qarzlar»da shu mijoz ismi bilan turgan qarzlar uning hisobiga o'tadi (umumiy qarzlar jami o'zgarmaydi).
    keys — faqat egasi tanlagan odamlar (person_key); bo'lmasa — hammasi."""
    before = await repo.client_balance(session, owner.id, client.id)
    rows = await repo.client_outside_debts(session, owner.id, client)
    chosen = [b.key for b in rows if keys is None or b.key in keys]
    moved = await repo.move_debts_to_client(session, owner.id, client, chosen)
    balance = await repo.client_balance(session, owner.id, client.id)
    added = {c: balance.get(c, Decimal(0)) - before.get(c, Decimal(0)) for c in {*before, *balance}}
    added = {c: v for c, v in added.items() if v}
    notice = None
    if moved and client.is_active and client.tg_id:
        text = (
            f"🛍 <b>{escape(owner.name)}</b> avval alohida yozilgan eski hisobni qo'shdi: "
            f"<b>{signed_totals(added)}</b>\n\n{client_side_balance(balance)}"
        )
        notice = (client.tg_id, text)
    return MergedDebts(moved, added, balance, notice)


def material_text(e: MaterialEntry, supplier: bool = True) -> str:
    """«🧱 <b>Teri</b> «Shirin gul» — 1 list = 10 000 000 so'm\n🏪 Alisher\n📅 18.10.2026».
    supplier=False — ta'minotchining o'ziga: kimdan olingani ko'rsatilmaydi."""
    text = "🧱 " + material_line(e.kind, e.name, e.quantity, e.unit, e.unit_price, e.amount, e.currency)
    if supplier and e.supplier:
        text += f"\n🏪 {escape(e.supplier)}"
    if e.on_credit:
        text += "\n💳 Qarzga olindi"
    return text + f"\n📅 {fmt_date(e.entry_date)}"


def material_kb(e: MaterialEntry, app: bool = False):
    """Xarid kartochkasi tugmalari: «💳 Qarzga qo'shish» / «💵 To'langan» (kimdan olingani va narxi bo'lsa), o'chirish."""
    return kbs.money_entry_kb("x", e.id, app=app, credit=e.on_credit if repo.material_credit_ok(e) else None)


def supplier_month_line(st: repo.SupplierStats) -> str:
    """«🧾 Shu oy jami: 5 000 000 so'm (3 ta xarid)»."""
    return f"🧾 Shu oy jami: {_sum_text(st.month)} ({st.month_count} ta xarid)"


async def supplier_deleted_message(
    session: AsyncSession, owner: User, entry: MaterialEntry
) -> tuple[int, str] | None:
    """O'chirilgan xarid ta'minotchidan olingan bo'lsa — unga xabar (avval «sizdan oldi» deb borgan): (chat, matn)."""
    if not entry.supplier_id:
        return None
    supplier = await repo.get_supplier(session, owner.id, entry.supplier_id)
    if supplier is None or not supplier.is_active or not supplier.tg_id:
        return None
    text = f"🗑 <b>{escape(owner.name)}</b> bu yozuvni o'chirdi:\n\n<s>{material_text(entry, supplier=False)}</s>"
    return supplier.tg_id, text


async def material_month_line(session: AsyncSession, owner_id: int) -> str:
    """«🧱 Shu oy xom ashyoga: 12 500 000 so'm (3 ta xarid)»."""
    start, end = period_range("month")
    rows = await repo.material_stat_rows(session, owner_id, start, end)
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for r in rows:
        totals[r.currency] += r.amount
    count = sum(r.count for r in rows)
    return f"🧱 Shu oy xom ashyoga: {_sum_text(totals)} ({count} ta xarid)"


def _sum_text(totals: dict[str, Decimal]) -> str:
    return f"<b>{fmt_totals(dict(totals))}</b>" if any(totals.values()) else "narxi aytilmagan"


def client_balance_text(name: str, balance: dict) -> str:
    """«📌 Tohir qarzi: 1 400 000 so'm» / «✅ Tohir bilan hisob yopilgan»."""
    name = escape(name)
    owes = {c: v for c, v in balance.items() if v > 0}
    prepaid = {c: -v for c, v in balance.items() if v < 0}
    parts = []
    if owes:
        parts.append(f"📌 {name} qarzi: <b>{fmt_totals(owes)}</b>")
    if prepaid:
        parts.append(f"💵 {name} oldindan to'lagan: <b>{fmt_totals(prepaid)}</b>")
    return "\n".join(parts) or f"✅ {name} bilan hisob yopilgan"


async def client_balance_line(session: AsyncSession, owner_id: int, client: Client) -> str:
    return client_balance_text(client.name, await repo.client_balance(session, owner_id, client.id))


async def client_outside_block(
    session: AsyncSession, owner_id: int, client: Client, hint: bool = True
) -> tuple[list[repo.DebtBalance], str | None]:
    """Mijozning «Qarzlar»da qolib ketgan qarzi — u mijoz hisobiga (qoldig'iga) kirmagan: (qatorlar, matn).
    Shu ismda ishchi yoki ta'minotchi ham bo'lsa — aytiladi (boshqa odam bo'lishi mumkin). hint — qanday
    qo'shilishi ham yoziladi (mijoz kartasida tugmasi bor)."""
    rows = await repo.client_outside_debts(session, owner_id, client)
    if not rows:
        return rows, None
    also = await repo.namesakes(session, owner_id, [b.person for b in rows])
    lines = []
    for b in rows:
        lines.extend(balance_lines(b))
        if b.person in also:
            lines.append(f"   ❗ Shu ismda {escape(', '.join(also[b.person]))} ham bor — o'sha emasligini tekshiring")
    text = f"⚠️ <b>«Qarzlar»da ham bor</b> — {escape(client.name)} hisobiga kirmagan:\n" + "\n".join(lines)
    if hint:
        text += (
            "\nBu o'shami — tekshirib, mijoz hisobiga qo'shing: «🛍 Mijozlar» → mijoz kartasi yoki ilovada mijoz "
            "oynasi (har biri alohida so'raladi)."
        )
    return rows, text


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


# --- Xom ashyo --------------------------------------------------------------

def _qty_totals(quantities: dict[str | None, Decimal]) -> str:
    """{"list": 12, "kg": 20} -> «12 list, 20 kg»."""
    return ", ".join(f"{fmt_number(q)} {escape(unit or 'ta')}" for unit, q in quantities.items() if q)


async def _materials_report(session: AsyncSession, owner_id: int, period: str) -> str:
    start, end = period_range(period)
    rows = await repo.material_stat_rows(session, owner_id, start, end)
    title = (
        f"🧱 <b>Xom ashyo · {PERIOD_LABELS[period]}</b> "
        f"({start.strftime('%d.%m')}{'' if start == end else ' – ' + end.strftime('%d.%m')})"
    )
    debts = await supplier_debts_block(session, owner_id)
    if not rows:
        text = (
            f"{title}\n\nBu davrda xom ashyo yozilmagan.\n\nYozish uchun ovoz yoki matn yuboring: "
            "<i>«1 list teri oldim»</i>, <i>«10 millionlik teri oldim, nomi Shirin gul»</i>, "
            "<i>«20 kg kley oldim, kilosi 45 ming»</i>."
        )
        return text + (f"\n\n{debts}" if debts else "")
    by_kind: dict[str, dict] = {}
    total: dict[str, Decimal] = defaultdict(Decimal)
    for r in rows:
        k = by_kind.setdefault(r.kind, {"amount": defaultdict(Decimal), "qty": defaultdict(Decimal), "count": 0})
        k["amount"][r.currency] += r.amount
        k["qty"][r.unit] += r.quantity
        k["count"] += r.count
        total[r.currency] += r.amount
    ranked = sorted(by_kind.items(), key=lambda kv: max(kv[1]["amount"].values(), default=0), reverse=True)
    lines = []
    for kind, k in ranked:
        parts = []
        if qty := _qty_totals(k["qty"]):
            parts.append(qty)
        if any(k["amount"].values()):
            parts.append(fmt_totals(dict(k["amount"])))
        lines.append(f"• <b>{escape(kind)}</b> — " + (" · ".join(parts) or f"{k['count']} ta xarid"))
    count = sum(r.count for r in rows)
    text = f"{title}\n\nJami: {_sum_text(total)} ({count} ta xarid)\n\n" + "\n".join(lines)
    return text + (f"\n\n{debts}" if debts else "")


@router.message(F.text == kbs.BTN_MATERIALS)
async def materials(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    text = await _materials_report(session, user.id, "month")
    await message.answer(text, reply_markup=kbs.materials_report_kb())


@router.callback_query(kbs.MaterialReportCb.filter())
async def on_materials_report(
    call: CallbackQuery, callback_data: kbs.MaterialReportCb, session: AsyncSession, user: User
) -> None:
    if callback_data.period == "recent":
        items = await repo.list_material_entries(session, user.id, limit=10)
        await call.answer()
        if not items:
            await call.message.answer("Hali xom ashyo yozilmagan.")
            return
        await call.message.answer(f"🕘 Oxirgi {len(items)} ta xarid:")
        for i, e in enumerate(reversed(items)):
            await call.message.answer(material_text(e), reply_markup=material_kb(e, app=i == len(items) - 1))
        return
    if callback_data.period not in PERIOD_LABELS:
        await call.answer()
        return
    text = await _materials_report(session, user.id, callback_data.period)
    await call.message.edit_text(text, reply_markup=kbs.materials_report_kb())
    await call.answer()


# --- Qarzlar ----------------------------------------------------------------

DEBT_SECTIONS = {
    "debt": "🤝 <b>Odamlar bilan qarzlar</b>",
    "material": "🧱 <b>Xom ashyo — ta'minotchilar</b>",
    "worker": "👷 <b>Ishchilar</b>",
    "client": "🛍 <b>Mijozlar</b>",
}
DEBTS_BUDGET = 3300  # Telegram xabari 4096 belgigacha


def debt_rows(b: repo.DebtBalance) -> list[str]:
    """«• Aslcharm: 📥 siz qarzdorsiz <b>48 792 850 so'm</b>» — har bir valyuta alohida."""
    return [
        f"• {escape(b.person)}: "
        + ("📤 sizga qarzdor" if value > 0 else "📥 siz qarzdorsiz")
        + f" <b>{fmt_money(abs(value), currency)}</b>"
        for currency, value in b.balance.items()
        if value
    ]


def debt_sum_text(owed: dict[str, Decimal], owe: dict[str, Decimal]) -> str:
    """«sizga qarz 50 000 so'm · qarzingiz 200 000 so'm»."""
    parts = []
    if owed:
        parts.append(f"sizga qarz <b>{fmt_totals(owed)}</b>")
    if owe:
        parts.append(f"qarzingiz <b>{fmt_totals(owe)}</b>")
    return " · ".join(parts) or "0"


async def _debts_view(session: AsyncSession, owner_id: int) -> str:
    """Umumiy qarzlar bo'limlarga ajratilgan (odamlar, ta'minotchilar, ishchilar, mijozlar — har birining jami bilan),
    oxirida hammasining jami."""
    balances = await repo.debt_overview(session, owner_id)
    if not balances:
        return (
            "🤝 <b>Qarzlar</b>\n\nHali qarz yozilmagan.\n\n"
            "Yozish uchun: <i>«Abbosdan 100 ming won oldim»</i>, <i>«Valiga 50 dollar qarz berdim»</i>, "
            "<i>«Abbosga 30 mingini qaytardim»</i>.\n"
            "Ishchilar va mijozlar hisobi, ta'minotchidan olingan xom ashyo ham shu yerda ko'rinadi."
        )
    blocks = ["🤝 <b>Qarzlar</b>"]
    budget = DEBTS_BUDGET
    for source, (owed, owe) in repo.debt_groups(balances).items():
        open_ = [b for b in balances if b.source == source and not b.settled]
        if not open_:
            continue
        lines = [f"{DEBT_SECTIONS[source]} — {debt_sum_text(owed, owe)}"]
        shown = 0
        for b in open_:
            rows = debt_rows(b)
            size = sum(len(row) + 1 for row in rows)
            if shown and size > budget:
                break
            budget -= size
            lines.extend(rows)
            shown += 1
        if shown < len(open_):
            lines.append(f"… yana {len(open_) - shown} ta — ilovada")
        blocks.append("\n".join(lines))

    owed_to_me, i_owe = repo.debt_totals(balances)
    totals = ["📊 <b>Jami (hamma bo'limlar)</b>"]
    if owed_to_me:
        totals.append(f"📤 Sizga qarz: <b>{fmt_totals(owed_to_me)}</b>")
    if i_owe:
        totals.append(f"📥 Qarzingiz: <b>{fmt_totals(i_owe)}</b>")
    if len(totals) > 1:
        blocks.append("\n".join(totals))
    settled = [escape(b.person) for b in balances if b.settled]
    if settled:
        blocks.append("✅ Yopilgan: " + ", ".join(settled[:15]))
    return "\n\n".join(blocks)


async def supplier_debts_block(session: AsyncSession, owner_id: int) -> str | None:
    """Xom ashyo hisobotida: ta'minotchilar bilan qarz (umumiy qarzlardagi «Xom ashyo» bo'limi)."""
    debts = await repo.supplier_debts(session, owner_id)
    if not debts:
        return None
    owed, owe = repo.debt_totals(debts)
    lines = [f"💳 <b>Ta'minotchilar bilan qarz</b> — {debt_sum_text(owed, owe)}"]
    for b in debts[:15]:
        lines.extend(debt_rows(b))
    if len(debts) > 15:
        lines.append(f"… yana {len(debts) - 15} ta — «🤝 Qarzlar»da")
    lines.append("<i>Umumiy «🤝 Qarzlar»ga ham qo'shilgan.</i>")
    return "\n".join(lines)


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

async def _get(
    session: AsyncSession, owner_id: int, kind: str, entry_id: int
) -> PersonalEntry | DebtEntry | ClientEntry | MaterialEntry | None:
    if kind == "x":
        return await repo.get_material_entry(session, owner_id, entry_id)
    if kind == "p":
        return await repo.get_personal_entry(session, owner_id, entry_id)
    if kind == "d":
        return await repo.get_debt_entry(session, owner_id, entry_id)
    if kind == "c":
        return await repo.get_client_entry(session, owner_id, entry_id)
    return None


def _text(entry: PersonalEntry | DebtEntry | ClientEntry | MaterialEntry) -> str:
    if isinstance(entry, MaterialEntry):
        return material_text(entry)
    if isinstance(entry, ClientEntry):
        return client_text(entry)
    return personal_text(entry) if isinstance(entry, PersonalEntry) else debt_text(entry)


@router.callback_query(kbs.MoneyCb.filter())
async def on_money_entry(call: CallbackQuery, callback_data: kbs.MoneyCb, session: AsyncSession, user: User) -> None:
    entry = await _get(session, user.id, callback_data.kind, callback_data.entry_id)
    if entry is None:
        await call.answer("Yozuv topilmadi yoki o'chirilgan.", show_alert=True)
        return
    kind, action = callback_data.kind, callback_data.action
    if action == "del":
        await ask_delete(call, kbs.money_confirm_delete_kb(kind, entry.id))
    elif action == "del_no":
        kb = material_kb(entry) if isinstance(entry, MaterialEntry) else kbs.money_entry_kb(kind, entry.id)
        await cancel_delete(call, kb)
    elif action == "credit" and isinstance(entry, MaterialEntry) and repo.material_credit_ok(entry):
        # Ta'minotchiga qarz (umumiy qarzlarda) yoki to'langan xarid (faqat xom ashyoda)
        entry.on_credit = not entry.on_credit
        await session.flush()
        lines = await person_balance(session, user.id, entry.supplier)
        status = "💳 Qarzga qo'shildi" if entry.on_credit else "💵 To'langan — qarzdan chiqarildi"
        text = material_text(entry) + f"\n\n{status}" + ("\n" + "\n".join(lines) if lines else "")
        await call.message.edit_text(text, reply_markup=material_kb(entry))
        await call.answer(status)
    elif action == "del_yes":
        text = _text(entry)
        await repo.soft_delete(session, entry)
        extra = ""
        if isinstance(entry, DebtEntry):
            lines = await person_balance(session, user.id, entry.person)
            extra = "\n\n" + "\n".join(lines) if lines else ""
        elif isinstance(entry, ClientEntry):
            extra = "\n\n" + await client_balance_line(session, user.id, entry.client)
        await call.message.edit_text(f"<s>{text}</s>\n\n🗑 O'chirildi{extra}")
        await call.answer("O'chirildi")
        notice = None
        if isinstance(entry, MaterialEntry):
            notice = await supplier_deleted_message(session, user, entry)
        elif isinstance(entry, ClientEntry):
            notice = await client_deleted_message(session, user, entry)
        if notice:
            await notify_user(call.bot, *notice)
    else:
        await call.answer()
