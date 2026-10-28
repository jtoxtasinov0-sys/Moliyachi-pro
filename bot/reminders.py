"""Kundalik eslatma: har kuni belgilangan soatda egaga «bugungi pulingizni kiriting» xabari. Ishchi yozgan ish yoki
pul («500 ming oldim»), ish boshqaruvchining so'rovi tasdiqlanmay qolgan bo'lsa — har kuni ertalab bir marta eslatma.

Server (Render bepul tarifi) 15 daqiqa so'rovsiz qolsa uxlaydi va eslatma vaqti o'tib ketadi. Shuning uchun
serverda bot har 10 daqiqada o'z manziliga /health so'rovini yuborib, o'zini uyg'oq ushlab turadi.
Uxlab qolgan bo'lsa ham — uyg'ongach, shu kungi eslatma yuborilmagan bo'lsa, darhol yuboriladi.
"""
import asyncio
import logging
import time
from datetime import datetime
from html import escape

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from bot import advisor
from bot import keyboards as kbs
from bot.config import settings
from bot.db import repo
from bot.db.base import session_factory
from bot.db.models import EntryStatus, RequestStatus, User

log = logging.getLogger(__name__)

CHECK_EVERY = 60  # soniya
PING_EVERY = 10 * 60
# Tasdiqlanmagan pul eslatmasi — har kuni shu soatdan (mahalliy vaqt), tasdiqlash tugmalari bilan shuncha yozuv
PAY_REMINDER_HOUR = 9
PAY_REMINDER_CARDS = 10
# Bot yangilangach tasdiq kutayotganlar eslatmasi soatini kutmasdan bir marta yuboriladi — shu qiymat o'zgarsa
PENDING_NOTICE_VERSION = "1"
# Ketma-ket xabarlar orasida (Telegram bir chatga juda tez yuborilganini to'xtatadi)
SEND_PAUSE = 0.5


async def send_due(bot: Bot) -> None:
    """Eslatma soati kelgan va bugun hali eslatma olmagan egalarga yuboradi."""
    now = datetime.now(settings.tz)
    day = now.date().isoformat()
    for tg_id in settings.owner_ids:
        async with session_factory() as session:
            user = await repo.get_user_by_tg(session, tg_id)
            if user is None or not user.is_owner:
                continue  # botni hali ochmagan
            hour = await repo.reminder_hour(session, user.id)
            if hour is None or now.hour < hour:
                continue
            if await repo.get_setting(session, user.id, "reminder_sent") == day:
                continue
            # Avval belgilab qo'yamiz: yuborishda xato bo'lsa ham har daqiqada qayta urinmaydi
            await repo.set_setting(session, user.id, "reminder_sent", day)
            await session.commit()
            text = await advisor.reminder_text(session, user.id)
        try:
            await bot.send_message(tg_id, text, reply_markup=kbs.reminder_kb())
        except TelegramForbiddenError:
            log.info("Eslatma yuborilmadi: %s botni bloklagan", tg_id)
        except TelegramAPIError:
            log.exception("Eslatma yuborilmadi: %s", tg_id)


def _digest_head(title: str, names: list[str], note: str) -> str:
    """«🔔 Tasdiqlanmagan pul bor — 3 ta yozuv», kimniki nechta («👷 Robiya — 2 ta, Ali — 1 ta») va izoh."""
    per_worker: dict[str, int] = {}
    for name in names:
        per_worker[name] = per_worker.get(name, 0) + 1
    who = ", ".join(f"{escape(name)} — {count} ta" for name, count in per_worker.items())
    text = (
        f"🔔 <b>{title}</b> — {len(names)} ta yozuv\n👷 {who}\n\n"
        f"{note} Pastda har birini ✅ tasdiqlang yoki 🚫 rad eting."
    )
    if len(names) > PAY_REMINDER_CARDS:
        text += f"\nPastda — eng eskilari ({PAY_REMINDER_CARDS} ta)."
    return text + "\n📍 Hammasi — menyudagi «⏳ Tasdiq kutayotganlar» bo'limida."


async def _pending_messages(session: AsyncSession, user: User) -> list[tuple[str, InlineKeyboardMarkup | None]]:
    """Tasdiq kutayotgan ish va pul yozuvlari, ish boshqaruvchi so'rovlari: sarlavha va har biri tugmalari bilan
    (eskisi birinchi). Hech narsa bo'lmasa — bo'sh."""
    from bot import approvals  # aylanma importdan qochish uchun
    from bot.handlers.entries import entry_text, pay_text

    messages: list[tuple[str, InlineKeyboardMarkup | None]] = []
    works = await repo.pending_work_entries(session, user.id)
    if works:
        note = "Tasdiqlamaguningizcha ishchi haqqiga (qoldig'iga) kirmaydi."
        messages.append((_digest_head("Tasdiqlanmagan ish bor", [e.worker.name for e in works], note), kbs.app_kb()))
        messages += [(entry_text(e), kbs.entry_kb(e, user)) for e in works[:PAY_REMINDER_CARDS]]
    pending = await repo.list_payments(session, user.id, status=EntryStatus.PENDING, limit=200)
    if pending:
        pending.reverse()
        note = "Tasdiqlamaguningizcha ishchi qoldig'iga kirmaydi."
        messages.append((_digest_head("Tasdiqlanmagan pul bor", [e.worker.name for e in pending], note), kbs.app_kb()))
        messages += [(pay_text(e, user), kbs.pay_entry_kb(e, user)) for e in pending[:PAY_REMINDER_CARDS]]
    requests = await repo.list_requests(session, user.id, status=RequestStatus.PENDING, limit=200)
    if requests:
        requests.reverse()
        messages.append((
            f"🔔 <b>Ish boshqaruvchi so'rovlari tasdiq kutmoqda</b> — {len(requests)} ta\n\n"
            "Tasdiqlamaguningizcha bajarilmaydi. Pastda har birini ✅ tasdiqlang yoki 🚫 rad eting.",
            kbs.app_kb("requests"),
        ))
        messages += [
            (approvals.request_text(r, True), kbs.request_kb(r, user)) for r in requests[:PAY_REMINDER_CARDS]
        ]
    return messages


async def send_pending_pays(bot: Bot, now_once: bool = False) -> None:
    """Ishchi yozgan ish yoki pul, ish boshqaruvchining so'rovi tasdiqlanmay qolgan bo'lsa — egaga kuniga bir marta
    (PAY_REMINDER_HOUR dan) eslatma. now_once — bot yangilangach soatini kutmasdan bir marta (PENDING_NOTICE_VERSION):
    shu kungi eslatma o'rniga o'tadi, kuniga bir martadan oshmaydi."""
    now = datetime.now(settings.tz)
    if not now_once and now.hour < PAY_REMINDER_HOUR:
        return
    day = now.date().isoformat()
    for tg_id in settings.owner_ids:
        async with session_factory() as session:
            user = await repo.get_user_by_tg(session, tg_id)
            if user is None or not user.is_owner:
                continue
            if now_once:
                if await repo.get_setting(session, user.id, "pending_notice_version") == PENDING_NOTICE_VERSION:
                    continue
                await repo.set_setting(session, user.id, "pending_notice_version", PENDING_NOTICE_VERSION)
            elif await repo.get_setting(session, user.id, "pay_reminder_sent") == day:
                continue
            # Bugun tekshirildi: keyin yozilgani haqida egasiga darhol xabar boradi, eslatmasi — ertaga
            await repo.set_setting(session, user.id, "pay_reminder_sent", day)
            await session.commit()
            messages = await _pending_messages(session, user)
        try:
            for i, (text, markup) in enumerate(messages):
                if i:
                    await asyncio.sleep(SEND_PAUSE)
                await bot.send_message(tg_id, text, reply_markup=markup)
        except TelegramForbiddenError:
            log.info("Tasdiq eslatmasi yuborilmadi: %s botni bloklagan", tg_id)
        except TelegramAPIError:
            log.exception("Tasdiq eslatmasi yuborilmadi: %s", tg_id)


async def _ping(http: aiohttp.ClientSession) -> None:
    try:
        async with http.get(f"{settings.base_url}/health", timeout=aiohttp.ClientTimeout(total=20)) as resp:
            await resp.read()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning("O'zini uyg'otish so'rovi o'tmadi: %s", e)


MENU_NEWS = "📋 Menyu yangilandi: «🏪 Ta'minotchilar» qo'shildi — pastdagi tugmalardan foydalaning."


async def refresh_menus(bot: Bot) -> None:
    """Menyu o'zgargan bo'lsa (keyboards.MENU_VERSION) — har bir egaga yangi tugmalar bir marta yuboriladi:
    Telegram eski menyuni o'zi almashtirmaydi, yangi bo'lim tugmasi /start bosilmaguncha ko'rinmaydi."""
    for tg_id in settings.owner_ids:
        async with session_factory() as session:
            user = await repo.get_user_by_tg(session, tg_id)
            if user is None or not user.is_owner:
                continue  # botni hali ochmagan — /start da yangi menyu oladi
            if await repo.get_setting(session, user.id, "menu_version") == kbs.MENU_VERSION:
                continue
            await repo.set_setting(session, user.id, "menu_version", kbs.MENU_VERSION)
            await session.commit()
        try:
            await bot.send_message(tg_id, MENU_NEWS, reply_markup=kbs.main_menu(user))
        except TelegramForbiddenError:
            log.info("Yangi menyu yuborilmadi: %s botni bloklagan", tg_id)
        except TelegramAPIError:
            log.exception("Yangi menyu yuborilmadi: %s", tg_id)


async def run(bot: Bot) -> None:
    keep_awake = settings.keep_awake and bool(settings.webapp_url)
    last_ping = 0.0
    try:
        await refresh_menus(bot)
    except Exception:
        log.exception("Yangi menyuni yuborishda xato")
    try:
        await send_pending_pays(bot, now_once=True)
    except Exception:
        log.exception("Tasdiq kutayotganlar eslatmasini yuborishda xato")
    async with aiohttp.ClientSession() as http:
        while True:
            try:
                await send_due(bot)
            except Exception:
                log.exception("Eslatmalarni tekshirishda xato")
            try:
                await send_pending_pays(bot)
            except Exception:
                log.exception("Tasdiq kutayotganlar eslatmasini tekshirishda xato")
            if keep_awake and time.monotonic() - last_ping >= PING_EVERY:
                last_ping = time.monotonic()
                await _ping(http)
            await asyncio.sleep(CHECK_EVERY)
