from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from bot.config import settings
from bot.db.models import Base

_url, _connect_args = settings.db_engine_args()
engine: AsyncEngine = create_async_engine(_url, connect_args=_connect_args, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """Jadvallarni yaratadi (mavjud bo'lsa tegmaydi)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
