from aiogram import Router

from bot.handlers import admin, advice, common, entries, input, money, owners


def build_router() -> Router:
    root = Router(name="root")
    # Tartib muhim: aniq buyruq va tugmalar avval, umumiy matn/ovoz (input) eng oxirida
    root.include_routers(
        common.router, owners.router, admin.router, money.router, advice.router, entries.router, input.router
    )
    return root
