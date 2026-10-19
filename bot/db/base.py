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
        "by_client": "BOOLEAN NOT NULL DEFAULT FALSE",
    },
    "clients": {
        "tg_id": "BIGINT",
        "invite_token": "VARCHAR(32)",
    },
    "debt_entries": {
        "stated": "BOOLEAN NOT NULL DEFAULT FALSE",
    },
    "jobs": {
        "price": "NUMERIC(14, 2)",
        "currency": "VARCHAR(3)",
        "model_free": "BOOLEAN NOT NULL DEFAULT FALSE",
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


# ALTER bilan qo'shilgan ustundan keyin: indeksi (yangi bazada create_all o'zi yaratadi) yoki boshlang'ich qiymati
_NEW_COLUMN_SQL = {
    ("clients", "tg_id"): "CREATE INDEX ix_clients_tg_id ON clients (tg_id)",
    ("clients", "invite_token"): "CREATE UNIQUE INDEX uq_clients_invite_token ON clients (invite_token)",
    # Kroy va brsovkada model so'ralmaydi (yangi kabinetda — jobs.MODEL_FREE_JOBS)
    ("jobs", "model_free"): "UPDATE jobs SET model_free = TRUE WHERE lower(name) IN ('kroychi', 'brsovkachi')",
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
                if sql := _NEW_COLUMN_SQL.get((table, name)):
                    conn.execute(text(sql))
                log.info("Ustun qo'shildi: %s.%s", table, name)


# Keyin ixtiyoriy bo'lgan ustunlar (NOT NULL olib tashlanadi)
_NULLABLE = {
    "work_entries": ("model_id",),
}


def _drop_not_null(conn: Connection) -> None:
    insp = inspect(conn)
    for table, columns in _NULLABLE.items():
        if not insp.has_table(table):
            continue
        strict = [c["name"] for c in insp.get_columns(table) if c["name"] in columns and not c["nullable"]]
        if not strict:
            continue
        if conn.dialect.name == "sqlite":
            _sqlite_rebuild(conn, table)
        else:
            for name in strict:
                conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {name} DROP NOT NULL"))
        log.info("Ustun ixtiyoriy bo'ldi: %s.%s", table, ", ".join(strict))


def _sqlite_rebuild(conn: Connection, table: str) -> None:
    """SQLite ustun cheklovini o'zgartira olmaydi: jadval yangi sxema bilan qayta yaratilib, yozuvlar ko'chiriladi."""
    insp = inspect(conn)
    old_columns = {c["name"] for c in insp.get_columns(table)}
    columns = ", ".join(c.name for c in Base.metadata.tables[table].columns if c.name in old_columns)
    for index in insp.get_indexes(table):
        conn.execute(text(f'DROP INDEX IF EXISTS "{index["name"]}"'))
    conn.execute(text(f"ALTER TABLE {table} RENAME TO {table}_old"))
    Base.metadata.tables[table].create(conn)
    conn.execute(text(f"INSERT INTO {table} ({columns}) SELECT {columns} FROM {table}_old"))
    conn.execute(text(f"DROP TABLE {table}_old"))


async def init_db() -> None:
    """Jadvallarni yaratadi (mavjud bo'lsa tegmaydi) va yangi ustunlarni qo'shadi."""
    async with engine.begin() as conn:
        await conn.run_sync(_drop_single_owner_schema)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_new_columns)
        await conn.run_sync(_drop_not_null)
