import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent, MenuButtonCommands, MenuButtonWebApp, WebAppInfo
from aiohttp import web

from bot.config import settings
from bot import reminders
from bot.db.base import init_db
from bot.handlers import build_router
from bot.middlewares import DbUserMiddleware
from bot.monitor import report_error
from bot.monitor import setup as setup_monitor
from bot.web.server import WEBHOOK_PATH, build_app

log = logging.getLogger("bot")


async def on_error(event: ErrorEvent, bot: Bot) -> bool:
    if isinstance(event.exception, TelegramBadRequest) and "message is not modified" in str(event.exception):
        return True  # bir xil tugma ikki marta bosilgan — e'tibor bermaymiz
    update = event.update
    chat_id = None
    if update.message:
        chat_id = update.message.chat.id
    elif update.callback_query and update.callback_query.message:
        chat_id = update.callback_query.message.chat.id
    report_error(chat_id, event.exception, "Botda: ")
    log.exception("Kutilmagan xato", exc_info=event.exception)
    if chat_id:
        try:
            await bot.send_message(chat_id, "⚠️ Xatolik yuz berdi. Qayta urinib ko'ring.")
        except Exception:
            pass
    return True


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await init_db()

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(DbUserMiddleware())
    dp.errors.register(on_error)
    dp.include_router(build_router())

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Bosh menyu"),
            BotCommand(command="hisobot", description="Hisobot"),
            BotCommand(command="oxirgi", description="Oxirgi yozuvlar"),
            BotCommand(command="maslahat", description="Moliyaviy maslahat va reja"),
            BotCommand(command="web", description="Ilovani brauzerda ochish"),
            BotCommand(command="yordam", description="Yordam"),
        ]
    )
    if settings.webapp_url:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="📊 Ilova", web_app=WebAppInfo(url=settings.webapp_url))
        )
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    # Serverda (HTTPS manzil bor) — webhook; kompyuterda — polling
    use_webhook = bool(settings.webapp_url)
    monitor = setup_monitor(bot, dp, use_webhook)
    app = build_app(bot, dp, with_webhook=use_webhook)
    if monitor:
        monitor.register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", settings.port).start()
    log.info("Veb-server: http://localhost:%s/app/", settings.port)

    me = await bot.me()
    log.info("Bot ishga tushdi: @%s", me.username)
    if monitor:
        try:
            await monitor.start()
        except Exception:
            # Kuzatuv boti ishlamasa ham asosiy bot ishlayversin
            log.warning("Kuzatuv boti ishga tushmadi — MONITOR_BOT_TOKEN ni tekshiring", exc_info=True)
            await monitor.stop()
            monitor = None
    reminder_task = asyncio.create_task(reminders.run(bot))
    try:
        if use_webhook:
            await bot.set_webhook(
                f"{settings.base_url}{WEBHOOK_PATH}",
                secret_token=settings.webhook_secret,
                allowed_updates=dp.resolve_used_update_types(),
            )
            log.info("Webhook: %s%s", settings.base_url, WEBHOOK_PATH)
            await asyncio.Event().wait()
        else:
            await bot.delete_webhook()
            await dp.start_polling(bot)
    finally:
        reminder_task.cancel()
        if monitor:
            await monitor.stop()
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
