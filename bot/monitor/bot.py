"""Kuzatuv boti: admin foydalanuvchilar botni qanday ishlatayotganini ko'radi.

👥 Foydalanuvchilar — kim qachon oxirgi marta ishlatgan, profil, suhbat tarixi, kiritgan yozuvlari, xabar yozish
📈 Statistika — bugun / 7 kun / 30 kun: kim nechta harakat qildi, nechta yozuv saqladi, nechta xato
⚠️ Xatolar — oxirgi xatolar
🔔 Lenta — jonli lenta rejimi va kunlik hisobot
"""
import asyncio
import hashlib
import logging
from datetime import datetime, timedelta
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    ReplyKeyboardMarkup,
    TelegramObject,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web
from sqlalchemy import delete

from bot.config import settings
from bot.db.base import session_factory
from bot.db.models import ActivityLog
from bot.monitor import stats, tracker
from bot.monitor.tracker import FEED_ALL, FEED_ERRORS, FEED_OFF, MonCb

log = logging.getLogger(__name__)

WEBHOOK_PATH = "/tg/monitor"
DAILY_HOUR = 23  # kunlik hisobot vaqti
KEEP_DAYS = 90  # tarix shuncha kun saqlanadi

BTN_USERS = "👥 Foydalanuvchilar"
BTN_STATS = "📈 Statistika"
BTN_ERRORS = "⚠️ Xatolar"
BTN_FEED = "🔔 Lenta"
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

router = Router(name="monitor")


class Admin(Filter):
    async def __call__(self, event: TelegramObject, event_from_user=None) -> bool:
        return event_from_user is not None and event_from_user.id in settings.admin_ids


router.message.filter(Admin())
router.callback_query.filter(Admin())

strangers = Router(name="monitor_strangers")


class MonSG(StatesGroup):
    text = State()


def menu() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    for text in (BTN_USERS, BTN_STATS, BTN_ERRORS, BTN_FEED):
        kb.button(text=text)
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)


async def _show(event: Message | CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    """Tugmadan kelgan bo'lsa — o'sha xabarni almashtiradi, aks holda yangi xabar."""
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=kb, link_preview_options=NO_PREVIEW)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb, link_preview_options=NO_PREVIEW)


# --- Boshlash ---------------------------------------------------------------

@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "👁 <b>Moliyachi — kuzatuv boti</b>\n\n"
        "Bu yerga botdan foydalanayotganlarning har bir harakati keladi: nima yozgani yoki aytgani, "
        "bot nima deb javob bergani, qaysi tugmani bosgani, ilovada nima qilgani va xatolar.\n\n"
        f"{BTN_USERS} — kim qachon ishlatgan, har birining suhbat tarixi va kiritgan yozuvlari, "
        "unga bot nomidan xabar yozish\n"
        f"{BTN_STATS} — bugun / 7 kun / 30 kun\n"
        f"{BTN_ERRORS} — oxirgi xatolar\n"
        f"{BTN_FEED} — jonli lenta va kunlik hisobotni sozlash",
        reply_markup=menu(),
    )


# --- Foydalanuvchilar -------------------------------------------------------

async def _users_view() -> tuple[str, InlineKeyboardMarkup]:
    async with session_factory() as session:
        rows = await stats.users_overview(session)
    kb = InlineKeyboardBuilder()
    lines = [f"👥 <b>Foydalanuvchilar</b> ({len(rows)})\n"]
    for row in rows:
        lines.append(
            f"{stats.dot(row.last_seen)} <b>{escape(row.name)}</b> — {row.role}\n"
            f"    oxirgi: {stats.ago(row.last_seen)} · 24 soatda {row.day} · 7 kunda {row.week} harakat"
            + ("" if row.tg_id else "\n    🔗 taklif havolasi bilan hali ulanmagan")
        )
        if row.tg_id:
            kb.button(text=f"{stats.dot(row.last_seen)} {row.name[:30]}", callback_data=MonCb(a="user", tg=row.tg_id))
    if not rows:
        lines.append("Hali hech kim yo'q.")
    lines.append("\n🟢 so'nggi soatda · 🟡 so'nggi 24 soatda · ⚪ ilgari · ⚫ hali ishlatmagan")
    kb.adjust(1)
    return "\n".join(lines), kb.as_markup()


@router.message(F.text == BTN_USERS)
@router.message(Command("users"))
async def users(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _show(message, *await _users_view())


@router.callback_query(MonCb.filter(F.a == "users"))
async def users_cb(call: CallbackQuery) -> None:
    await _show(call, *await _users_view())


async def _user_view(tg_id: int) -> tuple[str, InlineKeyboardMarkup]:
    person = await tracker.who(tg_id)
    async with session_factory() as session:
        first, last = await stats.first_last(session, tg_id)
        kinds = await stats.kind_counts(session, tg_id)
        entries = await stats.entry_counts(session, person.user) if person.user else None
    joined = f"{tracker.local(person.user.created_at):%d.%m.%Y}" if person.user and person.user.created_at else "—"
    text = (
        f"👤 <b>{escape(person.name)}</b> · {person.role}\n"
        f"🆔 <code>{tg_id}</code>\n"
        f"📅 ro'yxatga olingan: {joined}\n"
        f"🕐 oxirgi harakat: {stats.ago(last)}"
        + (f" · birinchi: {stats.ago(first)}" if first else "")
        + f"\n\n📊 <b>Harakatlar</b> (7 kun / jami):\n{kinds}"
    )
    if entries:
        text += f"\n\n🧾 <b>Kiritgan yozuvlari</b> (jami / 7 kun):\n{entries}"
    kb = InlineKeyboardBuilder()
    kb.button(text="💬 Suhbat tarixi", callback_data=MonCb(a="log", tg=tg_id))
    if person.user:
        kb.button(text="🧾 Yozuvlari", callback_data=MonCb(a="ent", tg=tg_id))
    kb.button(text="✉️ Xabar yozish", callback_data=MonCb(a="msg", tg=tg_id))
    kb.button(text="🔄 Yangilash", callback_data=MonCb(a="user", tg=tg_id))
    kb.button(text="⬅️ Foydalanuvchilar", callback_data=MonCb(a="users"))
    kb.adjust(2)
    return text, kb.as_markup()


@router.callback_query(MonCb.filter(F.a == "user"))
async def user_card(call: CallbackQuery, callback_data: MonCb) -> None:
    text, kb = await _user_view(callback_data.tg)
    current = call.message.text or ""
    if current.startswith("👥") or "🆔" in current:
        await _show(call, text, kb)  # ro'yxat yoki profilning o'zi — almashtiramiz
        return
    # Lenta, suhbat tarixi va h.k. — o'z joyida qolsin, profil alohida chiqadi
    await call.message.answer(text, reply_markup=kb)
    await call.answer()


@router.callback_query(MonCb.filter(F.a == "log"))
async def conversation(call: CallbackQuery, callback_data: MonCb) -> None:
    person = await tracker.who(callback_data.tg)
    async with session_factory() as session:
        lines, oldest, has_more = await stats.history(session, callback_data.tg, callback_data.n)
    head = f"💬 <b>{escape(person.name)}</b> — suhbat" + (" (davomi)" if callback_data.n else "")
    text = "\n".join([head, *lines]) if lines else f"{head}\n\nHali hech narsa yo'q."
    kb = InlineKeyboardBuilder()
    if has_more and oldest:
        kb.button(text="⬆️ Oldingilari", callback_data=MonCb(a="log", tg=callback_data.tg, n=oldest))
    kb.button(text="👤 Profil", callback_data=MonCb(a="user", tg=callback_data.tg))
    # Har sahifa — yangi xabar: tarixni yuqoriga qarab o'qish qulay
    await call.message.answer(text, reply_markup=kb.as_markup(), link_preview_options=NO_PREVIEW)
    await call.answer()


@router.callback_query(MonCb.filter(F.a == "ent"))
async def user_entries(call: CallbackQuery, callback_data: MonCb) -> None:
    person = await tracker.who(callback_data.tg)
    if person.user is None:
        await call.answer("Ro'yxatda yo'q", show_alert=True)
        return
    async with session_factory() as session:
        items = await stats.recent_entries(session, person.user)
    head = f"🧾 <b>{escape(person.name)}</b> — oxirgi yozuvlari"
    body = "\n\n".join(text for _, text in items) if items else "Hali yozuv yo'q."
    kb = InlineKeyboardBuilder()
    kb.button(text="👤 Profil", callback_data=MonCb(a="user", tg=callback_data.tg))
    await call.message.answer(tracker.cut(f"{head}\n\n{body}", 4000), reply_markup=kb.as_markup())
    await call.answer()


# --- Foydalanuvchiga xabar --------------------------------------------------

@router.callback_query(MonCb.filter(F.a == "msg"))
async def message_ask(call: CallbackQuery, callback_data: MonCb, state: FSMContext) -> None:
    person = await tracker.who(callback_data.tg)
    await state.set_state(MonSG.text)
    await state.update_data(tg=callback_data.tg)
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Bekor", callback_data=MonCb(a="cancel"))
    await call.message.answer(
        f"✉️ <b>{escape(person.name)}</b> ga xabar yozing — u Moliyachi botidan keladi.",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


@router.callback_query(MonCb.filter(F.a == "cancel"))
async def message_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text("❌ Bekor qilindi.")
    await call.answer()


@router.message(MonSG.text, F.text, ~F.text.in_({BTN_USERS, BTN_STATS, BTN_ERRORS, BTN_FEED}))
async def message_send(message: Message, state: FSMContext, main_bot: Bot) -> None:
    tg_id = (await state.get_data()).get("tg")
    await state.clear()
    if not tg_id or message.text.startswith("/"):
        await message.answer("❌ Bekor qilindi.")
        return
    try:
        await main_bot.send_message(tg_id, f"✉️ <b>Admindan xabar:</b>\n\n{escape(message.text)}")
    except TelegramAPIError as e:
        await message.answer(f"⚠️ Yuborilmadi: {escape(str(e))}")
        return
    await message.answer("✅ Yuborildi.", reply_markup=tracker.user_kb(tg_id))


# --- Statistika va xatolar --------------------------------------------------

def _stats_kb(days: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for n, label in ((1, "Bugun"), (7, "7 kun"), (30, "30 kun")):
        kb.button(text=f"{'• ' if n == days else ''}{label}", callback_data=MonCb(a="sum", n=n))
    return kb.as_markup()


async def _stats_text(days: int) -> str:
    async with session_factory() as session:
        return await stats.summary(session, days)


@router.message(F.text == BTN_STATS)
@router.message(Command("stats"))
async def stats_cmd(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _show(message, await _stats_text(1), _stats_kb(1))


@router.callback_query(MonCb.filter(F.a == "sum"))
async def stats_cb(call: CallbackQuery, callback_data: MonCb) -> None:
    days = callback_data.n if callback_data.n in (1, 7, 30) else 1
    await _show(call, await _stats_text(days), _stats_kb(days))


@router.message(F.text == BTN_ERRORS)
@router.message(Command("errors"))
async def errors(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with session_factory() as session:
        rows = await stats.recent_errors(session)
    if not rows:
        await message.answer("✅ Hali xato bo'lmagan.")
        return
    parts = ["⚠️ <b>Oxirgi xatolar</b>"]
    for row in rows:
        who = (await tracker.who(row.tg_id)).name if row.tg_id else "server"
        parts.append(
            f"\n<code>{tracker.local(row.created_at):%d.%m %H:%M}</code> · {escape(who)}\n"
            f"<pre>{escape(tracker.cut(row.text, 350))}</pre>"
        )
    await message.answer(tracker.cut("\n".join(parts), 4000))


# --- Lenta sozlamalari ------------------------------------------------------

def _feed_view() -> tuple[str, InlineKeyboardMarkup]:
    mode = tracker.pref("feed")
    me = tracker.pref("self") == "1"
    daily = tracker.pref("daily") == "1"
    modes = ((FEED_ALL, "Hammasi"), (FEED_ERRORS, "Faqat xatolar"), (FEED_OFF, "O'chiq"))
    kb = InlineKeyboardBuilder()
    for value, label in modes:
        kb.button(text=f"{'✅ ' if value == mode else ''}{label}", callback_data=MonCb(a="feed", v=value))
    kb.button(text=f"{'✅' if daily else '⬜'} Kunlik hisobot ({DAILY_HOUR}:00)", callback_data=MonCb(a="daily"))
    kb.button(text=f"{'✅' if me else '⬜'} O'z harakatlarimni ham ko'rsatish", callback_data=MonCb(a="self"))
    kb.adjust(3, 1, 1)
    text = (
        "🔔 <b>Jonli lenta</b>\n\n"
        "• <b>Hammasi</b> — har bir suhbat (odam yozgani yoki aytgani, bosgan tugmasi, bot javobi) "
        f"~{tracker.QUIET} soniya jimlikdan keyin bitta xabar bo'lib keladi. Xatolar — darhol.\n"
        "• <b>Faqat xatolar</b> — faqat xatolar keladi.\n"
        "• <b>O'chiq</b> — hech narsa kelmaydi.\n\n"
        "Qaysi rejimda bo'lmasin, tarix saqlanadi: «👥 Foydalanuvchilar» → odam → «💬 Suhbat tarixi».\n\n"
        f"🌙 Kunlik hisobot — har kuni {DAILY_HOUR}:00 da bugungi statistika.\n"
        "👤 O'z harakatlaringiz (Moliyachi botida o'zingiz yozganlaringiz) — sinab ko'rish uchun yoqing."
    )
    return text, kb.as_markup()


@router.message(F.text == BTN_FEED)
@router.message(Command("feed"))
async def feed(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _show(message, *_feed_view())


@router.callback_query(MonCb.filter(F.a.in_({"feed", "self", "daily"})))
async def feed_toggle(call: CallbackQuery, callback_data: MonCb) -> None:
    if callback_data.a == "feed" and callback_data.v in (FEED_ALL, FEED_ERRORS, FEED_OFF):
        await tracker.set_pref("feed", callback_data.v)
    elif callback_data.a in ("self", "daily"):
        await tracker.set_pref(callback_data.a, "0" if tracker.pref(callback_data.a) == "1" else "1")
    await _show(call, *_feed_view())


@router.message()
async def unknown(message: Message) -> None:
    await message.answer("Pastdagi menyudan tanlang.", reply_markup=menu())


@router.callback_query()
async def unknown_call(call: CallbackQuery) -> None:
    await call.answer()


@strangers.message()
async def stranger(message: Message) -> None:
    await message.answer(
        "⛔ Bu bot faqat admin uchun.\n"
        f"Sizning ID: <code>{message.from_user.id}</code> — admin bo'lsangiz, uni serverda "
        "<code>ADMIN_TG_ID</code> ga yozing."
    )


@strangers.callback_query()
async def stranger_call(call: CallbackQuery) -> None:
    await call.answer("⛔ Faqat admin uchun", show_alert=True)


# --- Kunlik hisobot va eski tarixni tozalash --------------------------------

async def _daily_loop() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            now = datetime.now(settings.tz)
            day = now.date().isoformat()
            if now.hour < DAILY_HOUR or tracker.pref("daily_done") == day:
                continue
            await tracker.set_pref("daily_done", day)
            async with session_factory() as session:
                cutoff = stats.utcnow() - timedelta(days=KEEP_DAYS)
                await session.execute(delete(ActivityLog).where(ActivityLog.created_at < cutoff))
                await session.commit()
                text = await stats.summary(session, 1)
            if tracker.pref("daily") == "1":
                await tracker.send_admins(f"🌙 <b>Kunlik hisobot</b>\n\n{text}", _stats_kb(1))
        except Exception:
            log.warning("Kuzatuv: kunlik hisobotda xato", exc_info=True)


# --- Ishga tushirish --------------------------------------------------------

class Monitor:
    """Kuzatuv boti: asosiy bot bilan bitta jarayonda, o'z tokeni bilan ishlaydi."""

    def __init__(self, main_bot: Bot, use_webhook: bool):
        self.bot = Bot(settings.monitor_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.dp = Dispatcher(storage=MemoryStorage())
        self.dp["main_bot"] = main_bot
        self.dp.include_routers(router, strangers)
        self.use_webhook = use_webhook
        self._tasks: list[asyncio.Task] = []

    @property
    def webhook_secret(self) -> str:
        return hashlib.sha256(f"monitor:{settings.monitor_bot_token}".encode()).hexdigest()[:48]

    def register(self, app: web.Application) -> None:
        if self.use_webhook:
            SimpleRequestHandler(dispatcher=self.dp, bot=self.bot, secret_token=self.webhook_secret).register(
                app, path=WEBHOOK_PATH
            )

    async def start(self) -> None:
        await tracker.start(self.bot)
        await self.bot.set_my_commands(
            [
                BotCommand(command="users", description="Foydalanuvchilar"),
                BotCommand(command="stats", description="Statistika"),
                BotCommand(command="errors", description="Xatolar"),
                BotCommand(command="feed", description="Jonli lenta sozlamasi"),
            ]
        )
        if self.use_webhook:
            await self.bot.set_webhook(
                f"{settings.base_url}{WEBHOOK_PATH}",
                secret_token=self.webhook_secret,
                allowed_updates=self.dp.resolve_used_update_types(),
            )
        else:
            await self.bot.delete_webhook()
            self._tasks.append(asyncio.create_task(self.dp.start_polling(self.bot, handle_signals=False)))
        self._tasks.append(asyncio.create_task(_daily_loop()))
        me = await self.bot.me()
        log.info("Kuzatuv boti ishga tushdi: @%s (admin: %s)", me.username, settings.admin_ids)
        if tracker.pref("feed") != FEED_OFF:
            await tracker.send_admins("🔄 Moliyachi boti ishga tushdi (yangilangan yoki server qayta yoqilgan).")

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await tracker.stop()
        await self.bot.session.close()
