"""Kundalik eslatma: har kuni belgilangan soatda egaga «bugungi pulingizni kiriting» xabari.

Server (Render bepul tarifi) 15 daqiqa so'rovsiz qolsa uxlaydi va eslatma vaqti o'tib ketadi. Shuning uchun
serverda bot har 10 daqiqada o'z manziliga /health so'rovini yuborib, o'zini uyg'oq ushlab turadi.
Uxlab qolgan bo'lsa ham — uyg'ongach, shu kungi eslatma yuborilmagan bo'lsa, darhol yuboriladi.
"""
import asyncio
import logging
import time
from datetime import datetime

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

from bot import advisor
from bot import keyboards as kbs
from bot.config import settings
from bot.db import repo
from bot.db.base import session_factory

log = logging.getLogger(__name__)

CHECK_EVERY = 60  # soniya
PING_EVERY = 10 * 60


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


async def _ping(http: aiohttp.ClientSession) -> None:
    try:
        async with http.get(f"{settings.base_url}/health", timeout=aiohttp.ClientTimeout(total=20)) as resp:
            await resp.read()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning("O'zini uyg'otish so'rovi o'tmadi: %s", e)


async def run(bot: Bot) -> None:
    keep_awake = settings.keep_awake and bool(settings.webapp_url)
    last_ping = 0.0
    async with aiohttp.ClientSession() as http:
        while True:
            try:
                await send_due(bot)
            except Exception:
                log.exception("Eslatmalarni tekshirishda xato")
            if keep_awake and time.monotonic() - last_ping >= PING_EVERY:
                last_ping = time.monotonic()
                await _ping(http)
            await asyncio.sleep(CHECK_EVERY)
