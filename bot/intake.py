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

from bot import advisor, drafts
from bot import keyboards as kbs
from bot.db import repo
from bot.db.models import (
    ClientKind,
    DebtDirection,
    EntryStatus,
    MaterialEntry,
    PayKind,
    Supplier,
    User,
    WorkEntry,
    WorkerPayment,
)
from bot.services import llm, matcher
from bot.services.utils import fmt_money, today


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


async def analyze(
    session: AsyncSession, user: User, text: str, draft: dict | None, worker: User | None = None
) -> dict:
    """Matndan yangi qoralama. draft — tuzatilayotgan (yoki to'ldirilayotgan) qoralama, bo'lmasa None.
    worker — egasi shu ishchining hisob-kitob oynasida yozdi: ismi aytilmagan ish va pul unga tegishli."""
    correcting = draft is not None and not drafts.is_stale(draft)
    known, worker_name, set_size, info = await _context(session, user)
    if worker is not None and user.is_owner:
        info += (
            f" Xabar ishchi {worker.name} bilan hisob-kitob oynasida yozildi: ishchi ismi aytilmagan ish va "
            f"pul (pay) — {worker.name}ga tegishli."
        )
    try:
        if correcting:
            ext = await llm.correct(drafts.for_llm(draft), text, today(), known, worker_name, info)
        else:
            ext = await llm.extract(text, today(), known, worker_name, info)
    except llm.LlmError:
        raise IntakeError("⚠️ Xabarni tahlil qilib bo'lmadi. Birozdan keyin qayta urinib ko'ring.")

    raw = f"{draft['raw_text']} → {text}" if correcting and draft.get("raw_text") else text
    new = await _build(
        session, user, ext, raw, set_size,
        said=f"🗣 <i>«{escape(text)}»</i>",
        entry_id=draft["entry_id"] if correcting else None,
        source=draft.get("source") if correcting else None,
    )
    if worker is not None and user.is_owner and new["section"] == "work":
        drafts.share_worker(new, worker.id, worker.name)
        await drafts.resolve(session, new, user)
    return new


MAX_IMAGES = 5
# Rasmdan ko'chirilgan matnning xabarda ko'rinadigan qismi
READ_PREVIEW = 600


async def analyze_images(session: AsyncSession, user: User, images: list[tuple[bytes, str]], caption: str = "") -> dict:
    """Bank skrinshoti, chek yoki daftar rasmidan yangi qoralama. images — (rasm, media turi).

    Avval rasm to'g'ridan-to'g'ri tahlil qilinadi. O'xshamasa (xato yoki yozuv topilmadi) — rasmdagi yozuv
    matnga ko'chiriladi (qo'lda yozilgan qog'oz, kirill yoki lotin) va oddiy xabardek tahlil qilinadi;
    qoralamada nima o'qilgani ko'rinadi."""
    caption = caption.strip()[:500]
    images = images[:MAX_IMAGES]
    known, worker_name, set_size, info = await _context(session, user)
    label = "🖼 Rasm" if len(images) == 1 else f"🖼 {len(images)} ta rasm"
    raw = f"{label}: {caption}" if caption else label
    try:
        ext = await llm.extract_images(images, today(), known, worker_name, info, caption)
    except llm.LlmError:
        ext = None
    if ext is not None and _understood(user, ext):
        return await _build(session, user, ext, raw, set_size, said=f"<i>{escape(raw)}</i>", source="image")

    try:
        text = await llm.read_images(images)
    except llm.LlmError:
        text = ""
    if not text:
        if ext is None:
            raise IntakeError("⚠️ Rasmni o'qib bo'lmadi. Birozdan keyin qayta yuboring yoki matn bilan yozing.")
        return await _build(session, user, ext, raw, set_size, said=f"<i>{escape(raw)}</i>", source="image")

    read = f"{label}: «{text}»"
    shown = text if len(text) <= READ_PREVIEW else text[:READ_PREVIEW] + "…"
    said = f"{label}da: <i>«{escape(shown)}»</i>"
    message = f"{text}\n\nIzoh: {caption}" if caption else text
    info = f"{info} Xabar — rasmdagi yozuvning ko'chirmasi (qo'lda yozilgan qog'oz, chek yoki skrinshot).".strip()
    try:
        ext = await llm.extract(message, today(), known, worker_name, info)
    except llm.LlmError:
        raise IntakeError(f"{said}\n\n⚠️ Yozuvni tahlil qilib bo'lmadi. Birozdan keyin qayta yuboring yoki matn bilan yozing.")
    return await _build(session, user, ext, read, set_size, said=said, source="image")


def _understood(user: User, ext: llm.Extraction) -> bool:
    """Xabardan saqlasa bo'ladigan yozuv chiqdimi (egasida — har qanday bo'lim, ishchida — ish yoki pul)."""
    special = user.is_owner and (
        drafts.is_money(ext) or drafts.is_client(ext) or drafts.is_price(ext) or drafts.is_material(ext)
    )
    return special or (ext.section in ("work", "pay") and bool(ext.work or ext.pays))


async def _reroute_debts(session: AsyncSession, user: User, ext: llm.Extraction) -> None:
    """Qarz deb ajratilgan odam aslida ishchi yoki mijoz bo'lsa (ovozda ism buzilib, Claude tanimay qolgan:
    «Rabiyaga 500 ming berdim») — ishchi bilan hisob-kitobga yoki mijoz to'loviga o'tkaziladi. Faqat hamma
    odamlar bitta turga aniq mos kelsa; aks holda qarz bo'lib qoladi."""
    if not user.is_owner or ext.section not in ("personal", "debt") or ext.personal or not ext.debt:
        return
    acc = user.account_id
    workers = [(w.id, w.name) for w in await repo.list_workers(session, acc)]
    clients = [(c.id, c.name) for c in await repo.list_clients(session, acc)]
    if not workers and not clients:
        return
    worker_aliases = await repo.get_aliases(session, acc, "worker")
    client_aliases = await repo.get_aliases(session, acc, "client")
    found: list[tuple[str, str]] = []
    for d in ext.debt:
        if not d.person or d.direction not in (DebtDirection.IN, DebtDirection.OUT):
            return
        worker_id = matcher.match(d.person, workers, worker_aliases).exact_id
        client_id = matcher.match(d.person, clients, client_aliases).exact_id
        if worker_id is not None and client_id is None:
            found.append(("worker", dict(workers)[worker_id]))
        elif client_id is not None and worker_id is None and d.direction == DebtDirection.IN and not d.stated:
            found.append(("client", dict(clients)[client_id]))
        else:
            return
    if len({kind for kind, _ in found}) != 1:
        return
    if found[0][0] == "worker":
        # Pul: berdim (out) — pay, oldim (in) — return. Qarz holati: «u menga qarzdor» (out) — ishchi qarzdor
        ext.pays = [
            llm.PayItem(
                worker_name=name,
                kind=PayKind.BALANCE if d.stated else PayKind.PAY if d.direction == DebtDirection.OUT else PayKind.RETURN,
                amount=d.amount or 0,
                whole=False,
                worker_owes=d.stated and d.direction == DebtDirection.OUT,
                currency=d.currency or "",
                note=d.note or "",
                date=d.date or "",
            )
            for d, (_, name) in zip(ext.debt, found)
        ]
        ext.section = "pay"
    else:
        ext.client = [
            llm.ClientItem(
                client_name=name, kind=ClientKind.PAYMENT, product=None, sets=None, pieces=None,
                price_per_set=None, price_per_piece=None, total=d.amount, currency=d.currency, note=d.note,
            )
            for d, (_, name) in zip(ext.debt, found)
        ]
        ext.section = "client"
    ext.debt = []


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
    if entry_id is None:
        await _reroute_debts(session, user, ext)
    if not _understood(user, ext):
        example = (
            "«bozorga 50 ming won», «Tohirga 10 komplekt maksima berdim, 140 mingdan», «A-12 modeldan 50 ta» "
            "yoki «Robiyaga 500 ming berdim»"
            if user.is_owner
            else "«A-12 modeldan 50 ta topshirdim» yoki «500 ming oldim»"
        )
        hint = ext.question or f"Tushunmadim. Masalan: {example}."
        raise IntakeError(f"{said}\n\n🤔 {escape(hint)}")

    currency = (await repo.enabled_currencies(session, user.account_id))[0]
    new_draft = drafts.from_extraction(ext, raw, entry_id=entry_id, default_currency=currency, set_size=set_size)
    if entry_id and (new_draft["section"] != "work" or len(new_draft["lines"]) != 1 or new_draft.get("pays")):
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
    """To'liq qoralama (ishchi, model, mijoz topildi, summa aytildi) «✅ Saqlash» bosilmasdan saqlanadimi.

    Ovoz va matn — «⚙️ Sozlamalar → Darhol saqlash» yoqilgan bo'lsa (sukut bo'yicha yoqiq). Rasmdan o'qilgani —
    faqat xarajat/daromad va qarz, «Rasmdagi xarajatlarni darhol saqlash» yoqilgan bo'lsa; rasmdagi boshqa
    yozuvlar har doim «To'g'rimi?» deb so'raladi."""
    if drafts.next_missing(draft) is not None:
        return False
    if draft.get("source") == "image":
        return user.is_owner and draft["section"] == "money" and await repo.auto_save_images(session, user.id)
    return await repo.auto_save(session, user.account_id)


async def choose(session: AsyncSession, user: User, draft: dict, cb: kbs.ResolveCb) -> bool:
    """Ishchi, model, kasb, mijoz yoki ta'minotchini tanlash tugmasi. False — o'zgarish yo'q."""
    if draft["section"] == "client":
        return await _choose_client(session, user, draft, cb)
    if draft["section"] == "material":
        return await _choose_supplier(session, user, draft, cb)
    if draft["section"] == "price":
        return await _choose_price(session, user, draft, cb)
    if draft["section"] == "work" and cb.kind == "pworker":
        if not user.is_owner or cb.line >= len(draft.get("pays", [])):
            return False
        await _pick_worker(session, user, draft, draft["pays"][cb.line], cb.item_id)
        await drafts.resolve(session, draft, user)
        return True
    if draft["section"] != "work" or cb.line >= len(draft["lines"]):
        return False
    line = draft["lines"][cb.line]

    if cb.kind == "worker" and user.is_owner:
        await _pick_worker(session, user, draft, line, cb.item_id)
    elif cb.kind == "model":
        model = await _pick_model(session, user, line, cb.item_id)
        if model is None:
            return False
        drafts.set_model(line, model)
    elif cb.kind == "job":
        job = await _pick_job(session, user, line["job_q"], cb.item_id)
        if job is None:
            return False
        line["job_id"], line["job_name"] = job.id, job.name
    else:
        return False
    # Ishchi almashsa — uning kasbi, model yoki kasb almashsa — narx yangilanadi
    await drafts.resolve(session, draft, user)
    return True


async def _pick_worker(session: AsyncSession, user: User, draft: dict, item: dict, worker_id: int) -> None:
    """Tugmada tanlangan ishchi. Ismi aytilmagan bo'lsa — ismi aytilmagan boshqa qatorlar ham shu ishchiga."""
    acc = user.account_id
    worker = await repo.get_worker(session, acc, worker_id)
    if worker is None or not worker.is_active:
        raise IntakeError("Ishchi topilmadi.")
    if item["worker_q"] and item["worker_q"].lower() != worker.name.lower():
        await repo.save_alias(session, acc, "worker", worker.id, item["worker_q"])
    item["worker_id"], item["worker_name"] = worker.id, worker.name
    if not item["worker_q"]:
        drafts.share_worker(draft, worker.id, worker.name)


async def _pick_model(session: AsyncSession, user: User, item: dict, item_id: int):
    """Tugmada tanlangan model; item_id=0 — aytilgan nom bilan yangi model (faqat egasi)."""
    acc = user.account_id
    if item_id == 0:
        if not user.is_owner or not item["model_q"]:
            return None
        return await repo.get_model_by_name(session, acc, item["model_q"]) or await repo.create_model(
            session, acc, item["model_q"].strip()
        )
    models = {m.id: m for m in await repo.list_models(session, acc)}
    model = models.get(item_id)
    if model is None:
        raise IntakeError("Model topilmadi.")
    if item["model_q"] and item["model_q"].lower() != model.name.lower():
        await repo.save_alias(session, acc, "model", model.id, item["model_q"])
    return model


async def _pick_job(session: AsyncSession, user: User, query: str | None, item_id: int):
    """Tugmada tanlangan kasb; item_id=0 — aytilgan nom bilan yangi kasb (faqat egasi)."""
    acc = user.account_id
    if item_id == 0:
        if not user.is_owner or not query:
            return None
        return await repo.create_job(session, acc, query)
    job = await repo.get_job(session, acc, item_id)
    if job is None:
        raise IntakeError("Kasb topilmadi.")
    if query and query.lower() != job.name.lower():
        await repo.save_alias(session, acc, "job", job.id, query)
    return job


async def _choose_price(session: AsyncSession, user: User, draft: dict, cb: kbs.ResolveCb) -> bool:
    if cb.kind not in ("pmodel", "pjob") or not user.is_owner or cb.line >= len(draft["prices"]):
        return False
    item = draft["prices"][cb.line]
    if cb.kind == "pmodel":
        model = await _pick_model(session, user, item, cb.item_id)
        if model is None:
            return False
        item["model_id"], item["model_name"] = model.id, model.name
    else:
        job = await _pick_job(session, user, item["job_q"], cb.item_id)
        if job is None:
            return False
        item["job_id"], item["job_name"] = job.id, job.name
    await drafts.resolve(session, draft, user)
    return True


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


async def _choose_supplier(session: AsyncSession, user: User, draft: dict, cb: kbs.ResolveCb) -> bool:
    """Ta'minotchi tugmasi; item_id=0 — ro'yxatda yo'q: aytilgancha yoziladi, xabar bormaydi."""
    if cb.kind != "supplier" or not user.is_owner or cb.line >= len(draft["materials"]):
        return False
    item = draft["materials"][cb.line]
    if cb.item_id == 0:
        item.update(supplier_id=None, supplier_free=True)
        return True
    supplier = await repo.get_supplier(session, user.account_id, cb.item_id)
    if supplier is None or not supplier.is_active:
        raise IntakeError("Ta'minotchi topilmadi.")
    if item["supplier"] and item["supplier"].lower() != supplier.name.lower():
        await repo.save_alias(session, user.account_id, "supplier", supplier.id, item["supplier"])
    item.update(supplier_id=supplier.id, supplier=supplier.name, supplier_free=False)
    return True


def set_client_kind(draft: dict, cb: kbs.ClientKindCb) -> bool:
    if draft["section"] != "client" or cb.line >= len(draft["client"]) or cb.kind not in CLIENT_KINDS:
        return False
    item = draft["client"][cb.line]
    item["kind"] = cb.kind
    drafts.client_total(item, draft.get("set_size", 5))
    return True


def set_pay_kind(draft: dict, cb: kbs.PayKindCb) -> bool:
    pays = draft.get("pays", [])
    if draft["section"] != "work" or cb.line >= len(pays) or cb.kind not in (PayKind.PAY, PayKind.RETURN):
        return False
    pays[cb.line]["kind"] = cb.kind
    return True


def pays_to_debt(draft: dict, user: User) -> bool:
    """«Ishchi emas — qarz qilib yozish»: faqat pul bo'lgan qoralama oddiy qarzga aylanadi."""
    if not user.is_owner or draft["section"] != "work" or draft["lines"] or not draft.get("pays"):
        return False
    debt = []
    for p in draft["pays"]:
        stated = p["kind"] == PayKind.BALANCE
        if stated:
            direction = DebtDirection.OUT if p["worker_owes"] else DebtDirection.IN
        elif p["kind"] in (PayKind.PAY, PayKind.RETURN):
            direction = DebtDirection.OUT if p["kind"] == PayKind.PAY else DebtDirection.IN
        else:
            direction = None
        debt.append(
            {
                "person": p["worker_q"] or p["worker_name"],
                "direction": direction,
                "stated": stated,
                "amount": p["amount"] if p["amount"] != "0" else None,
                "currency": p["currency"],
                "note": p["note"],
                "date": p["date"],
            }
        )
    for key in ("lines", "pays", "balances", "by_owner", "auto_ok", "pay_ok"):
        draft.pop(key, None)
    draft.update(section="money", personal=[], debt=debt)
    return True


def set_direction(draft: dict, cb: kbs.DebtDirCb) -> bool:
    if draft["section"] != "money" or cb.line >= len(draft["debt"]) or cb.direction not in ("in", "out"):
        return False
    draft["debt"][cb.line]["direction"] = cb.direction
    return True


def set_date(draft: dict, action: str) -> bool:
    """«Bugun» / «Kecha» tugmasi — hamma yozuvlar shu kunga. False — sana o'zgarmadi."""
    d = today() if action == "today" else today() - timedelta(days=1)
    items = [*draft.get("personal", []), *draft.get("debt", []), *draft.get("lines", []), *draft.get("pays", [])]
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
    """Qoralamani bazaga yozadi. Ish va pul yozuvi haqida ikkinchi tomonga xabar boradi: egasi yozsa — ishchiga,
    ishchi yozsa — egasiga (tasdiq kerak bo'lsa tugmalari bilan)."""
    from bot.handlers.entries import entry_text, pay_text  # aylanma importdan qochish uchun

    if drafts.next_missing(draft) is not None:
        raise IntakeError("Avval yetishmagan ma'lumotni to'ldiring.")
    if draft["section"] in ("money", "client", "price", "material"):
        if not user.is_owner:
            raise IntakeError("Ruxsat yo'q.", drop=True)
        if draft["section"] == "client":
            return await _save_client(session, user, draft)
        if draft["section"] == "price":
            return await _save_prices(session, user, draft)
        if draft["section"] == "material":
            return await _save_materials(session, bot, user, draft)
        return await _save_money(session, user, draft)

    acc = user.account_id
    needs_approval = not user.is_owner and await repo.require_approval(session, acc)
    payments: list[WorkerPayment] = []

    if draft["entry_id"]:
        entry = await repo.get_work_entry(session, acc, draft["entry_id"])
        if entry is None or (not user.is_owner and entry.worker_id != user.id):
            raise IntakeError("⚠️ Yozuv topilmadi (o'chirilgan bo'lishi mumkin).", drop=True)
        line = draft["lines"][0]
        entry.worker_id = line["worker_id"]
        # Model yoki kasb o'zgarmasa — yozilgan paytdagi narx qoladi
        if (entry.model_id, entry.job_id) != (line["model_id"], line["job_id"]):
            model = await repo.get_model(session, acc, line["model_id"])
            if model is not None:
                await repo.reprice_entry(session, entry, model, line["job_id"])
        entry.quantity = line["quantity"]
        entry.work_date = date.fromisoformat(drafts.item_date(draft, line))
        entry.raw_text = draft["raw_text"]
        if needs_approval:
            entry.status = EntryStatus.PENDING
        await session.flush()
        await session.refresh(entry, ["worker", "model", "job"])
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
                    job_id=line["job_id"],
                    quantity=line["quantity"],
                    work_date=date.fromisoformat(drafts.item_date(draft, line)),
                    status=EntryStatus.PENDING if needs_approval else EntryStatus.APPROVED,
                    raw_text=draft["raw_text"],
                    created_by=user.id,
                )
            )
        for p in draft.get("pays", []):
            payments.append(await _save_pay(session, user, draft, p))
        header = "✅ <b>Saqlandi</b>"

    if needs_approval and saved:
        header += "\n⏳ Ish yozuvlari egasining tasdig'ini kutmoqda."
    if any(p.status == EntryStatus.PENDING for p in payments):
        header += "\n⏳ Pul yozuvlari egasining tasdig'ini kutmoqda."
    balance = await _balance_lines(session, user, [e.worker for e in saved], [p.worker for p in payments])
    if balance:
        header += "\n\n" + "\n".join(balance)
    edited = bool(draft["entry_id"])
    if user.is_owner:
        notified = await _notify_workers(bot, session, user, saved, payments, edited)
        if notified:
            header += "\n📨 Xabar yuborildi: " + ", ".join(escape(name) for name in notified)
    else:
        await _notify_owner(bot, session, user, saved, payments, edited)
    cards = [(entry_text(e), kbs.entry_kb(e, user)) for e in saved]
    cards += [(pay_text(e, user), kbs.pay_entry_kb(e, user)) for e in payments]
    if cards:
        # «Ilovani ochish» — oxirgi kartochkada
        last_text, _ = cards[-1]
        last = payments[-1] if payments else saved[-1]
        kb = kbs.pay_entry_kb(last, user, app=True) if payments else kbs.entry_kb(last, user, app=True)
        cards[-1] = (last_text, kb)
    return Saved(header, cards)


async def save_auto(session: AsyncSession, bot: Bot, user: User, draft: dict) -> Saved:
    """«✅ Saqlash» bosilmasdan saqlash: qoralama ko'rsatilmagani uchun sarlavhada nima eshitilgani ham bor."""
    saved = await save(session, bot, user, draft)
    said = drafts.short_raw(draft)
    if said:
        first, _, rest = saved.header.partition("\n")
        saved.header = "\n".join(part for part in (first, said, rest) if part)
    return saved


async def _save_pay(session: AsyncSession, user: User, draft: dict, p: dict) -> WorkerPayment:
    """Ishchi «oldim» / «qaytardim» desa — «Pul yozuvlarini tasdiqlash» yoqilgan bo'lsa egasi tasdiqlagach,
    o'chiq bo'lsa darhol hisobga kiradi; hisob holatini ishchi yozsa — har doim egasi tasdiqlagach."""
    acc = user.account_id
    worker = await repo.get_worker(session, acc, p["worker_id"])
    if worker is None or (not user.is_owner and worker.id != user.id):
        raise IntakeError("⚠️ Ishchi topilmadi (o'chirilgan bo'lishi mumkin).", drop=True)
    approved = user.is_owner
    if not approved and p["kind"] in (PayKind.PAY, PayKind.RETURN):
        approved = not await repo.require_pay_approval(session, acc)
    amount = Decimal(p["amount"])
    if p["kind"] == PayKind.BALANCE and p["worker_owes"]:
        amount = -amount
    return await repo.create_payment(
        session,
        owner_id=acc,
        worker_id=worker.id,
        kind=p["kind"],
        amount=amount,
        currency=p["currency"],
        note=p["note"],
        entry_date=date.fromisoformat(drafts.item_date(draft, p)),
        status=EntryStatus.APPROVED if approved else EntryStatus.PENDING,
        raw_text=draft["raw_text"],
        created_by=user.id,
    )


async def _balance_lines(
    session: AsyncSession, viewer: User, workers: list[User], paid: list[User]
) -> list[str]:
    """Ishchilarning qoldig'i: pul yozilganlarniki — har doim, faqat ish yozilganlarniki — hisob-kitob
    yuritilsa (pul yoki hisob holati avval yozilgan bo'lsa)."""
    from bot.handlers.entries import tracked

    unique = list({w.id: w for w in [*workers, *paid]}.values())
    if not unique:
        return []
    balances = await repo.worker_balances(session, viewer.account_id, [w.id for w in unique])
    always = {w.id for w in paid}
    rows = []
    for w in unique:
        b = balances.get(w.id)
        if w.id in always or tracked(b):
            rows.append(drafts.balance_text(b.balance if b else {}, w.name if viewer.is_owner else None))
    return rows


async def _notify_workers(
    bot: Bot, session: AsyncSession, owner: User, entries: list[WorkEntry], payments: list[WorkerPayment],
    edited: bool,
) -> list[str]:
    """Egasi yozdi: har bir ishchiga uning ishlari va pullari bitta xabarda, yangi qoldig'i bilan.
    Xabar borgan ishchilar ismi qaytadi (botga ulanmagan yoki botni bloklagan ishchiga xabar bormaydi)."""
    from bot.handlers.entries import entry_text, notify_user, pay_text, tracked

    by_worker: dict[int, tuple[User, list[str]]] = {}
    for e in entries:
        by_worker.setdefault(e.worker_id, (e.worker, []))[1].append(entry_text(e))
    for e in payments:
        by_worker.setdefault(e.worker_id, (e.worker, []))[1].append(pay_text(e, e.worker))
    paid = {e.worker_id for e in payments}
    notified = []
    for worker_id, (worker, cards) in by_worker.items():
        if not worker.tg_id or not worker.is_active or worker_id == owner.id:
            continue
        title = "✏️ <b>Boshlig'ingiz yozuvni o'zgartirdi</b>" if edited else "🧾 <b>Boshlig'ingiz yozdi</b>"
        text = title + "\n\n" + "\n\n".join(cards)
        b = await repo.worker_balance(session, owner.id, worker_id)
        if worker_id in paid or tracked(b):
            text += "\n\n" + drafts.balance_text(b.balance)
        if await notify_user(bot, worker.tg_id, text, kbs.app_kb("ledger")):
            notified.append(worker.name)
    return notified


async def _notify_owner(
    bot: Bot, session: AsyncSession, worker: User, entries: list[WorkEntry], payments: list[WorkerPayment],
    edited: bool,
) -> None:
    """Ishchi yozdi: egasiga — tasdiq kutayotgani tugmalari bilan, darhol hisobga kirgani ham xabar bo'lib."""
    from bot.handlers.entries import entry_text, notify_owner, notify_owner_pending, pay_text

    owner = await session.get(User, worker.owner_id)
    if owner is None:
        return
    done = []
    for e in entries:
        if e.status == EntryStatus.PENDING:
            await notify_owner_pending(bot, session, e)
        else:
            done.append(e)
    if done:
        action = "yozuvini o'zgartirdi" if edited else "yozdi"
        text = f"🧾 <b>{escape(worker.name)} {action}</b>\n\n" + "\n\n".join(entry_text(e) for e in done)
        markup = kbs.entry_kb(done[0], owner) if len(done) == 1 else kbs.app_kb("entries")
        await notify_owner(bot, session, owner.id, text, markup=markup)
    for e in payments:
        balance = (await repo.worker_balance(session, e.owner_id, worker.id)).balance
        title = "🔔 <b>Tasdiq kutmoqda</b>" if e.status == EntryStatus.PENDING else "🧾 <b>Hisob-kitob</b>"
        text = (
            f"{title} — {escape(worker.name)} yozdi\n\n{pay_text(e, owner)}\n\n"
            f"{drafts.balance_text(balance, worker.name)}"
        )
        await notify_owner(bot, session, e.owner_id, text, markup=kbs.pay_entry_kb(e, owner))


async def _save_prices(session: AsyncSession, user: User, draft: dict) -> Saved:
    rows = []
    for p in draft["prices"]:
        model = await repo.get_model(session, user.id, p["model_id"])
        job = await repo.get_job(session, user.id, p["job_id"]) if p["job_id"] else None
        if model is None or (p["job_id"] and job is None):
            raise IntakeError("⚠️ Model yoki kasb topilmadi (o'chirilgan bo'lishi mumkin).", drop=True)
        price = Decimal(p["price"])
        await repo.set_model_price(session, model, job.id if job else None, price, p["currency"])
        what = escape(job.name) if job else "umumiy narx"
        rows.append(f"• {escape(model.name)} — {what}: <b>{fmt_money(price, p['currency'])}</b>")
    header = (
        "✅ <b>Narx saqlandi</b> (bir dona uchun)\n\n" + "\n".join(rows)
        + "\n\nYangi narx shu paytdan keyingi yozuvlarga qo'llanadi. Hamma narxlar — «🧵 Modellar»da."
    )
    return Saved(header)


async def _save_materials(session: AsyncSession, bot: Bot, user: User, draft: dict) -> Saved:
    """Xom ashyo xaridi. Ro'yxatdagi ta'minotchidan olingan bo'lsa — unga xabar boradi."""
    from bot.handlers.money import material_month_line, material_text

    def dec(value: str | None) -> Decimal | None:
        return Decimal(value) if value else None

    suppliers: dict[int, Supplier] = {}
    for m in draft["materials"]:
        if m["supplier_id"] and m["supplier_id"] not in suppliers:
            supplier = await repo.get_supplier(session, user.id, m["supplier_id"])
            if supplier is None:
                raise IntakeError("⚠️ Ta'minotchi topilmadi (o'chirilgan bo'lishi mumkin).", drop=True)
            suppliers[supplier.id] = supplier

    entries = [
        await repo.create_material_entry(
            session,
            owner_id=user.id,
            kind=m["kind"],
            name=m["name"],
            quantity=dec(m["quantity"]),
            unit=m["unit"],
            unit_price=dec(m["price"]),
            amount=dec(m["total"]),
            currency=m["currency"],
            supplier=m["supplier"],
            supplier_id=m["supplier_id"],
            entry_date=date.fromisoformat(draft["date"]),
            raw_text=draft["raw_text"],
            created_by=user.id,
        )
        for m in draft["materials"]
    ]
    header = "✅ <b>Xom ashyoga qo'shildi</b>\n\n" + await material_month_line(session, user.id)
    notified, unlinked = await _notify_suppliers(bot, session, user, entries, list(suppliers.values()))
    if notified:
        header += "\n📨 Xabar yuborildi: " + ", ".join(escape(name) for name in notified)
    if unlinked:
        header += (
            f"\n🔗 {', '.join(escape(name) for name in unlinked)} hali botga ulanmagan — xabar bormadi. "
            "Taklif havolasi: «🧱 Xom ashyo → 🏪 Ta'minotchilar»."
        )
    last = len(entries) - 1
    return Saved(
        header, [(material_text(e), kbs.money_entry_kb("x", e.id, app=i == last)) for i, e in enumerate(entries)]
    )


async def _notify_suppliers(
    bot: Bot, session: AsyncSession, owner: User, entries: list[MaterialEntry], suppliers: list[Supplier]
) -> tuple[list[str], list[str]]:
    """Egasi ta'minotchidan xom ashyo oldi: har bir ta'minotchiga bitta xabar («Jasur sizdan xom ashyo oldi»).
    Qaytadi: (xabar borganlar, botga ulanmaganlar). O'chirilgan ta'minotchiga xabar bormaydi."""
    from bot.handlers.entries import notify_user
    from bot.handlers.money import material_text, supplier_month_line

    notified: list[str] = []
    unlinked: list[str] = []
    stats = await repo.supplier_stats(session, owner.id, today().replace(day=1)) if suppliers else {}
    for supplier in suppliers:
        own = [e for e in entries if e.supplier_id == supplier.id]
        if not own or not supplier.is_active:
            continue
        if not supplier.tg_id:
            unlinked.append(supplier.name)
            continue
        text = f"🧱 <b>{escape(owner.name)}</b> sizdan xom ashyo oldi\n\n" + "\n\n".join(
            material_text(e, supplier=False) for e in own
        )
        st = stats.get(supplier.id)
        if st is not None and st.month_count > len(own):
            text += "\n\n" + supplier_month_line(st)
        if await notify_user(bot, supplier.tg_id, text):
            notified.append(supplier.name)
    return notified, unlinked


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
