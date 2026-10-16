"""Kuzatuv boti (ixtiyoriy): MONITOR_BOT_TOKEN berilsa ishlaydi.

Asosiy botdan foydalanayotganlar nima yozgani, bot nima javob bergani, ilovada nima qilgani va xatolar
bazaga (activity_log) yoziladi va alohida botda adminga (ADMIN_TG_ID) ko'rsatiladi.
"""
import logging

from aiogram import Bot, Dispatcher

from bot.config import settings
from bot.monitor import tracker
from bot.monitor.hooks import ErrorLogHandler, TrackRequests, TrackUpdates, heard, report_error, track_api

__all__ = ["heard", "report_error", "setup", "track_api"]


def setup(main_bot: Bot, dp: Dispatcher, use_webhook: bool):
    """Asosiy botga kuzatuvni ulaydi va kuzatuv botini qaytaradi (token bo'lmasa — None)."""
    if not settings.monitor_bot_token:
        return None
    from bot.monitor.bot import Monitor

    tracker.init()
    dp.update.outer_middleware(TrackUpdates())
    main_bot.session.middleware(TrackRequests())
    logging.getLogger().addHandler(ErrorLogHandler())
    return Monitor(main_bot, use_webhook)
