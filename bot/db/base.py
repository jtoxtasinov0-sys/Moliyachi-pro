import logging

from sqlalchemy import Connection, MetaData, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from bot.config import settings
from bot.db.models import Base

log = logging.getLogger(__name__)

_url, _connect_args = settings.db_engine_args()
engine: AsyncEngine = create_async_engine(_url, connect_args=_connect_args, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


def _drop_single_owner_schema(conn: Connection) -> None:
    """Eski (bitta egali, kabinetsiz) sxemani o'chiradi — faqat unda yozuvlar bo'lmasa."""
    insp = inspect(conn)
    if not insp.has_table("users") or "owner_id" in {c["name"] for c in insp.get_columns("users")}:
        return
    for table in ("work_entries", "client_entries", "personal_entries"):
        if insp.has_table(table) and conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar():
            raise RuntimeError(f"Eski bazaning {table} jadvalida yozuvlar bor — avtomatik yangilab bo'lmaydi")
    old = MetaData()
    old.reflect(conn)
    old.drop_all(conn)
    log.warning("Eski sxema o'chirildi, jadvallar kabinetlar bilan qayta yaratiladi")


# Keyin qo'shilgan ustunlar: jadval avvaldan bo'lsa ALTER TABLE bilan qo'shiladi
_NEW_COLUMNS = {
    "client_entries": {
        "product": "VARCHAR(100)",
        "pieces": "INTEGER",
        "unit_price": "NUMERIC(14, 2)",
        "price_unit": "VARCHAR(5)",
        "note": "TEXT",
    },
    "debt_entries": {
        "stated": "BOOLEAN NOT NULL DEFAULT FALSE",
    },
    "material_entries": {
        "supplier_id": "INTEGER",
    },
    "users": {
        "job_id": "INTEGER",
    },
    "work_entries": {
        "job_id": "INTEGER",
    },
}


def _add_new_columns(conn: Connection) -> None:
    insp = inspect(conn)
    for table, columns in _NEW_COLUMNS.items():
        if not insp.has_table(table):
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        for name, sql_type in columns.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
                log.info("Ustun qo'shildi: %s.%s", table, name)


async def init_db() -> None:
    """Jadvallarni yaratadi (mavjud bo'lsa tegmaydi) va yangi ustunlarni qo'shadi."""
    async with engine.begin() as conn:
        await conn.run_sync(_drop_single_owner_schema)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_new_columns)
