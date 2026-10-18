"""Qoralama: LLM ajratgan, lekin hali tasdiqlanmagan yozuv.

Qoralama FSM holatida oddiy dict sifatida saqlanadi. Ish topshirish:
{
  "section": "work",
  "entry_id": None | int,         # mavjud yozuvni tahrirlashda
  "date": "YYYY-MM-DD",
  "raw_text": "...",
  "created": "ISO vaqt",
  "lines": [{worker_id, worker_name, worker_q, model_id, model_name, model_q,
             job_id, job_name, job_q, job_auto, quantity, price, currency}],
}
job_q — xabarda aytilgan kasb («brsovka»). Aytilmasa va job_auto bo'lsa — ishchining o'z kasbi olinadi.
Narx (price) — model va kasb bo'yicha: kasb narxi, u bo'lmasa modelning umumiy narxi.
date — qatorning o'z sanasi (yozishma skrinshotida har xil kunlar), None — umumiy sana.
Ishchi bilan pul (avans, ish haqi, qaytarilgan pul, hisob holati) — shu qoralamada "pays" (ish bo'lmasa ham):
{
  ..., "pays": [{worker_id, worker_name, worker_q, kind, amount, whole, worker_owes, currency, note, date}],
       "balances": {"worker_id": {valyuta: qoldiq}},  # ishchilarning hozirgi qoldig'i (resolve da)
       "by_owner": bool, "auto_ok": bool,  # egasi yozdimi; ishchining ish yozuvi tasdiqsiz hisobga kiradimi
       "pay_ok": bool,  # ishchining «oldim» / «qaytardim» yozuvi tasdiqsiz qoldiqqa kiradimi
}
kind — PayKind; BALANCE da amount — qoldiq ("0" — hisob yopildi), worker_owes — ishchi qarzdor.
whole — «qolgan hamma pulini berdim»: summa qoldiqdan olinadi.
Model narxini belgilash — "section": "price":
{
  ..., "prices": [{model_id, model_name, model_q, job_id, job_name, job_q,
                   price, currency, cur_said, old}],
}
job_q yo'q — umumiy narx. old — hozirgi narx {price, currency}, bo'lmasa None.
Xom ashyo xaridi — "section": "material":
{
  ..., "materials": [{kind, name, quantity, unit, price, total, currency, supplier, supplier_id, supplier_free}],
}
price — bir birlik (list, kg...) narxi, total — jami; aytilmasa None, narxsiz ham saqlanadi.
supplier — kimdan olingani; ro'yxatdagi ta'minotchi bo'lsa supplier_id (saqlangach unga xabar boradi),
supplier_free — ro'yxatda yo'q, aytilgancha yoziladi. Ikkalasi ham bo'lmasa — qaysi ta'minotchi ekani so'raladi.
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

from bot import jobs as jobs_mod
from bot import keyboards as kbs
from bot.db import repo
from bot.db.models import (
    ClientKind,
    DebtDirection,
    ItemModel,
    PayKind,
    PersonalKind,
    PriceUnit,
    User,
    WorkEntry,
)
from bot.services import matcher
from bot.services.llm import Extraction
from bot.services.utils import fmt_date, fmt_money, fmt_number, fmt_totals, today

DRAFT_TTL = timedelta(minutes=30)


def _empty_line() -> dict:
    return {
        "worker_id": None, "worker_name": None, "worker_q": None,
        "model_id": None, "model_name": None, "model_q": None,
        "job_id": None, "job_name": None, "job_q": None, "job_auto": True,
        "quantity": None, "price": None, "currency": None, "date": None,
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


def is_price(ext: Extraction) -> bool:
    return ext.section == "price" and bool(ext.prices)


def is_material(ext: Extraction) -> bool:
    return ext.section == "material" and bool(ext.materials)


def _name(value: str | None, size: int) -> str | None:
    return (value or "").strip()[:size] or None


def _quantity(value: float | None) -> str | None:
    """«1.5» -> "1.5", «20» -> "20"; 0 yoki aql bovar qilmaydigan son — None."""
    if not value or not 0 < value < 1e9:
        return None
    q = Decimal(str(value)).quantize(Decimal("0.001")).normalize()
    return str(int(q)) if q == q.to_integral_value() else str(q)


def material_total(item: dict) -> None:
    """Jami yoki bir birlik narxini ikkinchisidan hisoblaydi (miqdor bilan)."""
    if not item["quantity"]:
        return
    qty = Decimal(item["quantity"])
    if item["price"] and not item["total"]:
        item["total"] = _amount(float(Decimal(item["price"]) * qty))
    elif item["total"] and not item["price"]:
        item["price"] = _amount(float(Decimal(item["total"]) / qty))


PAY_KINDS = (PayKind.PAY, PayKind.RETURN, PayKind.BALANCE)


def _pay_items(ext: Extraction, default_currency: str, draft_date: str) -> list[dict]:
    items = []
    for p in ext.pays:
        kind = p.kind or None
        balance = kind == PayKind.BALANCE
        value = p.amount or 0
        owes = bool(p.worker_owes) and balance
        if value < 0:
            value, owes = -value, balance
        items.append(
            {
                "worker_id": None,
                "worker_name": None,
                "worker_q": _name(p.worker_name, 100),
                "kind": kind,
                # Hisob holatida summa aytilmasa — hisob yopildi (qoldiq 0)
                "amount": "0" if balance and not value else _amount(value),
                "whole": bool(p.whole) and kind == PayKind.PAY,
                "worker_owes": owes,
                "currency": p.currency or default_currency,
                "note": _name(p.note, 200),
                "date": _item_date(p.date, draft_date),
            }
        )
    return items


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
    if is_material(ext):
        said = {item.currency for item in ext.materials if item.currency}
        if len(said) == 1:
            default_currency = said.pop()
        draft.update(section="material", entry_id=None)
        draft["materials"] = []
        for item in ext.materials:
            m = {
                "kind": _name(item.kind, 50),
                "name": _name(item.name, 100),
                "quantity": _quantity(item.quantity),
                "unit": _name(item.unit, 20),
                "price": _amount(item.price_per_unit),
                "total": _amount(item.total),
                "currency": item.currency or default_currency,
                "supplier": _name(item.supplier, 100),
                "supplier_id": None,
                "supplier_free": False,
            }
            material_total(m)
            draft["materials"].append(m)
        return draft
    if is_price(ext):
        draft.update(section="price", entry_id=None)
        draft["prices"] = [
            {
                "model_id": None, "model_name": None, "model_q": _name(item.model_name, 100),
                "job_id": None, "job_name": None, "job_q": _name(item.job, 50),
                "price": _amount(item.price),
                # Valyuta aytilmasa — modelning hozirgi narxi valyutasi (resolve da), bo'lmasa asosiy valyuta
                "currency": item.currency or default_currency,
                "cur_said": bool(item.currency),
                "old": None,
            }
            for item in ext.prices
        ]
        return draft
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
        line["job_q"] = _name(item.job, 50)
        # Saqlangan yozuvni tahrirlashda kasb ishchiga qarab o'zgarib ketmasin — faqat aytilsa
        line["job_auto"] = entry_id is None
        line["quantity"] = item.quantity if item.quantity and item.quantity > 0 else None
        line["date"] = _item_date(item.date, draft["date"])
        lines.append(line)
    draft["lines"] = lines
    said = {p.currency for p in ext.pays if p.currency}
    draft["pays"] = _pay_items(ext, said.pop() if len(said) == 1 else default_currency, draft["date"])
    return draft


def from_entry(entry: WorkEntry) -> dict:
    line = _empty_line()
    line.update(
        worker_id=entry.worker_id, worker_name=entry.worker.name, worker_q=entry.worker.name,
        model_id=entry.model_id, model_name=entry.model.name, model_q=entry.model.name,
        job_id=entry.job_id, job_name=entry.job.name if entry.job else None,
        job_q=entry.job.name if entry.job else None, job_auto=False,
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
        "pays": [],
    }


def is_stale(draft: dict) -> bool:
    created = datetime.fromisoformat(draft["created"])
    return datetime.now(timezone.utc) - created > DRAFT_TTL


def _num(v: str | None) -> float | None:
    return float(v) if v is not None else None


def for_llm(draft: dict) -> dict:
    """LLM ga tuzatish uchun yuboriladigan qisqa ko'rinish."""
    if draft["section"] == "material":
        return {
            "date": draft["date"],
            "materials": [
                {
                    "kind": m["kind"] or "",
                    "name": m["name"] or "",
                    "quantity": _num(m["quantity"]) or 0,
                    "unit": m["unit"] or "",
                    "price_per_unit": _num(m["price"]) or 0,
                    "total": _num(m["total"]) or 0,
                    "currency": m["currency"],
                    "supplier": m["supplier"] or "",
                }
                for m in draft["materials"]
            ],
        }
    if draft["section"] == "price":
        return {
            "prices": [
                {
                    "model_name": p["model_name"] or p["model_q"],
                    "job": p["job_name"] or p["job_q"],
                    "price": _num(p["price"]),
                    "currency": p["currency"] if p["cur_said"] else None,
                }
                for p in draft["prices"]
            ],
        }
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
                # Ishchining o'z kasbi (aytilmagan) yuborilmaydi: ishchi almashsa, kasb ham almashadi
                "job": (ln["job_name"] or ln["job_q"]) if ln["job_q"] else None,
                "quantity": ln["quantity"],
                "date": ln.get("date") or "",
            }
            for ln in draft["lines"]
        ],
        "pays": [
            {
                "worker_name": p["worker_name"] or p["worker_q"] or "",
                "kind": p["kind"] or "",
                "amount": _num(p["amount"]) or 0,
                "whole": p["whole"],
                "worker_owes": p["worker_owes"],
                "currency": p["currency"],
                "note": p["note"] or "",
                "date": p["date"] or "",
            }
            for p in draft.get("pays", [])
        ],
    }


async def known_names(session: AsyncSession, user: User) -> dict[str, list[str]]:
    acc = user.account_id
    known = {
        "models": [m.name for m in await repo.list_models(session, acc)],
        "jobs": [j.name for j in await repo.list_jobs(session, acc)],
    }
    if user.is_owner:
        known["workers"] = [w.name for w in await repo.list_workers(session, acc)]
        known["categories"] = await repo.personal_categories(session, acc)
        known["people"] = [name for _, name in await repo.debt_people(session, acc)][:60]
        known["clients"] = [c.name for c in await repo.list_clients(session, acc)][:100]
        known["material_kinds"] = await repo.material_kinds(session, acc)
        known["material_names"] = await repo.material_names(session, acc)
        known["suppliers"] = [s.name for s in await repo.list_suppliers(session, acc)][:100]
    return known


def set_model(line: dict, model: ItemModel) -> None:
    line["model_id"] = model.id
    line["model_name"] = model.name


async def price_lines(session: AsyncSession, draft: dict, acc: int) -> None:
    """Har bir qatorning bir dona narxi: model va kasb bo'yicha."""
    models = {m.id: m for m in await repo.list_models(session, acc, active_only=False)}
    for line in draft["lines"]:
        price, currency = await repo.unit_price(session, models.get(line["model_id"]), line["job_id"])
        line["price"] = str(price) if price is not None else None
        line["currency"] = currency


async def resolve(session: AsyncSession, draft: dict, user: User) -> None:
    """Ismlarni bazadagi yozuvlarga bog'laydi (faqat aniq mos kelganlarini)."""
    acc = user.account_id
    if draft["section"] == "price":
        await _resolve_prices(session, draft, acc)
        return
    if draft["section"] == "material":
        # Avval yozilgan tur va nom bo'lsa — o'sha yozilishi («teri» -> «Teri», «shirin gul» -> «Shirin gul»)
        for key, values in (("kind", await repo.material_kinds(session, acc)),
                            ("name", await repo.material_names(session, acc))):
            items = list(enumerate(values))
            for m in draft["materials"]:
                if m[key] and items:
                    res = matcher.match(m[key], items)
                    if res.exact_id is not None:
                        m[key] = values[res.exact_id]
        await _resolve_suppliers(session, draft, acc)
        return
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
    jobs = await repo.list_jobs(session, acc)
    jobs_by_id = {j.id: j for j in jobs}
    job_aliases = await repo.get_aliases(session, acc, "job")
    workers_by_id = {w.id: w for w in workers}
    workers_by_id[user.id] = user

    pays = draft.setdefault("pays", [])
    for item in [*draft["lines"], *pays]:
        if not user.is_owner:
            item["worker_id"], item["worker_name"] = user.id, user.name
        elif item["worker_id"] is None and item["worker_q"]:
            res = matcher.match(item["worker_q"], [(w.id, w.name) for w in workers], worker_aliases)
            if res.exact_id:
                item["worker_id"] = res.exact_id
                item["worker_name"] = workers_by_id[res.exact_id].name
    # «Robiya 15 ta loaf qildi, 500 ming berdim» — ismi aytilmagan qatorlar ham shu ishchiga
    named = {(i["worker_id"], i["worker_name"]) for i in [*draft["lines"], *pays] if i["worker_id"]}
    if len(named) == 1:
        share_worker(draft, *named.pop())

    for line in draft["lines"]:
        if line["model_id"] is None and line["model_q"]:
            res = matcher.match(line["model_q"], [(m.id, m.name) for m in models], model_aliases)
            if res.exact_id:
                set_model(line, models_by_id[res.exact_id])

        if line["job_q"] and line["job_id"] is None:
            if not jobs:  # kasblar yo'q — kasb aytilmagandek
                line["job_q"] = None
            else:
                res = jobs_mod.match(line["job_q"], jobs, job_aliases)
                if res.exact_id:
                    line["job_id"], line["job_name"] = res.exact_id, jobs_by_id[res.exact_id].name
        if not line["job_q"] and line.get("job_auto", True):
            worker = workers_by_id.get(line["worker_id"])
            job = jobs_by_id.get(worker.job_id) if worker else None
            line["job_id"], line["job_name"] = (job.id, job.name) if job else (None, None)

    await price_lines(session, draft, acc)
    await _pay_balances(session, draft, user)


def share_worker(draft: dict, worker_id: int, worker_name: str) -> None:
    """Ismi aytilmagan ish va pul qatorlari — shu ishchiga."""
    for item in [*draft.get("lines", []), *draft.get("pays", [])]:
        if item["worker_id"] is None and not item["worker_q"]:
            item["worker_id"], item["worker_name"] = worker_id, worker_name


def pay_counted(draft: dict, p: dict) -> bool:
    """Pul yozuvi saqlanishi bilan qoldiqqa kiradimi: egasi yozsa — ha; ishchi «oldim» / «qaytardim» desa —
    «Pul yozuvlarini tasdiqlash» o'chiq bo'lsa; hisob holatini ishchi yozsa — faqat egasi tasdiqlagach."""
    if draft.get("by_owner", True):
        return True
    return p["kind"] in (PayKind.PAY, PayKind.RETURN) and draft.get("pay_ok", False)


def dec_str(value: Decimal) -> str:
    return str(int(value)) if value == value.to_integral_value() else str(value)


def project_balances(draft: dict) -> dict[int, tuple[dict[str, Decimal], dict[str, Decimal]]]:
    """Ishchi -> (hozirgi qoldiq, saqlangandan keyingi qoldiq). «Qolgan hammasini berdim» summasi shu yerda
    qo'yiladi (summa aytilmagan bo'lsa)."""
    out: dict[int, tuple[dict[str, Decimal], dict[str, Decimal]]] = {}

    def get(worker_id: int):
        if worker_id not in out:
            before = {c: Decimal(v) for c, v in draft.get("balances", {}).get(str(worker_id), {}).items()}
            out[worker_id] = (before, dict(before))
        return out[worker_id][1]

    pays = draft.get("pays", [])
    if not pays:
        return out
    work_ok = draft.get("by_owner", True) or draft.get("auto_ok", False)
    for ln in draft["lines"]:
        if work_ok and ln["worker_id"] and ln["price"] and ln["quantity"]:
            after = get(ln["worker_id"])
            after[ln["currency"]] = after.get(ln["currency"], Decimal(0)) + Decimal(ln["price"]) * ln["quantity"]
    for p in pays:
        if not p["worker_id"] or p["kind"] not in PAY_KINDS:
            continue
        after = get(p["worker_id"])
        if p["kind"] == PayKind.PAY and p["whole"] and p["amount"] is None:
            due = after.get(p["currency"], Decimal(0))
            if due > 0:
                p["amount"] = dec_str(due)
        if p["amount"] is None or not pay_counted(draft, p):
            continue
        value = Decimal(p["amount"])
        if p["kind"] == PayKind.BALANCE:
            after.clear()
            if value:
                after[p["currency"]] = -value if p["worker_owes"] else value
        else:
            sign = -1 if p["kind"] == PayKind.PAY else 1
            after[p["currency"]] = after.get(p["currency"], Decimal(0)) + sign * value
    return out


async def _pay_balances(session: AsyncSession, draft: dict, user: User) -> None:
    """Pul qatoridagi ishchilarning hozirgi qoldig'i (qoralamada «900 000 → 400 000» ko'rsatish uchun)."""
    pays = draft.get("pays", [])
    ids = sorted({p["worker_id"] for p in pays if p["worker_id"]})
    acc = user.account_id
    balances = await repo.worker_balances(session, acc, ids) if ids else {}
    draft["balances"] = {
        str(w): {c: str(v) for c, v in balances[w].balance.items()} if w in balances else {} for w in ids
    }
    draft["by_owner"] = user.is_owner
    draft["auto_ok"] = user.is_owner or not await repo.require_approval(session, acc)
    draft["pay_ok"] = user.is_owner or not await repo.require_pay_approval(session, acc)
    project_balances(draft)


async def _resolve_prices(session: AsyncSession, draft: dict, acc: int) -> None:
    """Model va kasbni topadi, hozirgi narxini (old) va valyutasini qo'yadi."""
    models = await repo.list_models(session, acc)
    models_by_id = {m.id: m for m in models}
    model_aliases = await repo.get_aliases(session, acc, "model")
    jobs = await repo.list_jobs(session, acc)
    jobs_by_id = {j.id: j for j in jobs}
    job_aliases = await repo.get_aliases(session, acc, "job")
    default_currency = (await repo.enabled_currencies(session, acc))[0]

    for p in draft["prices"]:
        if p["model_id"] is None and p["model_q"]:
            res = matcher.match(p["model_q"], [(m.id, m.name) for m in models], model_aliases)
            if res.exact_id:
                p["model_id"], p["model_name"] = res.exact_id, models_by_id[res.exact_id].name
        if p["job_q"] and p["job_id"] is None:
            res = jobs_mod.match(p["job_q"], jobs, job_aliases)
            if res.exact_id:
                p["job_id"], p["job_name"] = res.exact_id, jobs_by_id[res.exact_id].name

    prices = await repo.model_prices(session, [p["model_id"] for p in draft["prices"] if p["model_id"]])
    for p in draft["prices"]:
        model = models_by_id.get(p["model_id"])
        old = None
        if model is not None and p["job_id"]:
            row = prices.get(model.id, {}).get(p["job_id"])
            old = (row.price, row.currency) if row else None
        elif model is not None and not p["job_q"] and model.price is not None:
            old = (model.price, model.currency)
        p["old"] = {"price": str(old[0]), "currency": old[1]} if old else None
        if not p["cur_said"]:
            if old:
                p["currency"] = old[1]
            elif model is not None and model.price is not None:
                p["currency"] = model.currency
            else:
                p["currency"] = default_currency


async def _resolve_suppliers(session: AsyncSession, draft: dict, acc: int) -> None:
    """Aniq mos kelsa — ro'yxatdagi ta'minotchi, o'xshashi bo'lmasa — aytilgancha, aks holda so'raladi."""
    items = [(s.id, s.name) for s in await repo.list_suppliers(session, acc)]
    aliases = await repo.get_aliases(session, acc, "supplier")
    names = dict(items)
    for m in draft["materials"]:
        if m["supplier_id"] is not None or m["supplier_free"] or not m["supplier"]:
            continue
        res = matcher.match(m["supplier"], items, aliases)
        if res.exact_id is not None:
            m["supplier_id"], m["supplier"] = res.exact_id, names[res.exact_id]
        elif not res.options:
            m["supplier_free"] = True


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
    if draft["section"] == "material":
        for i, m in enumerate(draft["materials"]):
            if not m["kind"]:
                return "m_kind", i
            if m["supplier"] and m["supplier_id"] is None and not m["supplier_free"]:
                return "m_supplier", i
        return None
    if draft["section"] == "price":
        for i, p in enumerate(draft["prices"]):
            if p["model_id"] is None:
                return "pmodel", i
            if p["job_q"] and p["job_id"] is None:
                return "pjob", i
            if p["price"] is None:
                return "pr_price", i
        return None
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
        if line["job_q"] and line["job_id"] is None:
            return "job", i
        if not line["quantity"]:
            return "quantity", i
    for i, p in enumerate(draft.get("pays", [])):
        if p["worker_id"] is None:
            return "pworker", i
        if p["kind"] not in PAY_KINDS:
            return "pkind", i
        if p["amount"] is None:
            return "pamount", i
    return None


def short_raw(draft: dict) -> str | None:
    raw = draft.get("raw_text") or ""
    if not raw:
        return None
    short = raw if len(raw) <= 300 else raw[:300] + "…"
    if draft.get("source") == "image":
        return f"<i>{escape(short)}</i>"
    return f"🗣 <i>«{escape(short)}»</i>"


def _render(draft: dict, owner: bool = True) -> str:
    lines = draft["lines"]
    pays = draft.get("pays", [])
    if draft["entry_id"]:
        title = f"✏️ <b>Yozuv #{draft['entry_id']} — tahrirlash</b>"
    elif pays and lines:
        title = "📝 <b>Ish va pul</b>"
    elif pays:
        title = "💵 <b>Ishchi bilan hisob-kitob</b>" if owner else "💵 <b>Hisob-kitob</b>"
    else:
        title = "📝 <b>Ish topshirish</b>"
    parts = [title]
    if raw := short_raw(draft):
        parts.append(raw)
    parts.append(f"📅 Sana: {fmt_date(date.fromisoformat(draft['date']))}")

    worker_ids = {item["worker_id"] for item in [*lines, *pays]}
    one_worker = len(worker_ids) == 1 and None not in worker_ids
    if one_worker and (owner or lines):
        parts.append(f"👷 Ishchi: <b>{escape((lines or pays)[0]['worker_name'])}</b>")

    totals: dict[str, Decimal] = {}
    rows = []
    for ln in lines:
        model = escape(ln["model_name"] or (f"{ln['model_q']}?" if ln["model_q"] else "?"))
        qty = f"{ln['quantity']} ta" if ln["quantity"] else "? ta"
        job = ln["job_name"] or (f"{ln['job_q']}?" if ln["job_q"] else None)
        if job:
            qty += f" · {escape(job)}"
        row = f"• {model} — {qty}"
        if not one_worker:
            worker = escape(ln["worker_name"] or (f"{ln['worker_q']}?" if ln["worker_q"] else "?"))
            row = f"• {worker}: {model} — {qty}"
        if ln["price"] and ln["quantity"]:
            pay = Decimal(ln["price"]) * ln["quantity"]
            totals[ln["currency"]] = totals.get(ln["currency"], Decimal(0)) + pay
            row += f" × {fmt_money(Decimal(ln['price']), ln['currency'])}"
        rows.append(row + _day_mark(ln))
    if rows:
        parts.append("\n".join(rows))
    if totals:
        parts.append(f"💰 Ish haqi: <b>{fmt_totals(totals)}</b>")

    if pays:
        pay_rows = []
        for p in pays:
            # Ishchi hali aniqlanmagan bo'lsa — qisqa ko'rinish va aytilgan ism so'roq bilan
            who = p["worker_name"] if owner else None
            amount = _money_amount(p["amount"], p["currency"])
            row = "• " + pay_line(p["kind"], amount, who, p["worker_owes"], p["amount"] == "0", owner)
            if owner and not p["worker_id"]:
                row += f" · 👷 {escape(p['worker_q'])}?" if p["worker_q"] else " · 👷 ?"
            if p["note"]:
                row += f" <i>({escape(p['note'])})</i>"
            if p["amount"] is not None and p["kind"] in PAY_KINDS and not pay_counted(draft, p):
                row += " · ⏳ egasi tasdiqlagach"
            pay_rows.append(row + _day_mark(p))
        parts.append("\n".join(pay_rows))
        names = {i["worker_id"]: i["worker_name"] for i in [*lines, *pays] if i["worker_id"]}
        projected = []
        for worker_id, (before, after) in project_balances(draft).items():
            label = f"🧾 {escape(names.get(worker_id) or '')} — qoldiq" if owner and not one_worker else "🧾 Qoldiq"
            if before == after:
                projected.append(f"{label}: <b>{signed_hint(after)}</b>")
            else:
                projected.append(f"{label}: {fmt_signed(before)} → <b>{signed_hint(after)}</b>")
        if projected:
            parts.append("\n".join(projected))
    return "\n\n".join(parts)


def fmt_signed(totals: dict[str, Decimal]) -> str:
    """Qoldiq: {"UZS": 400000} -> «400 000 so'm», manfiy — «−200 000 so'm», bo'sh — «0»."""
    parts = [("−" if v < 0 else "") + fmt_money(abs(v), c) for c, v in totals.items() if v]
    return " + ".join(parts) if parts else "0"


def signed_hint(totals: dict[str, Decimal]) -> str:
    """fmt_signed, manfiy bo'lsa — «(avans)»: ishchi ishlaganidan ko'p olgan."""
    return fmt_signed(totals) + (" <i>(avans)</i>" if any(v < 0 for v in totals.values()) else "")


def balance_text(balance: dict[str, Decimal], name: str | None = None) -> str:
    """Ishchi qoldig'i. name — egasiga ko'rsatiladi («Robiyaga berishingiz kerak»), None — ishchining o'ziga."""
    due = {c: v for c, v in balance.items() if v > 0}
    over = {c: -v for c, v in balance.items() if v < 0}
    who = escape(name) if name else None
    rows = []
    if due:
        label = f"💰 {who}ga berishingiz kerak" if who else "💰 Olishingiz kerak"
        rows.append(f"{label}: <b>{fmt_totals(due)}</b>")
    if over:
        label = f"📌 {who} ortiqcha olgan (avans)" if who else "📌 Ortiqcha olgansiz (avans)"
        rows.append(f"{label}: <b>{fmt_totals(over)}</b>")
    if not rows:
        rows.append(f"✅ {who} bilan hisob yopilgan" if who else "✅ Hisob yopilgan")
    return "\n".join(rows)


def pay_line(
    kind: str | None,
    amount: str,
    who: str | None = None,
    worker_owes: bool = False,
    zero: bool = False,
    owner: bool = True,
) -> str:
    """Ishchi bilan pul harakati. owner — egasiga ko'rsatiladi (who — ishchi ismi, bo'lmasa qisqa:
    «Berildi»), aks holda — ishchining o'ziga («Oldingiz»)."""
    name = escape(who) if who else None
    if kind == PayKind.BALANCE:
        if zero:
            text = f"🧾 {name} bilan hisob yopildi" if name else "🧾 Hisob yopildi"
            return text + " (qoldiq 0)"
        if owner:
            if worker_owes:
                text = f"🧾 Hisob: {name} sizga qarzdor" if name else "🧾 Hisob: ishchi sizga qarzdor"
            else:
                text = f"🧾 Hisob: siz {name}ga qarzdorsiz" if name else "🧾 Hisob: siz qarzdorsiz"
        else:
            text = "🧾 Hisob: siz qarzdorsiz" if worker_owes else "🧾 Hisob: boshlig'ingiz sizga qarzdor"
    elif kind == PayKind.PAY:
        text = (f"💵 {name}ga berildi" if name else "💵 Berildi") if owner else "💵 Oldingiz"
    elif kind == PayKind.RETURN:
        text = (f"↩️ {name} qaytardi" if name else "↩️ Qaytardi") if owner else "↩️ Qaytardingiz"
    else:
        text = f"❔ {name}" if name else "❔ Pul"
    return f"{text} — {amount}"


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
    if raw := short_raw(draft):
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
        if draft.get("source") == "image":
            return (
                text + "\n\n<b>To'g'rimi?</b> «✅ Tasdiqlash» ni bossangiz hisobga qo'shiladi.",
                kbs.draft_kb(with_date_buttons=True, save_text="✅ Tasdiqlash"),
            )
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


def material_line(
    kind: str | None,
    name: str | None,
    quantity: str | Decimal | None,
    unit: str | None,
    price: str | Decimal | None,
    total: str | Decimal | None,
    currency: str,
) -> str:
    """«<b>Teri</b> «Shirin gul» — 1 list × 10 000 000 so'm = <b>10 000 000 so'm</b>»"""
    text = f"<b>{escape(kind or '?')}</b>"
    if name:
        text += f" «{escape(name)}»"
    qty = f"{fmt_number(Decimal(quantity))} {escape(unit or '')}".strip() if quantity else None
    if qty:
        text += f" — {qty}"
    if total:
        if qty and price:
            text += f" × {fmt_money(Decimal(price), currency)}"
        text += " = " if qty else " — "
        text += f"<b>{fmt_money(Decimal(total), currency)}</b>"
    elif price:
        text += f" × {fmt_money(Decimal(price), currency)}"
    else:
        text += " · <i>narxi aytilmagan</i>"
    return text


def _render_material(draft: dict) -> str:
    parts = ["🧱 <b>Xom ashyo</b>"]
    if raw := short_raw(draft):
        parts.append(raw)
    parts.append(f"📅 Sana: {fmt_date(date.fromisoformat(draft['date']))}")
    rows = []
    totals: dict[str, Decimal] = {}
    for m in draft["materials"]:
        row = "• " + material_line(
            m["kind"], m["name"], m["quantity"], m["unit"], m["price"], m["total"], m["currency"]
        )
        if m["supplier"]:
            row += f"\n   🏪 {escape(m['supplier'])}" + ("" if m["supplier_id"] or m["supplier_free"] else "?")
        rows.append(row)
        if m["total"]:
            totals[m["currency"]] = totals.get(m["currency"], Decimal(0)) + Decimal(m["total"])
    parts.append("\n".join(rows))
    if len(draft["materials"]) > 1 and totals:
        parts.append(f"Jami: <b>{fmt_totals(totals)}</b>")
    return "\n\n".join(parts)


async def _present_material(session: AsyncSession, draft: dict, user: User) -> tuple[str, InlineKeyboardMarkup]:
    text = _render_material(draft)
    missing = next_missing(draft)
    if missing is None:
        return text + "\n\n<b>To'g'rimi?</b>", kbs.draft_kb(with_date_buttons=True)
    kind, i = missing
    if kind == "m_supplier":
        query = draft["materials"][i]["supplier"]
        acc = user.account_id
        items = [(s.id, s.name) for s in await repo.list_suppliers(session, acc)]
        aliases = await repo.get_aliases(session, acc, "supplier")
        options = [(o[0], o[1]) for o in matcher.match(query, items, aliases).options] or items[:20]
        return (
            text + f"\n\n❓ «{escape(query)}» — qaysi ta'minotchi? Ro'yxatdagisiga undan olinganda xabar boradi.",
            kbs.resolve_kb("supplier", i, options, query, new_label="✍️ Ro'yxatda yo'q"),
        )
    return text + "\n\n❓ Qanday xom ashyo? Masalan: <i>teri</i>, <i>kley</i>, <i>padosh</i>.", kbs.cancel_only_kb()


async def _job_options(session: AsyncSession, acc: int, query: str) -> list[tuple[int, str]]:
    """Kasblar kam — hammasi tugma bo'lib chiqadi, o'xshashlari birinchi."""
    jobs = await repo.list_jobs(session, acc)
    saved = await repo.get_aliases(session, acc, "job")
    first = [o[0] for o in jobs_mod.match(query, jobs, saved).options]
    jobs.sort(key=lambda j: first.index(j.id) if j.id in first else len(first))
    return [(j.id, j.name) for j in jobs]


def _render_price(draft: dict) -> str:
    parts = ["💰 <b>Ish haqi narxi</b> (bir dona uchun)"]
    if raw := short_raw(draft):
        parts.append(raw)
    rows = []
    for p in draft["prices"]:
        model = escape(p["model_name"] or (f"{p['model_q']}?" if p["model_q"] else "?"))
        job = escape(p["job_name"] or (f"{p['job_q']}?" if p["job_q"] else "umumiy narx"))
        row = f"• {model} — {job}: {_money_amount(p['price'], p['currency'])}"
        old = p.get("old")
        if old and p["price"] and (Decimal(old["price"]), old["currency"]) != (Decimal(p["price"]), p["currency"]):
            row += f" <i>(avval {fmt_money(Decimal(old['price']), old['currency'])})</i>"
        rows.append(row)
    parts.append("\n".join(rows))
    if any(not p["job_q"] for p in draft["prices"]):
        parts.append("ℹ️ Umumiy narx — kasbining narxi yozilmagan ishlarga olinadi.")
    return "\n\n".join(parts)


async def _present_price(session: AsyncSession, draft: dict, user: User) -> tuple[str, InlineKeyboardMarkup]:
    text = _render_price(draft)
    missing = next_missing(draft)
    if missing is None:
        return text + "\n\n<b>To'g'rimi?</b> Yangi narx shu paytdan keyingi yozuvlarga qo'llanadi.", kbs.draft_kb()

    kind, i = missing
    p = draft["prices"][i]
    acc = user.account_id
    if kind == "pmodel":
        items = [(m.id, m.name) for m in await repo.list_models(session, acc)]
        if p["model_q"]:
            aliases = await repo.get_aliases(session, acc, "model")
            options = [(o[0], o[1]) for o in matcher.match(p["model_q"], items, aliases).options] or items[:20]
            return (
                text + f"\n\n❓ «{escape(p['model_q'])}» — qaysi model?",
                kbs.resolve_kb("pmodel", i, options, p["model_q"]),
            )
        if not items:
            return text + "\n\n❓ Qaysi model? Nomini yozing yoki ayting.", kbs.cancel_only_kb()
        return text + "\n\n❓ Qaysi model? Tanlang yoki nomini ayting.", kbs.resolve_kb("pmodel", i, items[:20], None)
    if kind == "pjob":
        return (
            text + f"\n\n❓ «{escape(p['job_q'])}» — qaysi kasb?",
            kbs.resolve_kb("pjob", i, await _job_options(session, acc, p["job_q"]), p["job_q"]),
        )
    who = escape(p["model_name"] or "")
    job = escape(p["job_name"] or "umumiy narx")
    return text + f"\n\n❓ <b>{who} — {job}</b>: bir donasi qancha? Yozing yoki ayting.", kbs.cancel_only_kb()


async def _present_pay(
    session: AsyncSession, draft: dict, user: User, text: str, kind: str, i: int
) -> tuple[str, InlineKeyboardMarkup]:
    p = draft["pays"][i]
    if kind == "pworker":
        acc = user.account_id
        items = [(w.id, w.name) for w in await repo.list_workers(session, acc)]
        # Faqat pul bo'lsa — bu ishchi emas, oddiy qarz bo'lishi mumkin
        as_debt = not draft["lines"] and bool(p["worker_q"])
        if not items and not as_debt:
            return text + "\n\n⚠️ Avval «👷 Ishchilar» bo'limida ishchi qo'shing.", kbs.cancel_only_kb()
        options = []
        if p["worker_q"]:
            aliases = await repo.get_aliases(session, acc, "worker")
            options = [(o[0], o[1]) for o in matcher.match(p["worker_q"], items, aliases).options]
        question = f"❓ «{escape(p['worker_q'])}» — qaysi ishchi?" if p["worker_q"] else "❓ Qaysi ishchi?"
        return text + f"\n\n{question}", kbs.pay_worker_kb(i, options or items[:20], as_debt)
    who = escape(p["worker_name"] or "")
    if kind == "pkind":
        question = (
            f"❓ <b>{who}</b> — pul berdingizmi yoki u qaytardimi?" if user.is_owner
            else "❓ Pulni oldingizmi yoki qaytardingizmi?"
        )
        return text + f"\n\n{question}", kbs.pay_kind_kb(i, user.is_owner)
    question = f"❓ <b>{who}</b> — qancha? Summani yozing yoki ayting." if user.is_owner else "❓ Qancha? Summani yozing yoki ayting."
    return text + f"\n\n{question}", kbs.cancel_only_kb()


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
    if raw := short_raw(draft):
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
    if draft["section"] == "material":
        return await _present_material(session, draft, user)
    if draft["section"] == "price":
        return await _present_price(session, draft, user)
    if draft["section"] == "client":
        return await _present_client(session, draft, user)
    if draft["section"] == "money":
        return _present_money(draft)

    acc = user.account_id
    text = _render(draft, user.is_owner)
    missing = next_missing(draft)

    if missing is None:
        return text + "\n\n<b>To'g'rimi?</b>", kbs.draft_kb(with_date_buttons=True)

    kind, i = missing
    if kind in ("pworker", "pkind", "pamount"):
        return await _present_pay(session, draft, user, text, kind, i)
    line = draft["lines"][i]

    if kind == "quantity":
        model = escape(line["model_name"] or "")
        return (
            text + f"\n\n❓ <b>{model}</b> dan nechta? Sonini yozing yoki ovoz bilan ayting.",
            kbs.cancel_only_kb(),
        )

    if kind == "job":
        options = await _job_options(session, acc, line["job_q"])
        return (
            text + f"\n\n❓ «{escape(line['job_q'])}» — qaysi kasb?",
            kbs.resolve_kb("job", i, options, line["job_q"] if user.is_owner else None),
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
