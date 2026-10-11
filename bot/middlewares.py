from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.filters import Filter
from aiogram.types import TelegramObject

from bot.config import settings
from bot.db import repo
from bot.db.base import session_factory
from bot.db.models import User


class DbUserMiddleware(BaseMiddleware):
    """Har bir update uchun baza sessiyasi ochadi va foydalanuvchini aniqlaydi.

    Handler xatosiz tugasa — o'zgarishlar saqlanadi (commit), xato bo'lsa — bekor qilinadi.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with session_factory() as session:
            data["session"] = session
            tg_user = data.get("event_from_user")
            user = None
            if tg_user is not None:
                if tg_user.id in settings.owner_ids:
                    user = await repo.ensure_owner(session, tg_user.id, tg_user.full_name)
                else:
                    user = await repo.get_user_by_tg(session, tg_user.id)
            data["user"] = user
            result = await handler(event, data)
            await session.commit()
            return result


class Registered(Filter):
    async def __call__(self, event: TelegramObject, user: User | None = None) -> bool:
        return user is not None and user.is_active


class Guest(Filter):
    async def __call__(self, event: TelegramObject, user: User | None = None) -> bool:
        return user is None or not user.is_active


class IsOwner(Filter):
    async def __call__(self, event: TelegramObject, user: User | None = None) -> bool:
        return user is not None and user.is_owner
