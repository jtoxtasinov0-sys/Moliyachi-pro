import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    # Bir nechta ega bo'lsa — vergul bilan: 111,222
    owner_tg_id: str
    database_url: str = "sqlite+aiosqlite:///moliyachi.db"

    # Ovozni matnga: Aisha / VoiceLab (asosiy), ishlamasa — ElevenLabs (zaxira).
    # Ikkalasi ham bo'lmasa — bot ishlaydi, faqat ovozli kiritish ishlamaydi
    aisha_api_key: str | None = None
    elevenlabs_api_key: str | None = None
    anthropic_api_key: str
    llm_model: str = "claude-sonnet-5-5"
    llm_advice_model: str = "claude-sonnet-5-5"

    timezone: str = "Asia/Tashkent"

    # Veb-server (Mini App + webhook). Render PORT va RENDER_EXTERNAL_URL ni o'zi beradi.
    port: int = 8080
    public_url: str | None = Field(default=None, validation_alias=AliasChoices("PUBLIC_URL", "RENDER_EXTERNAL_URL"))
    # Serverda o'zini uyg'oq ushlab turish (Render bepul tarifi uxlab qolsa, kundalik eslatma kechikadi)
    keep_awake: bool = True
    # Faqat kompyuterda sinash uchun: Telegramsiz brauzerda ilovani egasi sifatida ochish
    webapp_dev: bool = False

    # Kuzatuv boti (ixtiyoriy): @BotFather dan alohida bot tokeni. Bo'lsa — foydalanuvchilar nima yozgani,
    # bot nima javob bergani, ilovada nima qilgani va xatolar shu botga keladi.
    monitor_bot_token: str | None = None
    # Kuzatuv botini kim ishlata oladi. Yozilmasa — OWNER_TG_ID dagi birinchi ID
    admin_tg_id: str | None = None

    @field_validator("owner_tg_id")
    @classmethod
    def _check_owner_ids(cls, value: str) -> str:
        ids = [part.strip() for part in value.split(",") if part.strip()]
        if not ids or not all(part.isdigit() for part in ids):
            raise ValueError("OWNER_TG_ID — Telegram ID raqamlari, bir nechta bo'lsa vergul bilan: 111,222")
        return ",".join(ids)

    @field_validator("monitor_bot_token")
    @classmethod
    def _empty_token(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @field_validator("admin_tg_id")
    @classmethod
    def _check_admin_ids(cls, value: str | None) -> str | None:
        ids = [part.strip() for part in (value or "").split(",") if part.strip()]
        if not all(part.isdigit() for part in ids):
            raise ValueError("ADMIN_TG_ID — Telegram ID raqamlari, bir nechta bo'lsa vergul bilan: 111,222")
        return ",".join(ids) or None

    @property
    def owner_ids(self) -> tuple[int, ...]:
        return tuple(int(part) for part in self.owner_tg_id.split(","))

    @property
    def admin_ids(self) -> tuple[int, ...]:
        if self.admin_tg_id:
            return tuple(int(part) for part in self.admin_tg_id.split(","))
        return self.owner_ids[:1]

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def base_url(self) -> str | None:
        return self.public_url.rstrip("/") if self.public_url else None

    @property
    def webapp_url(self) -> str | None:
        """Telegram Mini App faqat HTTPS da ochiladi."""
        if self.base_url and self.base_url.startswith("https://"):
            return f"{self.base_url}/app/"
        return None

    @property
    def webhook_secret(self) -> str:
        return hashlib.sha256(f"webhook:{self.bot_token}".encode()).hexdigest()[:48]

    def db_engine_args(self) -> tuple[str, dict]:
        """Neon/Render bergan "postgres://...?sslmode=require" manzilini asyncpg ga moslaydi."""
        url = self.database_url
        if not url.startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")):
            return url, {}
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query))
        connect_args: dict = {}
        if query.pop("sslmode", None) not in (None, "disable"):
            connect_args["ssl"] = "require"
        query.pop("channel_binding", None)
        if "-pooler" in (parts.hostname or ""):
            # PgBouncer (Neon pooler) tayyorlangan so'rovlarni saqlamaydi
            connect_args["statement_cache_size"] = 0
            query["prepared_statement_cache_size"] = "0"
        url = urlunsplit(("postgresql+asyncpg", parts.netloc, parts.path, urlencode(query), parts.fragment))
        return url, connect_args


settings = Settings()
