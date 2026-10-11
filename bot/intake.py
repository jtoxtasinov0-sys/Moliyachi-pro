"""Ovoz/matndan yozuvgacha: tahlil, qoralamani to'ldirish va saqlash.

Bot chati (handlers/input.py) ham, Mini App (web/api.py) ham shu funksiyalardan foydalanadi:
qoralama har ikkisida bir xil dict, faqat saqlanadigan joyi boshqa (FSM yoki xotira).
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from html import escape

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from bot import drafts
from bot import keyboards as kbs
from bot.db import repo
from bot.db.models import EntryStatus, User
from bot.services import llm
from bot.services.utils import today


class IntakeError(Exception):
    """Foydalanuvchiga ko'rsatiladigan xabar. drop=True — qoralama endi yaroqsiz."""

    def __init__(self, message: str, drop: bool = False):
        super().__init__(message)
        self.message = message
        self.drop = drop


async def analyze(session: AsyncSession, user: User, text: str, draft: dict | None) -> dict:
    """Matndan yangi qoralama. draft — tuzatilayotgan (yoki to'ldirilayotgan) qoralama, bo'lmasa None."""
    correcting = draft is not None and not drafts.is_stale(draft)
    known = await drafts.known_names(session, user)
    worker_name = None if user.is_owner else user.name
    try:
        if correcting:
            ext = await llm.correct(drafts.for_llm(draft), text, today(), known, worker_name)
        else:
            ext = await llm.extract(text, today(), known, worker_name)
    except llm.LlmError:
        raise IntakeError("⚠️ Xabarni tahlil qilib bo'lmadi. Birozdan keyin qayta urinib ko'ring.")

    if ext.section == "client" and user.is_owner:
        raise IntakeError(
            f"🗣 <i>«{escape(text)}»</i>\n\nBu mijozlar bo'limiga tegishli — u keyingi bosqichda qo'shiladi."
        )

    money = user.is_owner and drafts.is_money(ext)
    if not money and (ext.section != "work" or not ext.work):
        example = (
            "«bozorga 50 ming won» yoki «A-12 modeldan 50 ta»"
            if user.is_owner
            else "«A-12 modeldan 50 ta topshirdim»"
        )
        hint = ext.question or f"Tushunmadim. Masalan: {example}."
        raise IntakeError(f"🗣 <i>«{escape(text)}»</i>\n\n🤔 {escape(hint)}")

    raw = f"{draft['raw_text']} → {text}" if correcting and draft.get("raw_text") else text
    entry_id = draft["entry_id"] if correcting else None
    currency = (await repo.enabled_currencies(session, user.account_id))[0]
    new_draft = drafts.from_extraction(ext, raw, entry_id=entry_id, default_currency=currency)
    if entry_id and (new_draft["section"] != "work" or len(new_draft["lines"]) != 1):
        raise IntakeError("Tahrirlashda faqat bitta yozuv bo'lishi kerak. Qaytadan ayting.")

    await drafts.resolve(session, new_draft, user)
    return new_draft


async def choose(session: AsyncSession, user: User, draft: dict, cb: kbs.ResolveCb) -> bool:
    """Ishchi yoki modelni tanlash tugmasi. False — o'zgarish yo'q."""
    if draft["section"] != "work" or cb.line >= len(draft["lines"]):
        return False
    line = draft["lines"][cb.line]
    acc = user.account_id

    if cb.kind == "worker" and user.is_owner:
        workers = {w.id: w for w in await repo.list_workers(session, acc)}
        worker = workers.get(cb.item_id)
        if worker is None:
            raise IntakeError("Ishchi topilmadi.")
        if line["worker_q"] and line["worker_q"].lower() != worker.name.lower():
            await repo.save_alias(session, acc, "worker", worker.id, line["worker_q"])
        line["worker_id"], line["worker_name"] = worker.id, worker.name
        return True

    if cb.kind == "model":
        if cb.item_id == 0:
            if not user.is_owner or not line["model_q"]:
                return False
            model = await repo.get_model_by_name(session, acc, line["model_q"]) or await repo.create_model(
                session, acc, line["model_q"].strip()
            )
        else:
            models = {m.id: m for m in await repo.list_models(session, acc)}
            model = models.get(cb.item_id)
            if model is None:
                raise IntakeError("Model topilmadi.")
            if line["model_q"] and line["model_q"].lower() != model.name.lower():
                await repo.save_alias(session, acc, "model", model.id, line["model_q"])
        drafts.set_model(line, model)
        return True
    return False


def set_direction(draft: dict, cb: kbs.DebtDirCb) -> bool:
    if draft["section"] != "money" or cb.line >= len(draft["debt"]) or cb.direction not in ("in", "out"):
        return False
    draft["debt"][cb.line]["direction"] = cb.direction
    return True


def set_date(draft: dict, action: str) -> bool:
    """«Bugun» / «Kecha» tugmasi. False — sana o'zgarmadi."""
    d = today() if action == "today" else today() - timedelta(days=1)
    if draft["date"] == d.isoformat():
        return False
    draft["date"] = d.isoformat()
    return True


@dataclass
class Saved:
    header: str
    # Har bir saqlangan yozuv kartochkasi va uning tugmalari
    cards: list[tuple[str, InlineKeyboardMarkup]] = field(default_factory=list)


async def save(session: AsyncSession, bot: Bot, user: User, draft: dict) -> Saved:
    """Qoralamani bazaga yozadi. Ishchi yozuvi tasdiq kutsa — egaga xabar yuboriladi."""
    from bot.handlers.entries import entry_text, notify_owner_pending  # aylanma importdan qochish uchun

    if drafts.next_missing(draft) is not None:
        raise IntakeError("Avval yetishmagan ma'lumotni to'ldiring.")
    if draft["section"] == "money":
        if not user.is_owner:
            raise IntakeError("Ruxsat yo'q.", drop=True)
        return await _save_money(session, user, draft)

    acc = user.account_id
    needs_approval = not user.is_owner and await repo.require_approval(session, acc)
    work_date = date.fromisoformat(draft["date"])

    if draft["entry_id"]:
        entry = await repo.get_work_entry(session, acc, draft["entry_id"])
        if entry is None or (not user.is_owner and entry.worker_id != user.id):
            raise IntakeError("⚠️ Yozuv topilmadi (o'chirilgan bo'lishi mumkin).", drop=True)
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

    if needs_approval:
        header += "\n⏳ Egasining tasdig'ini kutmoqda."
        for entry in saved:
            await notify_owner_pending(bot, session, entry)
    last = len(saved) - 1
    cards = [(entry_text(e), kbs.entry_kb(e, user, app=i == last)) for i, e in enumerate(saved)]
    return Saved(header, cards)


async def _save_money(session: AsyncSession, user: User, draft: dict) -> Saved:
    from bot.handlers.money import debt_text, person_balance, personal_text

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

    header = "✅ <b>Saqlandi</b>"
    balance = [line for person in people for line in await person_balance(session, user.id, person)]
    if balance:
        header += "\n\n" + "\n".join(balance)
    last = len(cards) - 1
    return Saved(
        header,
        [(text, kbs.money_entry_kb(kind, entry_id, app=i == last)) for i, (text, kind, entry_id) in enumerate(cards)],
    )
