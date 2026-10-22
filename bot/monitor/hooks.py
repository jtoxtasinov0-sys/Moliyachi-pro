"""Asosiy botdan kuzatuvga ma'lumot yig'ish: kiruvchi xabarlar va tugmalar, botning javoblari,
ilova (Mini App) so'rovlari va xatolar."""
import json
import logging
import re
import time
import traceback
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    SendAnimation,
    SendAudio,
    SendDocument,
    SendMessage,
    SendPhoto,
    SendVideo,
    SendVoice,
    TelegramMethod,
)
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, TelegramObject, Update
from aiohttp import web

from bot.db.models import ActivityKind as K
from bot.db.models import User
from bot.monitor import tracker

log = logging.getLogger(__name__)


def _duration(seconds: int | None) -> str:
    seconds = seconds or 0
    return f"{seconds // 60}:{seconds % 60:02d}"


# --- Kiruvchi: foydalanuvchi yozgani va bosgan tugmalari --------------------

# callback_query.id → tg_id: tugma bosilganda bot ko'rsatgan qisqa oyna (answer) kimga ekanini bilish uchun
_callbacks: dict[str, int] = {}
CALLBACKS_LIMIT = 1000


def _track_message(m: Message) -> None:
    if m.chat.type != "private" or m.from_user is None:
        return
    tg_id = m.from_user.id
    tracker.remember(tg_id, m.from_user.full_name, m.from_user.username)
    if m.text:
        kind = K.COMMAND if m.text.startswith("/") else K.TEXT
        tracker.record(tg_id, kind, m.text, msg_id=m.message_id)
    elif m.voice or m.audio:
        media = m.voice or m.audio
        tracker.record(tg_id, K.VOICE, _duration(media.duration), msg_id=m.message_id)
    elif m.photo or (m.document and (m.document.mime_type or "").startswith("image/")):
        text = "rasm" if m.photo else f"rasm (fayl): {m.document.file_name or ''}"
        tracker.record(tg_id, K.PHOTO, f"{text} — {m.caption}" if m.caption else text, msg_id=m.message_id)
    elif m.document:
        tracker.record(tg_id, K.OTHER, f"fayl: {m.document.file_name or m.document.mime_type}", msg_id=m.message_id)
    else:
        tracker.record(tg_id, K.OTHER, str(getattr(m.content_type, "value", m.content_type)), msg_id=m.message_id)


def _button_text(markup: InlineKeyboardMarkup | None, data: str | None) -> str | None:
    for row in markup.inline_keyboard if markup else ():
        for button in row:
            if button.callback_data == data:
                return button.text
    return None


def _track_callback(call: CallbackQuery) -> None:
    tg_id = call.from_user.id
    tracker.remember(tg_id, call.from_user.full_name, call.from_user.username)
    _callbacks[call.id] = tg_id
    if len(_callbacks) > CALLBACKS_LIMIT:
        _callbacks.pop(next(iter(_callbacks)))
    markup = call.message.reply_markup if isinstance(call.message, Message) else None
    tracker.record(tg_id, K.BUTTON, _button_text(markup, call.data) or call.data or "?")


class TrackUpdates(BaseMiddleware):
    """Asosiy botning dispatcher'iga: har bir kelgan xabar va bosilgan tugma."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            if isinstance(event, Update):
                if event.message:
                    _track_message(event.message)
                elif event.callback_query:
                    _track_callback(event.callback_query)
        except Exception:
            log.warning("Kuzatuv: xabar yozilmadi", exc_info=True)
        return await handler(event, data)


def heard(message: Message, text: str) -> None:
    """Ovozli xabar matnga aylandi — tarixda «🎙 0:12» o'rniga «🎙 0:12 «matn»» bo'ladi."""
    media = message.voice or message.audio
    tracker.record(
        message.from_user.id,
        K.VOICE,
        f"{_duration(media.duration if media else 0)} «{text}»",
        msg_id=message.message_id,
        edit=True,
    )


# --- Chiquvchi: bot foydalanuvchiga nima yozdi ------------------------------

_MEDIA = {SendVideo: "video", SendPhoto: "rasm", SendDocument: "fayl", SendAnimation: "gif", SendVoice: "ovoz", SendAudio: "audio"}


def _track_request(method: TelegramMethod, result: Any) -> None:
    msg_id, edit = None, False
    if isinstance(method, SendMessage):
        chat_id, text = method.chat_id, method.text
        msg_id = getattr(result, "message_id", None)
    elif isinstance(method, EditMessageText):
        chat_id, text, msg_id, edit = method.chat_id, method.text, method.message_id, True
    elif type(method) in _MEDIA:
        chat_id = method.chat_id
        text = f"[{_MEDIA[type(method)]}] {method.caption or ''}"
        msg_id = getattr(result, "message_id", None)
    elif isinstance(method, AnswerCallbackQuery) and method.text:
        chat_id = _callbacks.get(method.callback_query_id)
        text = f"{'[oyna] ' if method.show_alert else '[qisqa] '}{method.text}"
    else:
        return
    if isinstance(chat_id, int) and chat_id > 0:
        tracker.record(chat_id, K.REPLY, tracker.plain(text), msg_id=msg_id, edit=edit)


class TrackRequests(BaseRequestMiddleware):
    """Asosiy botning sessiyasiga: bot yuborgan va tahrirlagan har bir xabar."""

    async def __call__(self, make_request: NextRequestMiddlewareType, bot, method: TelegramMethod):
        try:
            result = await make_request(bot, method)
        except TelegramForbiddenError:
            chat_id = getattr(method, "chat_id", None)
            if isinstance(chat_id, int) and chat_id > 0:
                tracker.record(chat_id, K.ERROR, "Xabar yetmadi: foydalanuvchi botni bloklagan yoki o'chirgan")
            raise
        try:
            _track_request(method, result)
        except Exception:
            log.warning("Kuzatuv: bot javobi yozilmadi", exc_info=True)
        return result


# --- Xatolar ----------------------------------------------------------------

def _short_traceback(exc: BaseException) -> str:
    frames = traceback.extract_tb(exc.__traceback__)[-4:]
    return "\n".join(f"  {Path(f.filename).name}:{f.lineno} {f.name}" for f in frames)


def report_error(tg_id: int | None, exc: BaseException, where: str = "") -> None:
    tracker.record(tg_id, K.ERROR, f"{where}{type(exc).__name__}: {exc}\n{_short_traceback(exc)}")
    try:
        exc.monitor_reported = True  # log orqali ikkinchi marta kelmasin
    except AttributeError:
        pass


class ErrorLogHandler(logging.Handler):
    """Logdagi har bir xato (ERROR) — kuzatuv botiga: Claude/Aisha xatolari, eslatma yuborilmagani va h.k."""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("bot.monitor"):
            return
        exc = record.exc_info[1] if record.exc_info else None
        if exc is not None and getattr(exc, "monitor_reported", False):
            return
        try:
            text = f"[{record.name}] {record.getMessage()}"
            if exc is not None:
                text += f"\n{type(exc).__name__}: {exc}\n{_short_traceback(exc)}"
            tracker.record(None, K.ERROR, text)
        except Exception:
            pass


# --- Ilova (Mini App) -------------------------------------------------------

_VIEWS = {
    "/api/dashboard": "Asosiy (hisobot)",
    "/api/personal": "Hamyon",
    "/api/personal/entries": "Hamyon: yozuvlar",
    "/api/debts": "Qarzlar",
    "/api/clients": "Mijozlar",
    "/api/clients/entries": "Mijozlar: yozuvlar",
    "/api/advice": "Maslahat",
    "/api/workers": "Ishchilar",
    "/api/models": "Modellar",
    "/api/jobs": "Kasblar",
    "/api/materials": "Xom ashyo",
    "/api/materials/entries": "Xom ashyo: xaridlar",
    "/api/suppliers": "Ta'minotchilar",
    "/api/entries": "Yozuvlar",
    "/api/activity": "Oxirgi yozuvlar",
    "/api/ledger": "Hisob-kitob",
}
_ACTIONS = [
    ("POST", r"/api/advice/ai", "🤖 Shaxsiy maslahat so'radi"),
    ("PATCH", r"/api/settings", "Sozlamani o'zgartirdi"),
    ("DELETE", r"/api/personal/\d+", "Xarajat/daromad yozuvini o'chirdi"),
    ("DELETE", r"/api/debts/\d+", "Qarz yozuvini o'chirdi"),
    ("DELETE", r"/api/clients/entries/\d+", "Mijoz yozuvini o'chirdi"),
    ("DELETE", r"/api/entries/\d+", "Ish yozuvini o'chirdi"),
    ("POST", r"/api/entries/\d+/approve", "Ish yozuvini tasdiqladi"),
    ("POST", r"/api/entries/\d+/reject", "Ish yozuvini rad etdi"),
    ("POST", r"/api/clients", "Mijoz qo'shdi"),
    ("PATCH", r"/api/clients/\d+", "Mijozni tahrirladi"),
    ("POST", r"/api/models", "Model qo'shdi"),
    ("PATCH", r"/api/models/\d+", "Modelni tahrirladi"),
    ("POST", r"/api/workers", "Ishchi qo'shdi"),
    ("POST", r"/api/workers/\d+/toggle", "Ishchini yoqdi/o'chirdi"),
    ("POST", r"/api/workers/\d+/invite", "Ishchi uchun yangi havola oldi"),
    ("PATCH", r"/api/workers/\d+", "Ishchini tahrirladi (kasbi)"),
    ("POST", r"/api/jobs", "Kasb qo'shdi"),
    ("PATCH", r"/api/jobs/\d+", "Kasb nomini o'zgartirdi"),
    ("DELETE", r"/api/jobs/\d+", "Kasbni o'chirdi"),
    ("DELETE", r"/api/materials/\d+", "Xom ashyo yozuvini o'chirdi"),
    ("POST", r"/api/suppliers", "Ta'minotchi qo'shdi"),
    ("POST", r"/api/suppliers/\d+/toggle", "Ta'minotchini yoqdi/o'chirdi"),
    ("POST", r"/api/suppliers/\d+/invite", "Ta'minotchi uchun yangi havola oldi"),
    ("POST", r"/api/payments", "Ishchi bilan pul yozdi (ilovada)"),
    ("DELETE", r"/api/payments/\d+", "Ishchi pul yozuvini o'chirdi"),
    ("POST", r"/api/payments/\d+/approve", "Ishchi pul yozuvini tasdiqladi"),
    ("POST", r"/api/payments/\d+/reject", "Ishchi pul yozuvini rad etdi"),
    ("POST", r"/api/reset/clients", "🧹 Mijozlar hisobini 0 qildi"),
    ("POST", r"/api/reset/workers", "🧹 Ishchilar hisobini 0 qildi"),
    ("POST", r"/api/reset/materials", "🧹 Xom ashyo hisobini 0 qildi"),
    ("POST", r"/api/reset/personal", "🧹 Xarajatlar hisobini 0 qildi"),
]
OPEN_EVERY = 20 * 60  # ilovani ochish — 20 daqiqada bir marta yoziladi
VIEW_EVERY = 10 * 60  # bir bo'limni ko'rish — 10 daqiqada bir marta

_seen: dict[tuple[int, str], float] = {}
# Ilovadagi qoralama tugmalari: callback qiymati → yozuvi (bosilganda nomi bilan yozish uchun)
_app_buttons: dict[int, dict[str, str]] = {}


def _first_time(tg_id: int, key: str, every: int) -> bool:
    now = time.monotonic()
    if now - _seen.get((tg_id, key), -every) < every:
        return False
    _seen[(tg_id, key)] = now
    return True


def _response_json(response: web.StreamResponse) -> dict:
    body = getattr(response, "body", None)
    if not isinstance(body, bytes) or "json" not in (response.content_type or ""):
        return {}
    try:
        data = json.loads(body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def _request_json(request: web.Request) -> dict:
    try:
        data = await request.json()  # aiohttp so'rov tanasini eslab qoladi — qayta o'qish mumkin
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def _track_api(request: web.Request, user: User, response: web.StreamResponse) -> None:
    tg_id, path, method = user.tg_id, request.path, request.method
    if method == "GET":
        if path == "/api/me" and _first_time(tg_id, "open", OPEN_EVERY):
            where = "Telegram ichida" if request.headers.get("X-Telegram-Init-Data") else "brauzerda"
            tracker.record(tg_id, K.OPEN, f"Ilovani ochdi ({where})", source="app")
        elif path in _VIEWS and _first_time(tg_id, path, VIEW_EVERY):
            tracker.record(tg_id, K.VIEW, _VIEWS[path], source="app")
        return

    data = _response_json(response)
    if path == "/api/input":
        body = await _request_json(request)
        if not body.get("heard"):  # ovozdan eshitilgani — /api/input/voice da yozilgan
            tracker.record(tg_id, K.TEXT, str(body.get("text") or ""), source="app")
    elif path == "/api/input/voice":
        text = f"«{data['heard']}»" if data.get("heard") else "ovozli xabar"
        tracker.record(tg_id, K.VOICE, text, source="app")
    elif path == "/api/input/image":
        form = await request.post()
        count, caption = len(form.getall("image", [])), str(form.get("caption") or "")
        text = "rasm" if count == 1 else f"{count} ta rasm"
        tracker.record(tg_id, K.PHOTO, f"{text} — {caption}" if caption else text, source="app")
    elif path == "/api/input/action":
        value = str((await _request_json(request)).get("data") or "")
        tracker.record(tg_id, K.BUTTON, _app_buttons.get(tg_id, {}).get(value, value), source="app")
    else:
        label = next((text for m, rx, text in _ACTIONS if m == method and re.fullmatch(rx, path)), None)
        details = ""
        if method in ("POST", "PATCH") and request.content_type == "application/json":
            body = await _request_json(request)
            details = " " + json.dumps(body, ensure_ascii=False)[:200] if body else ""
        tracker.record(tg_id, K.ACTION, f"{label or f'{method} {path}'}{details}", source="app")
        return

    # Ilova foydalanuvchiga nima ko'rsatdi
    draft = data.get("draft") if isinstance(data.get("draft"), dict) else None
    if draft:
        _app_buttons[tg_id] = {b["data"]: b["text"] for row in draft.get("buttons", []) for b in row}
    for key in ("notice", "saved"):
        if data.get(key):
            tracker.record(tg_id, K.REPLY, tracker.plain(str(data[key])), source="app")
    if draft and not data.get("notice") and not data.get("saved"):
        tracker.record(tg_id, K.REPLY, tracker.plain(str(draft.get("html") or "")), source="app")


async def track_api(request: web.Request, user: User, response: web.StreamResponse) -> None:
    if not tracker.enabled() or not user.tg_id:
        return
    try:
        await _track_api(request, user, response)
    except Exception:
        log.warning("Kuzatuv: ilova so'rovi yozilmadi", exc_info=True)
