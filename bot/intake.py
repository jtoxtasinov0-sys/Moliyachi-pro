"""Ovoz, matn yoki rasmdan yozuvgacha: tahlil, qoralamani to'ldirish va saqlash.

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

from bot import advisor, billing, drafts
from bot import keyboards as kbs
from bot.db import repo
from bot.db.models import ClientKind, EntryStatus, User
from bot.services import llm
from bot.services.utils import today


CLIENT_KINDS = (ClientKind.SALE, ClientKind.PAYMENT, ClientKind.RETURN)


class IntakeError(Exception):
    """Foydalanuvchiga ko'rsatiladigan xabar. drop=True — qoralama endi yaroqsiz."""

    def __init__(self, message: str, drop: bool = False):
        super().__init__(message)
        self.message = message
        self.drop = drop


async def _context(session: AsyncSession, user: User) -> tuple[dict[str, list[str]], str | None, int, str]:
    known = await drafts.known_names(session, user)
    worker_name = None if user.is_owner else user.name
    set_size = await repo.set_size(session, user.account_id)
    info = f"Bir komplekt = {set_size} dona (har razmerdan bittadan)." if user.is_owner else ""
    return known, worker_name, set_size, info


async def analyze(session: AsyncSession, user: User, text: str, draft: dict | None) -> dict:
    """Matndan yangi qoralama. draft — tuzatilayotgan (yoki to'ldirilayotgan) qoralama, bo'lmasa None."""
    correcting = draft is not None and not drafts.is_stale(draft)
    known, worker_name, set_size, info = await _context(session, user)
    try:
        async with billing.metered(session, user, requests=1):
            if correcting:
                ext = await llm.correct(drafts.for_llm(draft), text, today(), known, worker_name, info)
            else:
                ext = await llm.extract(text, today(), known, worker_name, info)
    except billing.QuotaError as e:
        raise IntakeError(e.message)
    except llm.LlmError:
        raise IntakeError("⚠️ Xabarni tahlil qilib bo'lmadi. Birozdan keyin qayta urinib ko'ring.")

    raw = f"{draft['raw_text']} → {text}" if correcting and draft.get("raw_text") else text
    return await _build(
        session, user, ext, raw, set_size,
        said=f"🗣 <i>«{escape(text)}»</i>",
        entry_id=draft["entry_id"] if correcting else None,
        source=draft.get("source") if correcting else None,
    )


MAX_IMAGES = 5


async def analyze_images(session: AsyncSession, user: User, images: list[tuple[bytes, str]], caption: str = "") -> dict:
    """Bank skrinshoti, chek yoki daftar rasmidan yangi qoralama. images — (rasm, media turi)."""
    caption = caption.strip()[:500]
    known, worker_name, set_size, info = await _context(session, user)
    images = images[:MAX_IMAGES]
    try:
        async with billing.metered(session, user, images=len(images)):
            ext = await llm.extract_images(images, today(), known, worker_name, info, caption)
    except billing.QuotaError as e:
        raise IntakeError(e.message)
    except llm.LlmError:
        raise IntakeError("⚠️ Rasmni o'qib bo'lmadi. Birozdan keyin qayta yuboring yoki matn bilan yozing.")
    label = "🖼 Rasm" if len(images) == 1 else f"🖼 {len(images)} ta rasm"
    raw = f"{label}: {caption}" if caption else label
    return await _build(session, user, ext, raw, set_size, said=f"<i>{escape(raw)}</i>", source="image")


async def _build(
    session: AsyncSession,
    user: User,
    ext: llm.Extraction,
    raw: str,
    set_size: int,
    said: str,
    entry_id: int | None = None,
    source: str | None = None,
) -> dict:
    special = user.is_owner and (drafts.is_money(ext) or drafts.is_client(ext))
    if not special and (ext.section != "work" or not ext.work):
        example = (
            "«bozorga 50 ming won», «Tohirga 10 komplekt maksima berdim, 140 mingdan» yoki «A-12 modeldan 50 ta»"
            if user.is_owner
            else "«A-12 modeldan 50 ta topshirdim»"
        )
        hint = ext.question or f"Tushunmadim. Masalan: {example}."
        raise IntakeError(f"{said}\n\n🤔 {escape(hint)}")

    currency = (await repo.enabled_currencies(session, user.account_id))[0]
    new_draft = drafts.from_extraction(ext, raw, entry_id=entry_id, default_currency=currency, set_size=set_size)
    if entry_id and (new_draft["section"] != "work" or len(new_draft["lines"]) != 1):
        raise IntakeError("Tahrirlashda faqat bitta yozuv bo'lishi kerak. Qaytadan ayting.")
    if source:
        new_draft["source"] = source

    await drafts.resolve(session, new_draft, user)
    if source == "image" and new_draft["section"] == "money":
        await drafts.mark_duplicates(session, new_draft, user.account_id)
        if not new_draft["debt"] and all(p["dup"] for p in new_draft["personal"]):
            raise IntakeError(f"{said}\n\n🔁 Bu rasmdagi yozuvlarning hammasi avval kiritilgan — qayta qo'shilmadi.")
    return new_draft


async def autosave(session: AsyncSession, user: User, draft: dict) -> bool:
    """Rasmdan o'qilgan xarajat/daromad va qarz to'liq bo'lsa — «✅ Tasdiqlash» bosilmasdan saqlanadimi."""
    return (
        user.is_owner
        and draft.get("source") == "image"
        and draft["section"] == "money"
        and drafts.next_missing(draft) is None
        and await repo.auto_save_images(session, user.id)
    )


async def choose(session: AsyncSession, user: User, draft: dict, cb: kbs.ResolveCb) -> bool:
    """Ishchi, model yoki mijozni tanlash tugmasi. False — o'zgarish yo'q."""
    if draft["section"] == "client":
        return await _choose_client(session, user, draft, cb)
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


async def _choose_client(session: AsyncSession, user: User, draft: dict, cb: kbs.ResolveCb) -> bool:
    if cb.kind != "client" or not user.is_owner or cb.line >= len(draft["client"]):
        return False
    item = draft["client"][cb.line]
    if cb.item_id == 0:
        if not item["client_q"]:
            return False
        item.update(client_id=None, client_new=True, client_name=item["client_q"])
        return True
    client = await repo.get_client(session, user.account_id, cb.item_id)
    if client is None:
        raise IntakeError("Mijoz topilmadi.")
    if item["client_q"] and item["client_q"].lower() != client.name.lower():
        await repo.save_alias(session, user.account_id, "client", client.id, item["client_q"])
    item.update(client_id=client.id, client_new=False, client_name=client.name)
    return True


def set_client_kind(draft: dict, cb: kbs.ClientKindCb) -> bool:
    if draft["section"] != "client" or cb.line >= len(draft["client"]) or cb.kind not in CLIENT_KINDS:
        return False
    item = draft["client"][cb.line]
    item["kind"] = cb.kind
    drafts.client_total(item, draft.get("set_size", 5))
    return True


def set_direction(draft: dict, cb: kbs.DebtDirCb) -> bool:
    if draft["section"] != "money" or cb.line >= len(draft["debt"]) or cb.direction not in ("in", "out"):
        return False
    draft["debt"][cb.line]["direction"] = cb.direction
    return True


def set_date(draft: dict, action: str) -> bool:
    """«Bugun» / «Kecha» tugmasi — hamma yozuvlar shu kunga. False — sana o'zgarmadi."""
    d = today() if action == "today" else today() - timedelta(days=1)
    items = [*draft.get("personal", []), *draft.get("debt", [])]
    if draft["date"] == d.isoformat() and not any(item.get("date") for item in items):
        return False
    draft["date"] = d.isoformat()
    for item in items:
        item["date"] = None
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
    if draft["section"] in ("money", "client"):
        if not user.is_owner:
            raise IntakeError("Ruxsat yo'q.", drop=True)
        if draft["section"] == "client":
            return await _save_client(session, user, draft)
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

    cards: list[tuple[str, str, int]] = []
    personal = []
    for p in draft["personal"]:
        if p.get("dup"):
            continue
        e = await repo.create_personal_entry(
            session,
            owner_id=user.id,
            kind=p["kind"],
            category=p["category"],
            amount=Decimal(p["amount"]),
            currency=p["currency"],
            note=p["note"],
            entry_date=date.fromisoformat(drafts.item_date(draft, p)),
            raw_text=draft["raw_text"],
        )
        cards.append((personal_text(e), "p", e.id))
        personal.append(e)
    people: list[str] = []
    for d in draft["debt"]:
        e = await repo.create_debt_entry(
            session,
            owner_id=user.id,
            person=d["person"],
            direction=d["direction"],
            stated=d.get("stated", False),
            amount=Decimal(d["amount"]),
            currency=d["currency"],
            note=d["note"],
            entry_date=date.fromisoformat(drafts.item_date(draft, d)),
            raw_text=draft["raw_text"],
        )
        cards.append((debt_text(e), "d", e.id))
        if e.person not in people:
            people.append(e.person)

    if not cards:
        raise IntakeError("🔁 Bu yozuvlarning hammasi avval kiritilgan — qayta qo'shilmadi.", drop=True)
    header = "✅ <b>Saqlandi</b>"
    skipped = sum(1 for p in draft["personal"] if p.get("dup"))
    if skipped:
        header += f"\n🔁 Avval kiritilgan {skipped} ta yozuv qayta qo'shilmadi."
    balance = [line for person in people for line in await person_balance(session, user.id, person)]
    if balance:
        header += "\n\n" + "\n".join(balance)
    hint = await advisor.saved_hint(session, user.id, personal)
    if hint:
        header += "\n\n" + hint
    last = len(cards) - 1
    return Saved(
        header,
        [(text, kbs.money_entry_kb(kind, entry_id, app=i == last)) for i, (text, kind, entry_id) in enumerate(cards)],
    )


async def _save_client(session: AsyncSession, user: User, draft: dict) -> Saved:
    from bot.handlers.money import client_balance_line, client_text

    entry_date = date.fromisoformat(draft["date"])
    entries = []
    for c in draft["client"]:
        client = await repo.get_client(session, user.id, c["client_id"]) if c["client_id"] else None
        if client is None:
            client = await repo.get_or_create_client(session, user.id, c["client_name"] or c["client_q"])
        entries.append(
            await repo.create_client_entry(
                session,
                owner_id=user.id,
                client_id=client.id,
                kind=c["kind"],
                product=c["product"],
                sets=c["sets"],
                pieces=c["pieces"],
                unit_price=Decimal(c["price"]) if c["price"] else None,
                price_unit=c["price_unit"] if c["price"] else None,
                amount=Decimal(c["total"]),
                currency=c["currency"],
                note=c["note"],
                entry_date=entry_date,
                raw_text=draft["raw_text"],
                created_by=user.id,
            )
        )

    header = "✅ <b>Saqlandi</b>"
    clients = list({e.client_id: e.client for e in entries}.values())
    balance = [await client_balance_line(session, user.id, client) for client in clients]
    header += "\n\n" + "\n".join(balance)
    last = len(entries) - 1
    return Saved(header, [(client_text(e), kbs.money_entry_kb("c", e.id, app=i == last)) for i, e in enumerate(entries)])
