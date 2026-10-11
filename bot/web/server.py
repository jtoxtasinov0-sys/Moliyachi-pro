"""Veb-server: Mini App sahifalari, JSON API va Telegram webhook."""
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web

from bot.config import settings
from bot.web.api import routes as api_routes

STATIC_DIR = Path(__file__).parent / "static"
WEBHOOK_PATH = "/tg/webhook"


@web.middleware
async def no_cache_html(request: web.Request, handler):
    response = await handler(request)
    if request.path.startswith("/app"):
        # Telegram ichidagi brauzer eski fayllarni ushlab qolmasligi uchun
        response.headers["Cache-Control"] = "no-cache"
    return response


async def _index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def _health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def _root(request: web.Request) -> web.Response:
    raise web.HTTPFound("/app/")


def build_app(bot: Bot, dp: Dispatcher, with_webhook: bool) -> web.Application:
    app = web.Application(middlewares=[no_cache_html], client_max_size=10 * 1024**2)  # ilovadan ovoz yuborish uchun
    app["bot"] = bot
    app.router.add_get("/", _root)
    app.router.add_get("/health", _health)
    app.router.add_get("/app", _root)
    app.router.add_get("/app/", _index)
    app.router.add_static("/app/static/", STATIC_DIR)
    app.add_routes(api_routes)
    if with_webhook:
        SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=settings.webhook_secret).register(
            app, path=WEBHOOK_PATH
        )
    return app
