from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from bot.db.models import EntryStatus, User, WorkEntry

# --- Asosiy menyu -----------------------------------------------------------

BTN_REPORT = "📊 Hisobot"
BTN_RECENT = "🕘 Oxirgi yozuvlar"
BTN_PENDING = "⏳ Tasdiq kutayotganlar"
BTN_WORKERS = "👷 Ishchilar"
BTN_MODELS = "🧵 Modellar"
BTN_SETTINGS = "⚙️ Sozlamalar"
BTN_HELP = "❓ Yordam"

MENU_TEXTS = {BTN_REPORT, BTN_RECENT, BTN_PENDING, BTN_WORKERS, BTN_MODELS, BTN_SETTINGS, BTN_HELP}


def main_menu(user: User) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=BTN_REPORT)
    kb.button(text=BTN_RECENT)
    if user.is_owner:
        kb.button(text=BTN_PENDING)
        kb.button(text=BTN_WORKERS)
        kb.button(text=BTN_MODELS)
        kb.button(text=BTN_SETTINGS)
    kb.button(text=BTN_HELP)
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True, input_field_placeholder="Ovozli yoki matnli xabar yuboring")


# --- Callback ma'lumotlari --------------------------------------------------

class DraftCb(CallbackData, prefix="d"):
    action: str  # save | fix | cancel | today | yesterday


class ResolveCb(CallbackData, prefix="r"):
    kind: str  # worker | model
    line: int
    item_id: int  # 0 — yangi qo'shish


class EntryCb(CallbackData, prefix="e"):
    action: str  # edit | del | del_yes | del_no | approve | reject
    entry_id: int


class ReportCb(CallbackData, prefix="rep"):
    period: str  # day | week | month


class SettingsCb(CallbackData, prefix="s"):
    action: str  # approval | cur
    value: str = ""


class WorkerCb(CallbackData, prefix="wk"):
    action: str  # add | link | toggle
    user_id: int = 0


class ModelCb(CallbackData, prefix="md"):
    action: str  # add | price | toggle | noprice
    model_id: int = 0


# --- Tugmalar ---------------------------------------------------------------

def draft_kb(with_date_buttons: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Saqlash", callback_data=DraftCb(action="save"))
    kb.button(text="✏️ Tuzatish", callback_data=DraftCb(action="fix"))
    kb.button(text="❌ Bekor", callback_data=DraftCb(action="cancel"))
    sizes = [1, 2]
    if with_date_buttons:
        kb.button(text="📅 Bugun", callback_data=DraftCb(action="today"))
        kb.button(text="📅 Kecha", callback_data=DraftCb(action="yesterday"))
        sizes.append(2)
    kb.adjust(*sizes)
    return kb.as_markup()


def cancel_only_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Bekor", callback_data=DraftCb(action="cancel"))
    return kb.as_markup()


def resolve_kb(
    kind: str, line: int, options: list[tuple[int, str]], allow_new: str | None
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for item_id, name in options:
        kb.button(text=name, callback_data=ResolveCb(kind=kind, line=line, item_id=item_id))
    if allow_new:
        kb.button(text=f"➕ Yangi: {allow_new[:30]}", callback_data=ResolveCb(kind=kind, line=line, item_id=0))
    kb.button(text="❌ Bekor", callback_data=DraftCb(action="cancel"))
    kb.adjust(2)
    return kb.as_markup()


def entry_kb(entry: WorkEntry, viewer: User) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if viewer.is_owner and entry.status == EntryStatus.PENDING:
        kb.button(text="✅ Tasdiqlash", callback_data=EntryCb(action="approve", entry_id=entry.id))
        kb.button(text="🚫 Rad etish", callback_data=EntryCb(action="reject", entry_id=entry.id))
    kb.button(text="✏️ Tahrirlash", callback_data=EntryCb(action="edit", entry_id=entry.id))
    kb.button(text="🗑 O'chirish", callback_data=EntryCb(action="del", entry_id=entry.id))
    kb.adjust(2)
    return kb.as_markup()


def confirm_delete_kb(entry_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Ha, o'chirilsin", callback_data=EntryCb(action="del_yes", entry_id=entry_id))
    kb.button(text="↩️ Yo'q", callback_data=EntryCb(action="del_no", entry_id=entry_id))
    return kb.as_markup()


def report_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Bugun", callback_data=ReportCb(period="day"))
    kb.button(text="Shu hafta", callback_data=ReportCb(period="week"))
    kb.button(text="Shu oy", callback_data=ReportCb(period="month"))
    return kb.as_markup()
