"""Kundalik eslatma: har kuni belgilangan soatda egaga «bugungi pulingizni kiriting» xabari.
Obuna tugashi haqida ogohlantirish: 3 kun qolganda va tugaganda.

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

from bot import advisor, billing
from bot import keyboards as kbs
from bot.config import settings
from bot.db import repo
from bot.db.base import session_factory
from bot.db.models import User

log = logging.getLogger(__name__)

CHECK_EVERY = 60  # soniya
PING_EVERY = 10 * 60


async def _send(bot: Bot, tg_id: int, text: str, **kwargs) -> None:
    try:
        await bot.send_message(tg_id, text, **kwargs)
    except TelegramForbiddenError:
        log.info("Xabar yuborilmadi: %s botni bloklagan", tg_id)
    except TelegramAPIError:
        log.exception("Xabar yuborilmadi: %s", tg_id)


async def send_due(bot: Bot) -> None:
    """Eslatma soati kelgan va bugun hali eslatma olmagan egalarga yuboradi."""
    now = datetime.now(settings.tz)
    day = now.date().isoformat()
    async with session_factory() as session:
        owner_ids = [u.id for u in await repo.list_owners(session) if u.tg_id and u.is_active]
    for owner_id in owner_ids:
        async with session_factory() as session:
            user = await session.get(User, owner_id)
            if await billing.access_error(session, user):
                continue  # obuna tugagan
            hour = await repo.reminder_hour(session, user.id)
            if hour is None or now.hour < hour:
                continue
            if await repo.get_setting(session, user.id, "reminder_sent") == day:
                continue
            # Avval belgilab qo'yamiz: yuborishda xato bo'lsa ham har daqiqada qayta urinmaydi
            await repo.set_setting(session, user.id, "reminder_sent", day)
            await session.commit()
            text = await advisor.reminder_text(session, user.id)
            tg_id = user.tg_id
        await _send(bot, tg_id, text, reply_markup=kbs.reminder_kb())


NOTICE_HOUR = 9
SOON_DAYS = 3


async def send_subscription_notices(bot: Bot) -> None:
    """Obuna tugashiga 3 kun qolganda va tugaganda egaga (tugaganda — bosh adminga ham) bir marta xabar."""
    if datetime.now(settings.tz).hour < NOTICE_HOUR:
        return
    async with session_factory() as session:
        for owner in await repo.list_owners(session):
            if billing.is_admin(owner) or not owner.is_active or not owner.tg_id or owner.paid_until is None:
                continue
            left = (owner.paid_until - billing.today()).days
            stage = "ended" if left < 0 else "soon" if left <= SOON_DAYS else None
            key = f"{owner.paid_until.isoformat()}:{stage}"
            if stage is None or await repo.get_setting(session, owner.id, "sub_notice") == key:
                continue
            await repo.set_setting(session, owner.id, "sub_notice", key)
            await session.commit()
            until = owner.paid_until.strftime("%d.%m.%Y")
            if stage == "soon":
                when = "bugun oxirgi kun" if left == 0 else f"{left} kun qoldi"
                await _send(
                    bot, owner.tg_id,
                    f"🔔 Obunangiz <b>{until}</b> gacha ({when}). Bot to'xtab qolmasligi uchun "
                    f"to'lovni oldindan qiling.{billing.contact_line()}",
                )
            else:
                await _send(
                    bot, owner.tg_id,
                    f"⛔ Obunangiz {until} da tugadi — bot siz va ishchilaringiz uchun to'xtadi. "
                    f"To'lov qilinganda ma'lumotlaringiz bilan birga qayta yoqiladi.{billing.contact_line()}",
                )
                for admin_id in settings.owner_ids:
                    await _send(bot, admin_id, f"⛔ <b>{escape(owner.name)}</b> obunasi tugadi ({until}). /egalar")


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
                await send_subscription_notices(bot)
            except Exception:
                log.exception("Eslatmalarni tekshirishda xato")
            if keep_awake and time.monotonic() - last_ping >= PING_EVERY:
                last_ping = time.monotonic()
                await _ping(http)
            await asyncio.sleep(CHECK_EVERY)
