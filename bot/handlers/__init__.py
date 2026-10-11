from aiogram import Router

from bot.handlers import admin, common, entries, input


def build_router() -> Router:
    root = Router(name="root")
    # Tartib muhim: aniq buyruq va tugmalar avval, umumiy matn/ovoz (input) eng oxirida
    root.include_routers(common.router, admin.router, entries.router, input.router)
    return root
