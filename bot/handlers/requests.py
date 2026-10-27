"""Ish boshqaruvchining so'rovlari: egasi tasdiqlaydi yoki rad etadi, boshqaruvchi o'z so'rovlarini ko'radi."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import approvals
from bot import keyboards as kbs
from bot.db import repo
from bot.db.models import RequestStatus, User
from bot.middlewares import Registered

router = Router(name="requests")
router.message.filter(Registered())
router.callback_query.filter(Registered())

MY_REQUESTS = 10
# Tasdiqlangandan keyingi natija (ishchilarga xabar borgani, qoldiqlar) — so'rov tavsifi bilan bitta xabarga sig'sin
DETAIL_LIMIT = 900


@router.callback_query(kbs.ReqCb.filter())
async def on_request(call: CallbackQuery, callback_data: kbs.ReqCb, session: AsyncSession, user: User) -> None:
    req = await repo.get_request(session, user.account_id, callback_data.req_id)
    if req is None or not (user.is_owner or req.manager_id == user.id):
        await call.answer("So'rov topilmadi.", show_alert=True)
        return
    action = callback_data.action
    if action not in ({"approve", "reject"} if user.is_owner else {"cancel"}):
        await call.answer("Ruxsat yo'q.", show_alert=True)
        return
    detail = None
    try:
        if action == "cancel":
            await approvals.cancel(session, req)
        else:
            detail = await approvals.review(session, call.bot, user, req, action == "approve")
    except approvals.RequestDone:
        status = approvals.STATUS_LABELS[req.status]
        await call.answer(f"Bu so'rov allaqachon ko'rib chiqilgan: {status}", show_alert=True)
        await call.message.edit_text(approvals.request_text(req, user.is_owner), reply_markup=kbs.request_kb(req, user))
        return
    text = approvals.request_text(req, user.is_owner)
    if detail and detail != approvals.DONE:
        text += f"\n\n{approvals.clip(detail, DETAIL_LIMIT)}"
    await call.message.edit_text(text, reply_markup=kbs.request_kb(req, user))
    answers = {
        RequestStatus.APPROVED: "✅ Tasdiqlandi — bajarildi",
        RequestStatus.REJECTED: "🚫 Rad etildi",
        RequestStatus.FAILED: "⚠️ Bajarib bo'lmadi",
        RequestStatus.CANCELLED: "↩️ So'rov qaytarib olindi",
    }
    await call.answer(answers.get(req.status))


@router.message(F.text == kbs.BTN_REQUESTS)
async def my_requests(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    if not user.is_manager:
        await message.answer("📨 So'rovlar — ish boshqaruvchida.")
        return
    items = await repo.list_requests(session, user.account_id, manager_id=user.id, limit=MY_REQUESTS)
    if not items:
        await message.answer(
            "📨 Hali so'rov yo'q.\n\nIsh, mijoz, xom ashyo yoki narxni ovoz yoki matn bilan yozing — boshliq "
            "tasdig'iga boradi, tasdiqlasa bajariladi.",
            reply_markup=kbs.app_kb(),
        )
        return
    waiting = await repo.count_requests(session, user.account_id, user.id)
    await message.answer(
        f"📨 Oxirgi {len(items)} ta so'rov" + (f" · ⏳ {waiting} tasi tasdiq kutmoqda" if waiting else "")
    )
    for req in reversed(items):
        await message.answer(approvals.request_text(req, False), reply_markup=kbs.request_kb(req, user))
