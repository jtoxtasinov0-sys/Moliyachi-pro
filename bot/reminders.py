"""Kundalik eslatma: har kuni belgilangan soatda egaga «bugungi pulingizni kiriting» xabari. Ishchi yozgan pul
(«500 ming oldim») tasdiqlanmay qolgan bo'lsa — har kuni ertalab «tasdiqlanmagan pul bor» xabari.

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

from bot import advisor
from bot import keyboards as kbs
from bot.config import settings
from bot.db import repo
from bot.db.base import session_factory
from bot.db.models import EntryStatus, WorkerPayment

log = logging.getLogger(__name__)

CHECK_EVERY = 60  # soniya
PING_EVERY = 10 * 60
# Tasdiqlanmagan pul eslatmasi — har kuni shu soatdan (mahalliy vaqt), tasdiqlash tugmalari bilan shuncha yozuv
PAY_REMINDER_HOUR = 9
PAY_REMINDER_CARDS = 10


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


def _pending_pays_text(pending: list[WorkerPayment]) -> str:
    per_worker: dict[str, int] = {}
    for e in pending:
        per_worker[e.worker.name] = per_worker.get(e.worker.name, 0) + 1
    names = ", ".join(f"{escape(name)} — {count} ta" for name, count in per_worker.items())
    text = (
        f"🔔 <b>Tasdiqlanmagan pul bor</b> — {len(pending)} ta yozuv\n👷 {names}\n\n"
        "Tasdiqlamaguningizcha ishchi qoldig'iga kirmaydi. Pastda har birini ✅ tasdiqlang yoki 🚫 rad eting."
    )
    if len(pending) > PAY_REMINDER_CARDS:
        text += (
            f"\n\nPastda — eng eskilari ({PAY_REMINDER_CARDS} ta), qolganlari — «👷 Ishchilar» bo'limida yoki ilovada."
        )
    return text


async def send_pending_pays(bot: Bot) -> None:
    """Ishchi yozgan pul tasdiqlanmay qolgan bo'lsa — egaga kuniga bir marta eslatma va har bir yozuv
    tasdiqlash tugmalari bilan (eskisi birinchi)."""
    from bot.handlers.entries import pay_text  # aylanma importdan qochish uchun

    now = datetime.now(settings.tz)
    if now.hour < PAY_REMINDER_HOUR:
        return
    day = now.date().isoformat()
    for tg_id in settings.owner_ids:
        async with session_factory() as session:
            user = await repo.get_user_by_tg(session, tg_id)
            if user is None or not user.is_owner:
                continue
            if await repo.get_setting(session, user.id, "pay_reminder_sent") == day:
                continue
            # Bugun tekshirildi: keyin yozilgan pul haqida egasiga darhol xabar boradi, eslatmasi — ertaga
            await repo.set_setting(session, user.id, "pay_reminder_sent", day)
            await session.commit()
            pending = await repo.list_payments(session, user.id, status=EntryStatus.PENDING, limit=200)
            if not pending:
                continue
            pending.reverse()
            messages = [(_pending_pays_text(pending), kbs.app_kb())]
            messages += [(pay_text(e, user), kbs.pay_entry_kb(e, user)) for e in pending[:PAY_REMINDER_CARDS]]
        try:
            for text, markup in messages:
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
    async with aiohttp.ClientSession() as http:
        while True:
            try:
                await send_due(bot)
            except Exception:
                log.exception("Eslatmalarni tekshirishda xato")
            try:
                await send_pending_pays(bot)
            except Exception:
                log.exception("Tasdiqlanmagan pul eslatmasini tekshirishda xato")
            if keep_awake and time.monotonic() - last_ping >= PING_EVERY:
                last_ping = time.monotonic()
                await _ping(http)
            await asyncio.sleep(CHECK_EVERY)
