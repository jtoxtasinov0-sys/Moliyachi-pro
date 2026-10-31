"""Kuzatuv yadrosi: har bir harakat (kim nima yozdi, bot nima javob berdi, ilovada nima qildi, xatolar)
bazaga yoziladi va kuzatuv botiga jonli lenta bo'lib boradi.

Asosiy botni sekinlashtirmaslik uchun harakat navbatga (asyncio.Queue) qo'yiladi, bazaga yozish va adminga
yuborish — fonda. Kuzatuvda xato bo'lsa ham asosiy bot ishlashda davom etadi.

Lenta: bitta odamning suhbati (u yozgani + bot javoblari) bir xabarga yig'iladi va suhbat ~25 soniya jim
tursa yuboriladi — har bir xabar alohida kelib, telefonni to'ldirib yubormasin. Xatolar — darhol.
"""
import asyncio
import html
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, LinkPreviewOptions
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select, update

from bot.config import settings
from bot.db.base import session_factory
from bot.db.models import ActivityKind as K
from bot.db.models import ActivityLog, MonitorPref, User

log = logging.getLogger(__name__)

MAX_TEXT = 1500
ICONS = {
    K.TEXT: "💬",
    K.VOICE: "🎙",
    K.PHOTO: "🖼",
    K.BUTTON: "👆",
    K.COMMAND: "⌨️",
    K.OTHER: "📎",
    K.OPEN: "🚪",
    K.VIEW: "👀",
    K.ACTION: "🛠",
    K.REPLY: "🤖",
    K.ERROR: "⚠️",
}
# Foydalanuvchining o'zi qilgan harakatlar (bot javobi va xatolardan tashqari)
USER_KINDS = (K.TEXT, K.VOICE, K.PHOTO, K.BUTTON, K.COMMAND, K.OTHER, K.OPEN, K.VIEW, K.ACTION)

QUIET = 25  # soniya: suhbat shuncha jim tursa — lentaga yuboriladi
MAX_WAIT = 120  # uzoq suhbat ham shundan kechikmasin
DIGEST_LIMIT = 3500  # Telegram xabari 4096 belgigacha
LINE_LIMIT = 350
# Bir xil xato shu vaqt ichida qayta kelsa — lentaga takror yuborilmaydi; keyingisida necha marta takrorlangani
# yoziladi (baza ishlamay qolsa har 10 daqiqada bir xil xabar kelib, telefon to'lib ketmasin)
ERROR_REPEAT = 6 * 60 * 60
# Ma'lum xatolar uchun tushuntirish: matnida shu so'z bo'lsa — xabar oxirida nima qilish kerakligi
ERROR_HINTS = (
    (
        "exceeded the quota",
        "💡 Neon bazasining bepul oylik limiti tugagan — bot bazaga ulana olmayapti (yozuvlar saqlanmaydi). "
        "Limit yangi oy boshida o'zi tiklanadi yoki neon.com saytida tarifni oshiring. Ma'lumotlar o'chmaydi.",
    ),
)


class MonCb(CallbackData, prefix="m"):
    a: str  # users | user | log | ent | msg | cancel | sum | errors | feed | self | daily
    tg: int = 0
    n: int = 0  # sahifa / kunlar soni
    v: str = ""


@dataclass
class Event:
    tg_id: int | None
    kind: str
    text: str
    source: str = "bot"  # bot | app
    msg_id: int | None = None
    edit: bool = False  # bot msg_id xabarini tahrirladi (yoki ovoz matnga aylandi)
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_queue: asyncio.Queue[Event] | None = None
_loop: asyncio.AbstractEventLoop | None = None
_bot: Bot | None = None
_worker: asyncio.Task | None = None
_tasks: set[asyncio.Task] = set()
_prefs: dict[str, str] = {}


def enabled() -> bool:
    return _queue is not None


# --- Yozib olish ------------------------------------------------------------

def plain(text: str) -> str:
    """Telegram HTML → oddiy matn."""
    text = re.sub(r"<br\s*/?>", "\n", text or "")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n\s*\n+", "\n", html.unescape(text)).strip()


def cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def record(
    tg_id: int | None,
    kind: str,
    text: str,
    *,
    source: str = "bot",
    msg_id: int | None = None,
    edit: bool = False,
) -> None:
    """Harakatni navbatga qo'yadi (kutmaydi). Kuzatuv o'chiq bo'lsa — hech narsa qilmaydi."""
    if _queue is None or _loop is None:
        return
    event = Event(tg_id, kind, cut((text or "").strip() or "—", MAX_TEXT), source, msg_id, edit)
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is _loop:
        _put(event)
    else:  # boshqa oqimdagi log yozuvi
        _loop.call_soon_threadsafe(_put, event)


def _put(event: Event) -> None:
    try:
        _queue.put_nowait(event)
    except asyncio.QueueFull:
        pass  # baza ishlamay qolgan bo'lsa xotira to'lib ketmasin


# --- Kim? -------------------------------------------------------------------

ROLE_OWNER = "ega"
ROLE_MANAGER = "ish boshqaruvchi"
ROLE_WORKER = "ishchi"
ROLE_GUEST = "ro'yxatda yo'q"


@dataclass
class Who:
    tg_id: int
    name: str
    role: str
    user: User | None
    is_new: bool


# Telegram ismi va @username — kiruvchi xabarlardan
_tg_info: dict[int, tuple[str, str | None]] = {}
_who_cache: dict[int, tuple[float, Who]] = {}
WHO_TTL = 300


def remember(tg_id: int, full_name: str, username: str | None) -> None:
    if _tg_info.get(tg_id) != (full_name, username):
        _tg_info[tg_id] = (full_name, username)
        _who_cache.pop(tg_id, None)


def to_utc(dt: datetime) -> datetime:
    # SQLite vaqt mintaqasini saqlamaydi — u yerda hammasi UTC
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def local(dt: datetime) -> datetime:
    return to_utc(dt).astimezone(settings.tz)


def role_of(user: User | None, tg_id: int) -> str:
    if user is not None:
        return ROLE_OWNER if user.is_owner else ROLE_MANAGER if user.is_manager else ROLE_WORKER
    return ROLE_OWNER if tg_id in settings.owner_ids else ROLE_GUEST


def display_name(tg_id: int, user: User | None) -> str:
    full_name, username = _tg_info.get(tg_id, (None, None))
    name = (user.name if user else None) or full_name or str(tg_id)
    return f"{name} @{username}" if username else name


async def who(tg_id: int) -> Who:
    cached = _who_cache.get(tg_id)
    if cached and time.monotonic() - cached[0] < WHO_TTL:
        return cached[1]
    async with session_factory() as session:
        user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    is_new = user is None or (
        user.created_at is not None and datetime.now(timezone.utc) - to_utc(user.created_at) < timedelta(hours=1)
    )
    result = Who(tg_id, display_name(tg_id, user), role_of(user, tg_id), user, is_new)
    _who_cache[tg_id] = (time.monotonic(), result)
    return result


def line(kind: str, source: str, text: str, limit: int = LINE_LIMIT) -> str:
    """Bitta harakat — lenta va tarix uchun (HTML)."""
    icon = ICONS.get(kind, "•")
    if source == "app" and kind != K.REPLY:
        icon = "📱" + icon
    return f"{icon} {html.escape(cut(text, limit), quote=False)}"


def user_kb(tg_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="👤 Profil", callback_data=MonCb(a="user", tg=tg_id))
    kb.button(text="💬 Suhbat", callback_data=MonCb(a="log", tg=tg_id))
    kb.adjust(2)
    return kb.as_markup()


# --- Sozlamalar -------------------------------------------------------------

FEED_ALL = "all"
FEED_ERRORS = "errors"
FEED_OFF = "off"
DEFAULT_PREFS = {"feed": FEED_ALL, "self": "0", "daily": "1"}


def pref(key: str) -> str:
    return _prefs.get(key, DEFAULT_PREFS.get(key, ""))


async def set_pref(key: str, value: str) -> None:
    async with session_factory() as session:
        row = await session.get(MonitorPref, key)
        if row is None:
            session.add(MonitorPref(key=key, value=value))
        else:
            row.value = value
        await session.commit()
    _prefs[key] = value


# --- Adminga yuborish -------------------------------------------------------

_unreachable: set[int] = set()


async def send_admins(text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    if _bot is None:
        return
    for admin_id in settings.admin_ids:
        try:
            await _bot.send_message(
                admin_id, text, reply_markup=kb, link_preview_options=LinkPreviewOptions(is_disabled=True)
            )
            _unreachable.discard(admin_id)
        except TelegramAPIError as e:
            if admin_id not in _unreachable:
                _unreachable.add(admin_id)
                log.warning("Kuzatuv boti adminga (%s) yozolmadi — botda /start bosilganmi? %s", admin_id, e)


def spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# --- Fondagi ishchi: bazaga yozish va lenta ---------------------------------

# Bot xabarini tahrirlasa («⏳ Tahlil qilinmoqda…» → qoralama) — o'sha yozuv yangilanadi. Lekin orada odam
# biror narsa qilgan bo'lsa (masalan «✅ Saqlash» bosgan) — eski holat tarixda qolsin, yangisi alohida yoziladi.
_turn: dict[int, int] = {}  # tg_id → foydalanuvchi harakatlari soni
_msg_turn: dict[tuple[int, int], int] = {}  # (tg_id, msg_id) → xabar yozilgan paytdagi _turn
MSG_TURN_LIMIT = 5000


def _resolve_edit(event: Event) -> bool:
    """True — mavjud yozuvni yangilash, False — yangi qator."""
    if event.tg_id is None:
        return False
    if event.kind in USER_KINDS and not event.edit:
        _turn[event.tg_id] = _turn.get(event.tg_id, 0) + 1
    if event.msg_id is None:
        return False
    key = (event.tg_id, event.msg_id)
    turn = _turn.get(event.tg_id, 0)
    replace = event.edit and _msg_turn.get(key) == turn
    _msg_turn[key] = turn
    if len(_msg_turn) > MSG_TURN_LIMIT:
        _msg_turn.pop(next(iter(_msg_turn)))
    return replace


async def _store(batch: list[tuple[Event, bool]]) -> None:
    async with session_factory() as session:
        for event, replace in batch:
            if replace:
                # Shu xabarning eng oxirgi yozuvi (avvalgi holatlari tarixda o'zgarmay qolsin)
                latest = (
                    select(func.max(ActivityLog.id))
                    .where(
                        ActivityLog.tg_id == event.tg_id,
                        ActivityLog.msg_id == event.msg_id,
                        ActivityLog.kind == event.kind,
                    )
                    .scalar_subquery()
                )
                result = await session.execute(
                    update(ActivityLog)
                    .where(ActivityLog.id == latest)
                    .values(text=event.text)
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount:
                    continue
            session.add(
                ActivityLog(
                    created_at=event.at,
                    tg_id=event.tg_id,
                    source=event.source,
                    kind=event.kind,
                    text=event.text,
                    msg_id=event.msg_id,
                )
            )
        await session.commit()


@dataclass
class _Digest:
    lines: list[list] = field(default_factory=list)  # [kind, msg_id, matn]
    first_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started: float = field(default_factory=time.monotonic)
    touched: float = field(default_factory=time.monotonic)


_digests: dict[int, _Digest] = {}
_recent_errors: dict[str, list] = {}  # xatoning birinchi qatori → [yuborilgan vaqt, shundan beri takrorlar soni]


def _watched(tg_id: int | None) -> bool:
    return tg_id is not None and (tg_id not in settings.admin_ids or pref("self") == "1")


def _to_feed(event: Event, replace: bool) -> None:
    mode = pref("feed")
    if mode == FEED_OFF:
        return
    if event.kind == K.ERROR and (event.tg_id is None or _watched(event.tg_id)):
        first_line = event.text.split("\n", 1)[0]
        now = time.monotonic()
        seen = _recent_errors.get(first_line)
        if seen is not None and now - seen[0] < ERROR_REPEAT:
            seen[1] += 1
        else:
            if len(_recent_errors) > 200:
                _recent_errors.clear()
            _recent_errors[first_line] = [now, 0]
            spawn(_send_error(event, seen[1] if seen else 0))
    if mode != FEED_ALL or not _watched(event.tg_id):
        return

    digest = _digests.get(event.tg_id)
    if digest is None:
        digest = _digests[event.tg_id] = _Digest(first_at=event.at)
        spawn(_flush_later(event.tg_id, digest))
    text = line(event.kind, event.source, event.text)
    same = None
    if replace:
        same = next((i for i in reversed(digest.lines) if i[0] == event.kind and i[1] == event.msg_id), None)
    if same is not None:
        same[2] = text
    else:
        digest.lines.append([event.kind, event.msg_id, text])
    digest.last_at = event.at
    digest.touched = time.monotonic()
    if sum(len(item[2]) + 1 for item in digest.lines) > DIGEST_LIMIT:
        digest.started = -MAX_WAIT  # darhol yuborilsin


async def _flush_later(tg_id: int, digest: _Digest) -> None:
    while True:
        deadline = min(digest.touched + QUIET, digest.started + MAX_WAIT)
        wait = deadline - time.monotonic()
        if wait <= 0:
            break
        await asyncio.sleep(min(wait, 5))
    if _digests.get(tg_id) is digest:
        del _digests[tg_id]
    try:
        await _send_digest(tg_id, digest)
    except Exception:
        log.warning("Kuzatuv: lentaga yuborilmadi", exc_info=True)


async def _send_digest(tg_id: int, digest: _Digest) -> None:
    person = await who(tg_id)
    start, end = local(digest.first_at), local(digest.last_at)
    period = start.strftime("%H:%M")
    if end.strftime("%H:%M") != period:
        period += end.strftime("–%H:%M")
    header = f"{'🆕 ' if person.is_new else ''}👤 <b>{html.escape(person.name)}</b> · {person.role} · {period}"
    body, chars = [], len(header)
    for _, _, text in digest.lines:
        if chars + len(text) + 1 > DIGEST_LIMIT:
            body.append("…")
            break
        body.append(text)
        chars += len(text) + 1
    await send_admins("\n".join([header, *body]), user_kb(tg_id))


async def _send_error(event: Event, repeats: int = 0) -> None:
    """repeats — oldingi xabardan beri shu xato necha marta takrorlangan (ular alohida yuborilmagan)."""
    if event.tg_id is not None:
        person = await who(event.tg_id)
        head = f"⚠️ <b>Xato</b> · 👤 {html.escape(person.name)} ({person.role})"
        kb = user_kb(event.tg_id)
    else:
        head, kb = "⚠️ <b>Server xatosi</b>", None
    text = f"{head}\n<pre>{html.escape(cut(event.text, 3000))}</pre>"
    if repeats:
        text += f"\n🔁 Oldingi xabardan beri yana {repeats} marta takrorlandi."
    hints = [hint for needle, hint in ERROR_HINTS if needle in event.text]
    if hints:
        text += "\n" + "\n".join(hints)
    else:
        text += f"\n🔕 Shu xato {ERROR_REPEAT // 3600} soat ichida takrorlansa — qayta yuborilmaydi."
    await send_admins(text, kb)


BATCH = 100


async def _work() -> None:
    while True:
        batch = [await _queue.get()]
        while len(batch) < BATCH and not _queue.empty():
            batch.append(_queue.get_nowait())
        resolved = [(event, _resolve_edit(event)) for event in batch]
        try:
            await _store(resolved)
        except Exception:
            log.warning("Kuzatuv: bazaga yozib bo'lmadi (%s ta harakat)", len(batch), exc_info=True)
        for event, replace in resolved:
            try:
                _to_feed(event, replace)
            except Exception:
                log.warning("Kuzatuv: lentada xato", exc_info=True)


def init() -> None:
    """Navbatni yaratadi — shu paytdan harakatlar yig'ila boshlaydi (asosiy bot ishga tushishidan oldin)."""
    global _queue, _loop
    _loop = asyncio.get_running_loop()
    _queue = asyncio.Queue(maxsize=10_000)


async def start(monitor_bot: Bot) -> None:
    global _bot, _worker
    _bot = monitor_bot
    async with session_factory() as session:
        rows = (await session.execute(select(MonitorPref))).scalars().all()
    _prefs.update({row.key: row.value for row in rows})
    _worker = asyncio.create_task(_work())


async def stop() -> None:
    global _queue
    _queue = None
    if _worker is not None:
        _worker.cancel()
    for task in list(_tasks):
        task.cancel()
