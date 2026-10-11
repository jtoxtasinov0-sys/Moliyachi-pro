"""Qoralama: LLM ajratgan, lekin hali tasdiqlanmagan yozuv.

Qoralama FSM holatida oddiy dict sifatida saqlanadi:
{
  "section": "work",
  "entry_id": None | int,         # mavjud yozuvni tahrirlashda
  "date": "YYYY-MM-DD",
  "raw_text": "...",
  "created": "ISO vaqt",
  "lines": [{worker_id, worker_name, worker_q, model_id, model_name, model_q,
             quantity, price, currency}],
}
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from html import escape

from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kbs
from bot.db import repo
from bot.db.models import ItemModel, User, WorkEntry
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


def from_extraction(ext: Extraction, raw_text: str, entry_id: int | None = None) -> dict:
    lines = []
    for item in ext.work:
        line = _empty_line()
        line["worker_q"] = item.worker_name
        line["model_q"] = item.model_name
        line["quantity"] = item.quantity if item.quantity and item.quantity > 0 else None
        lines.append(line)
    return {
        "section": "work",
        "entry_id": entry_id,
        "date": _parse_date(ext.date).isoformat(),
        "raw_text": raw_text,
        "created": datetime.now(timezone.utc).isoformat(),
        "lines": lines,
    }


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


def for_llm(draft: dict) -> dict:
    """LLM ga tuzatish uchun yuboriladigan qisqa ko'rinish."""
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
    known = {"models": [m.name for m in await repo.list_models(session)]}
    if user.is_owner:
        known["workers"] = [w.name for w in await repo.list_workers(session)]
    return known


def set_model(line: dict, model: ItemModel) -> None:
    line["model_id"] = model.id
    line["model_name"] = model.name
    line["price"] = str(model.price) if model.price is not None else None
    line["currency"] = model.currency if model.price is not None else None


async def resolve(session: AsyncSession, draft: dict, user: User) -> None:
    """Ismlarni bazadagi yozuvlarga bog'laydi (faqat aniq mos kelganlarini)."""
    workers = await repo.list_workers(session)
    worker_aliases = await repo.get_aliases(session, "worker")
    models = await repo.list_models(session)
    model_aliases = await repo.get_aliases(session, "model")
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


def next_missing(draft: dict) -> tuple[str, int] | None:
    for i, line in enumerate(draft["lines"]):
        if line["worker_id"] is None:
            return "worker", i
        if line["model_id"] is None:
            return "model", i
        if not line["quantity"]:
            return "quantity", i
    return None


def _render(draft: dict) -> str:
    title = (
        f"✏️ <b>Yozuv #{draft['entry_id']} — tahrirlash</b>"
        if draft["entry_id"]
        else "📝 <b>Ish topshirish</b>"
    )
    parts = [title]
    raw = draft.get("raw_text") or ""
    if raw:
        short = raw if len(raw) <= 300 else raw[:300] + "…"
        parts.append(f"🗣 <i>«{escape(short)}»</i>")
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


async def present(session: AsyncSession, draft: dict, user: User) -> tuple[str, InlineKeyboardMarkup]:
    """Qoralama kartochkasi: yetishmagan narsa bo'lsa so'raydi, aks holda tasdiq so'raydi."""
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
        items = [(w.id, w.name) for w in await repo.list_workers(session)]
        aliases = await repo.get_aliases(session, "worker")
        query = line["worker_q"]
        allow_new = None  # ishchi faqat taklif havolasi orqali qo'shiladi
        label = "ishchi"
    else:
        items = [(m.id, m.name) for m in await repo.list_models(session)]
        aliases = await repo.get_aliases(session, "model")
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
