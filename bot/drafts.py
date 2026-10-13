"""Qoralama: LLM ajratgan, lekin hali tasdiqlanmagan yozuv.

Qoralama FSM holatida oddiy dict sifatida saqlanadi. Ish topshirish:
{
  "section": "work",
  "entry_id": None | int,         # mavjud yozuvni tahrirlashda
  "date": "YYYY-MM-DD",
  "raw_text": "...",
  "created": "ISO vaqt",
  "lines": [{worker_id, worker_name, worker_q, model_id, model_name, model_q,
             quantity, price, currency}],
}
Shaxsiy pul (xarajat/daromad va qarz) — "section": "money":
{
  ..., "personal": [{kind, category, amount, currency, note, date, dup}],
       "debt": [{person, direction, amount, currency, note, date}],
}
date — yozuvning o'z sanasi (bank tarixida har xil kunlar), None — umumiy sana.
dup — rasmdan o'qilgan, lekin avval kiritilgan yozuv: saqlanmaydi.
Rasmdan olingan qoralamada "source": "image".
Mijoz bilan savdo — "section": "client":
{
  ..., "set_size": 5,
       "client": [{kind, client_id, client_name, client_q, client_new, product,
                   sets, pieces, price, price_unit, total, currency, note}],
}
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kbs
from bot.db import repo
from bot.db.models import ClientKind, DebtDirection, ItemModel, PersonalKind, PriceUnit, User, WorkEntry
from bot.services import matcher
from bot.services.llm import Extraction
from bot.services.utils import fmt_date, fmt_money, fmt_totals, today

DRAFT_TTL = timedelta(minutes=30)


def _empty_line() -> dict:
    return {
        "worker_id": None, "worker_name": None, "worker_q": None,
        "model_id": None, "model_name": None, "model_q": None,
        "quantity": None, "price": None, "currency": None,
    }


def _parse_date(value: str | None) -> date:
    if value:
        try:
            d = date.fromisoformat(value)
            # Kelajakdagi sana deyarli har doim xato
            if d <= today() + timedelta(days=1):
                return d
        except ValueError:
            pass
    return today()


def _item_date(value: str | None, draft_date: str) -> str | None:
    """Yozuvning o'z sanasi — faqat umumiy sanadan farq qilsa."""
    if not value:
        return None
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return None
    if d > today() + timedelta(days=1) or d.isoformat() == draft_date:
        return None
    return d.isoformat()


def _amount(value: float | None) -> str | None:
    if value is None:
        return None
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    if amount <= 0 or amount >= Decimal("1e13"):
        return None
    return str(int(amount)) if amount == amount.to_integral_value() else str(amount)


def is_money(ext: Extraction) -> bool:
    return ext.section in ("personal", "debt") and bool(ext.personal or ext.debt)


def is_client(ext: Extraction) -> bool:
    return ext.section == "client" and bool(ext.client)


def _count(value: int | None) -> int | None:
    return value if value and 0 < value < 1_000_000 else None


def client_total(item: dict, set_size: int) -> None:
    """Dona sonini va jami summani komplekt soni va narxdan hisoblaydi."""
    if item["sets"]:
        item["pieces"] = item["sets"] * set_size
    if item["kind"] == ClientKind.PAYMENT or not item["price"]:
        return
    qty = item["sets"] if item["price_unit"] == PriceUnit.SET else item["pieces"]
    if qty:
        item["total"] = _amount(float(Decimal(item["price"]) * qty))


def _client_items(ext: Extraction, default_currency: str, set_size: int) -> list[dict]:
    items = []
    for c in ext.client:
        price, unit = None, None
        if c.price_per_set:
            price, unit = _amount(c.price_per_set), PriceUnit.SET
        elif c.price_per_piece:
            price, unit = _amount(c.price_per_piece), PriceUnit.PIECE
        item = {
            "kind": c.kind,
            "client_id": None,
            "client_name": None,
            "client_q": (c.client_name or "").strip()[:100] or None,
            "client_new": False,
            "product": (c.product or "").strip()[:100] or None,
            "sets": _count(c.sets),
            "pieces": _count(c.pieces),
            "price": price,
            "price_unit": unit,
            "total": _amount(c.total),
            "currency": c.currency or default_currency,
            "note": (c.note or "").strip()[:200] or None,
        }
        if item["kind"] == ClientKind.PAYMENT:
            item.update(sets=None, pieces=None, price=None, price_unit=None)
        client_total(item, set_size)
        items.append(item)
    return items


def from_extraction(
    ext: Extraction,
    raw_text: str,
    entry_id: int | None = None,
    default_currency: str = "UZS",
    set_size: int = 5,
) -> dict:
    draft = {
        "section": "work",
        "entry_id": entry_id,
        "date": _parse_date(ext.date).isoformat(),
        "raw_text": raw_text,
        "created": datetime.now(timezone.utc).isoformat(),
    }
    if is_client(ext):
        said = {item.currency for item in ext.client if item.currency}
        if len(said) == 1:
            default_currency = said.pop()
        draft.update(section="client", entry_id=None, set_size=set_size)
        draft["client"] = _client_items(ext, default_currency, set_size)
        return draft
    if is_money(ext):
        # «bozorga 35 ming won, benzinga 60 ming» — ikkinchisi ham won
        said = {item.currency for item in [*ext.personal, *ext.debt] if item.currency}
        if len(said) == 1:
            default_currency = said.pop()
        draft["section"] = "money"
        draft["entry_id"] = None
        draft["personal"] = [
            {
                "kind": item.kind or PersonalKind.EXPENSE,
                "category": (item.category or "Boshqa").strip()[:50] or "Boshqa",
                "amount": _amount(item.amount),
                "currency": item.currency or default_currency,
                "note": (item.note or "").strip()[:200] or None,
                "date": _item_date(item.date, draft["date"]),
                "dup": False,
            }
            for item in ext.personal
        ]
        draft["debt"] = [
            {
                "person": (item.person or "").strip()[:100] or None,
                "direction": item.direction,
                "stated": item.stated,
                "amount": _amount(item.amount),
                "currency": item.currency or default_currency,
                "note": (item.note or "").strip()[:200] or None,
                "date": _item_date(item.date, draft["date"]),
            }
            for item in ext.debt
        ]
        return draft

    lines = []
    for item in ext.work:
        line = _empty_line()
        line["worker_q"] = item.worker_name
        line["model_q"] = item.model_name
        line["quantity"] = item.quantity if item.quantity and item.quantity > 0 else None
        lines.append(line)
    draft["lines"] = lines
    return draft


def from_entry(entry: WorkEntry) -> dict:
    line = _empty_line()
    line.update(
        worker_id=entry.worker_id, worker_name=entry.worker.name, worker_q=entry.worker.name,
        model_id=entry.model_id, model_name=entry.model.name, model_q=entry.model.name,
        quantity=entry.quantity,
        price=str(entry.unit_price) if entry.unit_price is not None else None,
        currency=entry.price_currency,
    )
    return {
        "section": "work",
        "entry_id": entry.id,
        "date": entry.work_date.isoformat(),
        "raw_text": entry.raw_text or "",
        "created": datetime.now(timezone.utc).isoformat(),
        "lines": [line],
    }


def is_stale(draft: dict) -> bool:
    created = datetime.fromisoformat(draft["created"])
    return datetime.now(timezone.utc) - created > DRAFT_TTL


def _num(v: str | None) -> float | None:
    return float(v) if v is not None else None


def for_llm(draft: dict) -> dict:
    """LLM ga tuzatish uchun yuboriladigan qisqa ko'rinish."""
    if draft["section"] == "client":
        return {
            "date": draft["date"],
            "client": [
                {
                    "client_name": c["client_name"] or c["client_q"],
                    "kind": c["kind"],
                    "product": c["product"],
                    "sets": c["sets"],
                    "pieces": None if c["sets"] else c["pieces"],
                    "price_per_set": _num(c["price"]) if c["price_unit"] == PriceUnit.SET else None,
                    "price_per_piece": _num(c["price"]) if c["price_unit"] == PriceUnit.PIECE else None,
                    "total": None if c["price"] else _num(c["total"]),
                    "currency": c["currency"],
                    "note": c["note"],
                }
                for c in draft["client"]
            ],
        }
    if draft["section"] == "money":
        num = _num

        return {
            "date": draft["date"],
            "personal": [
                {**{k: v for k, v in p.items() if k != "dup"}, "amount": num(p["amount"])} for p in draft["personal"]
            ],
            "debt": [{**d, "amount": num(d["amount"])} for d in draft["debt"]],
        }
    return {
        "date": draft["date"],
        "work": [
            {
                "worker_name": ln["worker_name"] or ln["worker_q"],
                "model_name": ln["model_name"] or ln["model_q"],
                "quantity": ln["quantity"],
            }
            for ln in draft["lines"]
        ],
    }


async def known_names(session: AsyncSession, user: User) -> dict[str, list[str]]:
    acc = user.account_id
    known = {"models": [m.name for m in await repo.list_models(session, acc)]}
    if user.is_owner:
        known["workers"] = [w.name for w in await repo.list_workers(session, acc)]
        known["categories"] = await repo.personal_categories(session, acc)
        known["people"] = [name for _, name in await repo.debt_people(session, acc)][:60]
        known["clients"] = [c.name for c in await repo.list_clients(session, acc)][:100]
    return known


def set_model(line: dict, model: ItemModel) -> None:
    line["model_id"] = model.id
    line["model_name"] = model.name
    line["price"] = str(model.price) if model.price is not None else None
    line["currency"] = model.currency if model.price is not None else None


async def resolve(session: AsyncSession, draft: dict, user: User) -> None:
    """Ismlarni bazadagi yozuvlarga bog'laydi (faqat aniq mos kelganlarini)."""
    acc = user.account_id
    if draft["section"] == "client":
        await _resolve_clients(session, draft, acc)
        return
    if draft["section"] == "money":
        # Qarz: avval yozilgan odam bo'lsa — o'sha yozilishi. Yangi odam haqida savol berilmaydi.
        people = await repo.debt_people(session, acc)
        items = [(i, name) for i, (_, name) in enumerate(people)]
        for d in draft["debt"]:
            if d["person"] and items:
                res = matcher.match(d["person"], items)
                if res.exact_id is not None:
                    d["person"] = people[res.exact_id][1]
        return

    workers = await repo.list_workers(session, acc)
    worker_aliases = await repo.get_aliases(session, acc, "worker")
    models = await repo.list_models(session, acc)
    model_aliases = await repo.get_aliases(session, acc, "model")
    models_by_id = {m.id: m for m in models}

    for line in draft["lines"]:
        if not user.is_owner:
            line["worker_id"], line["worker_name"] = user.id, user.name
        elif line["worker_id"] is None and line["worker_q"]:
            res = matcher.match(line["worker_q"], [(w.id, w.name) for w in workers], worker_aliases)
            if res.exact_id:
                line["worker_id"] = res.exact_id
                line["worker_name"] = next(w.name for w in workers if w.id == res.exact_id)

        if line["model_id"] is None and line["model_q"]:
            res = matcher.match(line["model_q"], [(m.id, m.name) for m in models], model_aliases)
            if res.exact_id:
                set_model(line, models_by_id[res.exact_id])


async def _resolve_clients(session: AsyncSession, draft: dict, acc: int) -> None:
    """Aniq mos kelsa — mavjud mijoz, o'xshashi bo'lmasa — yangi mijoz, aks holda so'raladi."""
    clients = await repo.list_clients(session, acc)
    items = [(c.id, c.name) for c in clients]
    aliases = await repo.get_aliases(session, acc, "client")
    names = dict(items)
    for c in draft["client"]:
        if c["client_id"] is not None or c["client_new"] or not c["client_q"]:
            continue
        res = matcher.match(c["client_q"], items, aliases)
        if res.exact_id is not None:
            c["client_id"], c["client_name"] = res.exact_id, names[res.exact_id]
        elif not res.options:
            c["client_new"], c["client_name"] = True, c["client_q"]


def item_date(draft: dict, item: dict) -> str:
    return item.get("date") or draft["date"]


async def mark_duplicates(session: AsyncSession, draft: dict, owner_id: int) -> None:
    """Rasmdan o'qilgan xarajat/daromad shu kuni xuddi shu summada avval yozilgan bo'lsa — qayta qo'shilmaydi.

    Bir xil summali ikkita yozuv bo'lsa, bazada nechta bo'lsa shunchasi takror hisoblanadi.
    """
    items = [p for p in draft["personal"] if p["amount"] is not None]
    for p in draft["personal"]:
        p["dup"] = False
    if not items:
        return
    days = [date.fromisoformat(item_date(draft, p)) for p in items]
    existing = Counter(
        (e.entry_date.isoformat(), e.kind, e.amount, e.currency)
        for e in await repo.personal_entries_between(session, owner_id, min(days), max(days))
    )
    for p in items:
        key = (item_date(draft, p), p["kind"], Decimal(p["amount"]), p["currency"])
        if existing[key] > 0:
            existing[key] -= 1
            p["dup"] = True


def next_missing(draft: dict) -> tuple[str, int] | None:
    if draft["section"] == "client":
        for i, c in enumerate(draft["client"]):
            if c["client_id"] is None and not c["client_new"]:
                return ("client" if c["client_q"] else "client_name"), i
            if c["kind"] not in (ClientKind.SALE, ClientKind.PAYMENT, ClientKind.RETURN):
                return "client_kind", i
            if c["total"] is None:
                if c["kind"] != ClientKind.PAYMENT and not (c["sets"] or c["pieces"]):
                    return "c_qty", i
                return ("c_price" if c["kind"] != ClientKind.PAYMENT else "c_amount"), i
        return None
    if draft["section"] == "money":
        for i, p in enumerate(draft["personal"]):
            if p["amount"] is None:
                return "p_amount", i
        for i, d in enumerate(draft["debt"]):
            if not d["person"]:
                return "person", i
            if d["direction"] not in (DebtDirection.IN, DebtDirection.OUT):
                return "direction", i
            if d["amount"] is None:
                return "d_amount", i
        return None

    for i, line in enumerate(draft["lines"]):
        if line["worker_id"] is None:
            return "worker", i
        if line["model_id"] is None:
            return "model", i
        if not line["quantity"]:
            return "quantity", i
    return None


def _short_raw(draft: dict) -> str | None:
    raw = draft.get("raw_text") or ""
    if not raw:
        return None
    short = raw if len(raw) <= 300 else raw[:300] + "…"
    if draft.get("source") == "image":
        return f"<i>{escape(short)}</i>"
    return f"🗣 <i>«{escape(short)}»</i>"


def _render(draft: dict) -> str:
    title = (
        f"✏️ <b>Yozuv #{draft['entry_id']} — tahrirlash</b>"
        if draft["entry_id"]
        else "📝 <b>Ish topshirish</b>"
    )
    parts = [title]
    if raw := _short_raw(draft):
        parts.append(raw)
    parts.append(f"📅 Sana: {fmt_date(date.fromisoformat(draft['date']))}")

    lines = draft["lines"]
    worker_ids = {ln["worker_id"] for ln in lines}
    one_worker = len(worker_ids) == 1 and None not in worker_ids
    if one_worker:
        parts.append(f"👷 Ishchi: <b>{escape(lines[0]['worker_name'])}</b>")

    totals: dict[str, Decimal] = {}
    rows = []
    for ln in lines:
        model = escape(ln["model_name"] or (f"{ln['model_q']}?" if ln["model_q"] else "?"))
        qty = f"{ln['quantity']} ta" if ln["quantity"] else "? ta"
        row = f"• {model} — {qty}"
        if not one_worker:
            worker = escape(ln["worker_name"] or (f"{ln['worker_q']}?" if ln["worker_q"] else "?"))
            row = f"• {worker}: {model} — {qty}"
        if ln["price"] and ln["quantity"]:
            pay = Decimal(ln["price"]) * ln["quantity"]
            totals[ln["currency"]] = totals.get(ln["currency"], Decimal(0)) + pay
            row += f" × {fmt_money(Decimal(ln['price']), ln['currency'])}"
        rows.append(row)
    parts.append("\n".join(rows))
    if totals:
        parts.append(f"💰 Ish haqi: <b>{fmt_totals(totals)}</b>")
    return "\n\n".join(parts)


def _money_amount(amount: str | None, currency: str) -> str:
    return f"<b>{fmt_money(Decimal(amount), currency)}</b>" if amount is not None else "<b>?</b>"


def debt_line(
    person: str | None, direction: str | None, amount: str, note: str | None = None, stated: bool = False
) -> str:
    """«📥 Abbosdan olindi — 100 000 won», qarz holati bo'lsa «📤 Abbos sizga qarzdor — 100 000 won»"""
    who = escape(person) if person else "?"
    if direction == DebtDirection.IN:
        text = f"📥 Siz {who}ga qarzdorsiz" if stated else f"📥 {who}dan olindi"
    elif direction == DebtDirection.OUT:
        text = f"📤 {who} sizga qarzdor" if stated else f"📤 {who}ga berildi"
    else:
        text = f"❔ {who}"
    text += f" — {amount}"
    if note:
        text += f" <i>({escape(note)})</i>"
    return text


def _day_mark(item: dict) -> str:
    """Yozuvning o'z sanasi bo'lsa: « · 📅 12.10»"""
    return f" · 📅 {date.fromisoformat(item['date']).strftime('%d.%m')}" if item.get("date") else ""


def _personal_row(p: dict) -> str:
    row = f"• {escape(p['category'])} — {_money_amount(p['amount'], p['currency'])}"
    if p["note"]:
        row += f" <i>({escape(p['note'])})</i>"
    return row + _day_mark(p)


# Telegram xabari 4096 belgigacha: bank tarixida yozuv ko'p bo'lsa, ro'yxat qisqartiriladi
ROWS_BUDGET = 2800
MAX_DUP_ROWS = 5


def _render_money(draft: dict) -> str:
    parts = ["📝 <b>Yangi yozuv</b>"]
    if raw := _short_raw(draft):
        parts.append(raw)
    parts.append(f"📅 Sana: {fmt_date(date.fromisoformat(draft['date']))}")

    budget = ROWS_BUDGET
    for kind, title in ((PersonalKind.EXPENSE, "💸 <b>Xarajat</b>"), (PersonalKind.INCOME, "💰 <b>Daromad</b>")):
        items = [p for p in draft["personal"] if p["kind"] == kind and not p.get("dup")]
        if not items:
            continue
        rows = []
        for p in items:
            row = _personal_row(p)
            if len(row) > budget:
                break
            budget -= len(row) + 1
            rows.append(row)
        if len(rows) < len(items):
            rows.append(f"… yana {len(items) - len(rows)} ta")
        if len(items) > 3 and all(p["amount"] is not None for p in items):
            totals: dict[str, Decimal] = {}
            for p in items:
                totals[p["currency"]] = totals.get(p["currency"], Decimal(0)) + Decimal(p["amount"])
            rows.append(f"Jami ({len(items)} ta): <b>{fmt_totals(totals)}</b>")
        parts.append(title + "\n" + "\n".join(rows))

    if draft["debt"]:
        rows = [
            "• " + debt_line(
                d["person"], d["direction"], _money_amount(d["amount"], d["currency"]), d["note"], d.get("stated", False)
            ) + _day_mark(d)
            for d in draft["debt"]
        ]
        parts.append("🤝 <b>Qarz</b>\n" + "\n".join(rows))

    dups = [p for p in draft["personal"] if p.get("dup")]
    if len(dups) > MAX_DUP_ROWS:
        parts.append(f"🔁 Avval kiritilgan {len(dups)} ta yozuv qayta qo'shilmaydi.")
    elif dups:
        parts.append("🔁 <b>Avval kiritilgan — qayta qo'shilmaydi</b>\n" + "\n".join(_personal_row(p) for p in dups))
    return "\n\n".join(parts)


def _present_money(draft: dict) -> tuple[str, InlineKeyboardMarkup]:
    text = _render_money(draft)
    missing = next_missing(draft)
    if missing is None:
        return text + "\n\n<b>To'g'rimi?</b>", kbs.draft_kb(with_date_buttons=True)

    kind, i = missing
    if kind == "p_amount":
        category = escape(draft["personal"][i]["category"])
        return text + f"\n\n❓ <b>{category}</b> — qancha? Summani yozing yoki ovoz bilan ayting.", kbs.cancel_only_kb()
    debt = draft["debt"][i]
    if kind == "person":
        return text + "\n\n❓ Kim bilan? Ismini yozing.", kbs.cancel_only_kb()
    if kind == "direction":
        return (
            text + f"\n\n❓ <b>{escape(debt['person'])}</b> — pulni oldingizmi yoki berdingizmi?",
            kbs.debt_direction_kb(i, debt["person"]),
        )
    return text + f"\n\n❓ <b>{escape(debt['person'])}</b> — qancha? Summani yozing.", kbs.cancel_only_kb()


CLIENT_KIND_LABELS = {
    ClientKind.SALE: "📦 Mol berildi",
    ClientKind.PAYMENT: "💵 To'lov olindi",
    ClientKind.RETURN: "↩️ Mol qaytarildi",
}


def qty_text(sets: int | None, pieces: int | None) -> str:
    if sets:
        return f"{sets} komplekt ({pieces} dona)" if pieces else f"{sets} komplekt"
    return f"{pieces} dona" if pieces else "? komplekt"


def client_line(c: dict) -> str:
    """«📦 Mol berildi: Maksima — 10 komplekt (50 dona) × 140 000 so'm = 1 400 000 so'm»"""
    label = CLIENT_KIND_LABELS.get(c["kind"], "❔ Mol yoki to'lov?")
    if c["kind"] == ClientKind.PAYMENT:
        return f"{label}: {_money_amount(c['total'], c['currency'])}"
    text = f"{label}: "
    if c["product"]:
        text += f"{escape(c['product'])} — "
    text += qty_text(c["sets"], c["pieces"])
    if c["price"]:
        text += f" × {fmt_money(Decimal(c['price']), c['currency'])}"
        if c["price_unit"] == PriceUnit.PIECE:
            text += " (dona)"
    text += f" = {_money_amount(c['total'], c['currency'])}"
    return text


def _client_who(c: dict) -> str:
    name = c["client_name"] or (f"{c['client_q']}?" if c["client_q"] else "?")
    return f"<b>{escape(name)}</b>" + (" <i>(yangi mijoz)</i>" if c["client_new"] else "")


def _render_client(draft: dict) -> str:
    parts = ["📝 <b>Mijoz bilan oldi-berdi</b>"]
    if raw := _short_raw(draft):
        parts.append(raw)
    parts.append(f"📅 Sana: {fmt_date(date.fromisoformat(draft['date']))}")
    items = draft["client"]
    one_client = len({(c["client_id"], c["client_name"], c["client_q"]) for c in items}) == 1
    if one_client:
        parts.append(f"👤 Mijoz: {_client_who(items[0])}")
        parts.append("\n".join(f"• {client_line(c)}" for c in items))
    else:
        parts.append("\n".join(f"• {_client_who(c)} — {client_line(c)}" for c in items))
    return "\n\n".join(parts)


async def _present_client(session: AsyncSession, draft: dict, user: User) -> tuple[str, InlineKeyboardMarkup]:
    text = _render_client(draft)
    missing = next_missing(draft)
    if missing is None:
        return text + "\n\n<b>To'g'rimi?</b>", kbs.draft_kb(with_date_buttons=True)

    kind, i = missing
    c = draft["client"][i]
    if kind == "client_name":
        return text + "\n\n❓ Qaysi mijoz? Ismini yozing yoki ayting.", kbs.cancel_only_kb()
    if kind == "client":
        acc = user.account_id
        items = [(x.id, x.name) for x in await repo.list_clients(session, acc)]
        aliases = await repo.get_aliases(session, acc, "client")
        options = [(o[0], o[1]) for o in matcher.match(c["client_q"], items, aliases).options] or items[:20]
        return (
            text + f"\n\n❓ «{escape(c['client_q'])}» — qaysi mijoz?",
            kbs.resolve_kb("client", i, options, c["client_q"]),
        )
    who = escape(c["client_name"] or "")
    if kind == "client_kind":
        return text + f"\n\n❓ <b>{who}</b> — mol berdingizmi yoki to'lov oldingizmi?", kbs.client_kind_kb(i)
    if kind == "c_qty":
        return text + "\n\n❓ Nechta komplekt? Sonini yozing yoki ayting.", kbs.cancel_only_kb()
    if kind == "c_price":
        return (
            text + "\n\n❓ Bir komplekt narxi qancha? (yoki jami summani ayting: «jami 1,4 million»)",
            kbs.cancel_only_kb(),
        )
    return text + f"\n\n❓ <b>{who}</b> qancha to'ladi? Summani yozing.", kbs.cancel_only_kb()


async def present(session: AsyncSession, draft: dict, user: User) -> tuple[str, InlineKeyboardMarkup]:
    """Qoralama kartochkasi: yetishmagan narsa bo'lsa so'raydi, aks holda tasdiq so'raydi."""
    if draft["section"] == "client":
        return await _present_client(session, draft, user)
    if draft["section"] == "money":
        return _present_money(draft)

    acc = user.account_id
    text = _render(draft)
    missing = next_missing(draft)

    if missing is None:
        return text + "\n\n<b>To'g'rimi?</b>", kbs.draft_kb(with_date_buttons=True)

    kind, i = missing
    line = draft["lines"][i]

    if kind == "quantity":
        model = escape(line["model_name"] or "")
        return (
            text + f"\n\n❓ <b>{model}</b> dan nechta? Sonini yozing yoki ovoz bilan ayting.",
            kbs.cancel_only_kb(),
        )

    if kind == "worker":
        items = [(w.id, w.name) for w in await repo.list_workers(session, acc)]
        aliases = await repo.get_aliases(session, acc, "worker")
        query = line["worker_q"]
        allow_new = None  # ishchi faqat taklif havolasi orqali qo'shiladi
        label = "ishchi"
    else:
        items = [(m.id, m.name) for m in await repo.list_models(session, acc)]
        aliases = await repo.get_aliases(session, acc, "model")
        query = line["model_q"]
        allow_new = query if (user.is_owner and query) else None
        label = "model"

    options: list[tuple[int, str]] = []
    if query:
        options = [(o[0], o[1]) for o in matcher.match(query, items, aliases).options]
    if not options:
        options = items[:20]

    if not items and not allow_new:
        hint = (
            "Avval «👷 Ishchilar» bo'limida ishchi qo'shing."
            if kind == "worker"
            else "Hali birorta model qo'shilmagan. Egasidan model qo'shishni so'rang."
        )
        return text + f"\n\n⚠️ {hint}", kbs.cancel_only_kb()

    if query:
        question = f"❓ «{escape(query)}» — qaysi {label}?"
    else:
        question = f"❓ Qaysi {label}?"
    return text + f"\n\n{question}", kbs.resolve_kb(kind, i, options, allow_new)
