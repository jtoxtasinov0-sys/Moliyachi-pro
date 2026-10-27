from aiogram import Router

from bot.handlers import admin, advice, clients, common, entries, input, money, requests


def build_router() -> Router:
    root = Router(name="root")
    # Tartib muhim: aniq buyruq va tugmalar avval, umumiy matn/ovoz (input) eng oxirida.
    # Mijoz (clients) — common dan oldin: common ro'yxatda yo'q odamning hamma xabarini oladi
    root.include_routers(
        clients.router, common.router, admin.router, money.router, advice.router, requests.router, entries.router,
        input.router,
    )
    return root
