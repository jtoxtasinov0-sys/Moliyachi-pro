"""Ish boshqaruvchi: egasi ishchisini shunday qiladi — u ishxonani egasidek boshqaradi (ishchilar ishi, kasblar,
modellar va narxlar, mijozlar, xom ashyo, ta'minotchilar), faqat ishchilarning puli (avans, ish haqi, qoldig'i),
egasining xarajat, daromad, qarzlari va sozlamalari unga yopiq.

Boshqaruvchi nima qilsa — darhol bajarilmaydi: egasiga so'rov boradi («✅ Tasdiqlash» / «🚫 Rad etish»),
tasdiqlansa egasi nomidan bajariladi (xuddi egasi o'zi qilgandek: ishchi, mijoz, ta'minotchiga xabar ham boradi),
boshqaruvchiga natija haqida xabar keladi. O'zi olgan pul («500 ming oldim») ham shunday — egasi tasdiqlagach.

So'rov ikki xil: ovoz, matn yoki rasmdan qoralama (tasdiqlansa — intake.save) va ilovadagi o'zgartirish
(tasdiqlansa — web/api.py dagi o'sha amal, egasi nomidan)."""
import html
import json
import re

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from bot import drafts, intake
from bot import keyboards as kbs
from bot.db import repo
from bot.db.models import ManagerRequest, RequestKind, RequestStatus, User

STATUS_LABELS = {
    RequestStatus.PENDING: "⏳ Boshliq tasdig'ini kutmoqda",
    RequestStatus.APPROVED: "✅ Tasdiqlandi — bajarildi",
    RequestStatus.REJECTED: "🚫 Rad etildi",
    RequestStatus.FAILED: "⚠️ Tasdiqlandi, lekin bajarib bo'lmadi",
    RequestStatus.CANCELLED: "↩️ Bekor qilindi",
}

# Telegram xabari 4096 belgigacha: tavsif sarlavha va natija bilan birga sig'sin
SUMMARY_LIMIT = 3000


MANAGER_ON = (
    "👔 <b>Siz endi ish boshqaruvchisiz</b>\n\n"
    "{owner} sizni ish boshqaruvchi qildi: ishchilar ishi, kasblar, modellar va narxlar, mijozlar, xom ashyo va "
    "ta'minotchilarni boshqarasiz. Har bir yozuv va o'zgartirishingiz boshliq tasdig'iga boradi — tasdiqlasa "
    "bajariladi va sizga xabar keladi («📨 So'rovlarim»).\n\n"
    "🔒 Ishchilarning puli (avans, ish haqi, qoldig'i), boshliqning xarajat, daromad va qarzlari — faqat boshliqda.\n\n"
    "Pastdagi menyu yangilandi. Batafsil — «❓ Yordam»."
)
MANAGER_OFF = "ℹ️ Siz endi oddiy ishchisiz: ish boshqaruvchi huquqi olib tashlandi. Pastdagi menyu yangilandi."


def role_notice(worker: User, owner: User) -> str:
    """Ishchiga: u ish boshqaruvchi bo'ldi yoki yana oddiy ishchi."""
    return MANAGER_ON.format(owner=html.escape(owner.name)) if worker.is_manager else MANAGER_OFF


class RequestDone(Exception):
    """So'rov allaqachon ko'rib chiqilgan (tasdiqlangan, rad etilgan yoki bekor qilingan)."""


DONE = "✅ Bajarildi"


def clip(text: str, limit: int = SUMMARY_LIMIT) -> str:
    """Uzun matn — teglarsiz va qisqartirilgan (HTML teg o'rtasidan kesilmasin)."""
    if len(text) <= limit:
        return text
    plain = html.unescape(re.sub(r"<[^>]+>", "", text))
    return html.escape(plain[: limit - 1]) + "…"


def status_label(req: ManagerRequest, for_owner: bool) -> str:
    if for_owner and req.status == RequestStatus.PENDING:
        return "⏳ Tasdiqingizni kutmoqda"
    return STATUS_LABELS.get(req.status, req.status)


def request_text(req: ManagerRequest, for_owner: bool) -> str:
    """So'rov kartochkasi: egasiga — kim so'ragani bilan."""
    head = f"📨 <b>So'rov #{req.id}</b>"
    if for_owner:
        head += f" · 👔 {html.escape(req.manager.name)}"
    parts = [head, req.summary, f"<b>{status_label(req, for_owner)}</b>"]
    if req.result:
        parts.append(req.result)
    return "\n\n".join(parts)


async def submit(
    session: AsyncSession, bot: Bot, manager: User, kind: str, payload: dict, summary: str
) -> ManagerRequest:
    """So'rov yaratiladi va egasiga tugmalari bilan yuboriladi."""
    from bot.handlers.entries import notify_owner  # aylanma importdan qochish uchun

    req = await repo.create_request(
        session, manager=manager, kind=kind, payload=json.dumps(payload, ensure_ascii=False), summary=clip(summary)
    )
    owner = await session.get(User, manager.owner_id)
    if owner is not None:
        text = f"🔔 <b>{html.escape(manager.name)} so'radi</b> — tasdiqlasangiz bajariladi\n\n{request_text(req, True)}"
        await notify_owner(bot, session, owner.id, text, markup=kbs.request_kb(req, owner))
    return req


async def submit_draft(session: AsyncSession, bot: Bot, manager: User, draft: dict) -> intake.Saved:
    """Boshqaruvchining qoralamasi (ovoz, matn, rasm) saqlanmaydi — egasining tasdig'iga yuboriladi."""
    from bot.handlers.entries import entry_text

    if refusal := intake.manager_refusal(manager, draft):
        raise intake.IntakeError(refusal, drop=True)
    summary = drafts.render(draft)
    if draft.get("entry_id"):
        entry = await repo.get_work_entry(session, manager.account_id, draft["entry_id"])
        if entry is None:
            raise intake.IntakeError("⚠️ Yozuv topilmadi (o'chirilgan bo'lishi mumkin).", drop=True)
        summary = f"<b>Hozir:</b>\n{entry_text(entry)}\n\n<b>O'zgartirish:</b>\n{summary}"
    req = await submit(session, bot, manager, RequestKind.DRAFT, draft, summary)
    header = "📨 <b>Boshliq tasdig'iga yuborildi</b>\nTasdiqlasa — hisobga kiradi, sizga xabar keladi."
    return intake.Saved(header, [(request_text(req, False), kbs.request_kb(req, manager))], queued=True)


async def ask(
    session: AsyncSession, bot: Bot, manager: User, op: str, match: dict[str, str], body: dict | None = None
) -> ManagerRequest:
    """Ilovadagi (yoki botdagi) o'zgartirish — egasining tasdig'iga. Noto'g'ri bo'lsa (yozuv topilmadi, nom bo'sh)
    — web.api.ApiError, so'rov yaratilmaydi. Tavsif funksiyasi tanani to'ldirishi mumkin (toggle — qaysi holatga)."""
    from bot.web import api  # aylanma importdan qochish uchun

    body = {} if body is None else body
    summary = await api.describe_op(session, manager, op, match, body)
    payload = {"op": op, "match": match, "body": body}
    # Xuddi shu so'rov hali kutayotgan bo'lsa — qayta yuborilmaydi (tugma ikki marta bosilganda)
    same = json.dumps(payload, ensure_ascii=False)
    for req in await repo.list_requests(session, manager.account_id, manager_id=manager.id, status=RequestStatus.PENDING):
        if req.kind == RequestKind.API and req.payload == same:
            raise api.ApiError(400, "Bu so'rov boshliqqa yuborilgan — javobini kuting («📨 So'rovlarim»)")
    return await submit(session, bot, manager, RequestKind.API, payload, summary)


async def _execute(session: AsyncSession, bot: Bot, owner: User, req: ManagerRequest) -> str:
    """Egasi nomidan bajaradi. Qaytadi: egasiga ko'rsatiladigan natija."""
    from bot.web import api

    payload = json.loads(req.payload)
    if req.kind == RequestKind.DRAFT:
        saved = await intake.save(session, bot, owner, payload)
        return saved.header
    await api.run_op(session, bot, owner, payload)
    return DONE


async def review(session: AsyncSession, bot: Bot, owner: User, req: ManagerRequest, approve: bool) -> str | None:
    """Egasi so'rovni tasdiqladi (egasi nomidan bajariladi) yoki rad etdi; boshqaruvchiga xabar boradi.
    Qaytadi: egasiga natija (tasdiqlanganda). Allaqachon ko'rib chiqilgan bo'lsa — RequestDone."""
    from bot.handlers.entries import notify_user
    from bot.web.api import ApiError

    # Bajarish xato bilan qaytsa, o'zgargan yozuvlar sessiyada eskiradi — kerakli qiymatlar oldindan olinadi
    manager_tg = req.manager.tg_id if req.manager.is_active else None
    if not await repo.claim_request(session, req, RequestStatus.APPROVED if approve else RequestStatus.REJECTED):
        raise RequestDone
    detail = None
    if approve:
        try:
            async with session.begin_nested():
                detail = await _execute(session, bot, owner, req)
        except (intake.IntakeError, ApiError) as e:
            # Qaytarilgan o'zgarishlar sessiyada eskiradi: kartochka uchun kerakli yozuvlar qayta o'qiladi
            for item in (req.manager, owner):
                await session.refresh(item)
            req.status = RequestStatus.FAILED
            req.result = f"Sababi: {html.escape(html.unescape(re.sub(r'<[^>]+>', '', e.message)))}"
            await session.flush()
    if manager_tg:
        verdict = {
            RequestStatus.APPROVED: "✅ <b>Boshliq tasdiqladi</b> — bajarildi",
            RequestStatus.REJECTED: "🚫 <b>Boshliq rad etdi</b>",
            RequestStatus.FAILED: "⚠️ <b>Boshliq tasdiqladi, lekin bajarib bo'lmadi</b>",
        }[req.status]
        await notify_user(bot, manager_tg, f"{verdict}\n\n{request_text(req, False)}", kbs.app_kb("requests"))
    return detail


async def cancel(session: AsyncSession, req: ManagerRequest) -> None:
    """Boshqaruvchi so'rovini qaytarib oldi. Allaqachon ko'rib chiqilgan bo'lsa — RequestDone."""
    if not await repo.claim_request(session, req, RequestStatus.CANCELLED):
        raise RequestDone
