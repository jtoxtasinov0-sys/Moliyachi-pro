from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.filters import Filter
from aiogram.types import TelegramObject, Update

from bot import billing
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
            # To'xtatilgan ega ham «ro'yxatda yo'qsiz» emas, «obuna to'xtatilgan» xabarini oladi
            if user is not None and (user.is_active or user.is_owner):
                blocked = await billing.access_error(session, user)
                if blocked:
                    await _tell_blocked(event, blocked)
                    return None
            result = await handler(event, data)
            await session.commit()
            return result


async def _tell_blocked(event: TelegramObject, text: str) -> None:
    """Obunasi tugagan yoki to'xtatilgan kabinet: handler o'rniga shu xabar."""
    if not isinstance(event, Update):
        return
    if event.message:
        await event.message.answer(text)
    elif event.callback_query:
        await event.callback_query.answer(text[:200], show_alert=True)


class Registered(Filter):
    async def __call__(self, event: TelegramObject, user: User | None = None) -> bool:
        return user is not None and user.is_active


class Guest(Filter):
    async def __call__(self, event: TelegramObject, user: User | None = None) -> bool:
        return user is None or not user.is_active


class IsOwner(Filter):
    async def __call__(self, event: TelegramObject, user: User | None = None) -> bool:
        return user is not None and user.is_owner


class IsAdmin(Filter):
    """Bosh admin: OWNER_TG_ID dagi ID."""

    async def __call__(self, event: TelegramObject, user: User | None = None) -> bool:
        return user is not None and user.is_owner and settings.is_admin(user.tg_id)
