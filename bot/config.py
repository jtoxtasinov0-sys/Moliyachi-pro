import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    owner_tg_id: int
    database_url: str = "sqlite+aiosqlite:///moliyachi.db"

    elevenlabs_api_key: str
    anthropic_api_key: str
    llm_model: str = "claude-sonnet-5-5"
    llm_advice_model: str = "claude-sonnet-5-5"

    timezone: str = "Asia/Tashkent"

    # Veb-server (Mini App + webhook). Render PORT va RENDER_EXTERNAL_URL ni o'zi beradi.
    port: int = 8080
    public_url: str | None = Field(default=None, validation_alias=AliasChoices("PUBLIC_URL", "RENDER_EXTERNAL_URL"))
    # Faqat kompyuterda sinash uchun: Telegramsiz brauzerda ilovani egasi sifatida ochish
    webapp_dev: bool = False

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
