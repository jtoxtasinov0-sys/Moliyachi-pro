from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from bot.config import settings
from bot.db.models import EntryStatus, User, WorkEntry
from bot.db.repo import SAVINGS_CHOICES

# --- Asosiy menyu -----------------------------------------------------------

BTN_REPORT = "📊 Hisobot"
BTN_RECENT = "🕘 Oxirgi yozuvlar"
BTN_EXPENSES = "💸 Xarajatlar"
BTN_DEBTS = "🤝 Qarzlar"
BTN_CLIENTS = "🛍 Mijozlar"
BTN_ADVICE = "💡 Maslahat"
BTN_PENDING = "⏳ Tasdiq kutayotganlar"
BTN_WORKERS = "👷 Ishchilar"
BTN_MODELS = "🧵 Modellar"
BTN_SETTINGS = "⚙️ Sozlamalar"
BTN_HELP = "❓ Yordam"

MENU_TEXTS = {
    BTN_REPORT, BTN_RECENT, BTN_EXPENSES, BTN_DEBTS, BTN_CLIENTS, BTN_ADVICE, BTN_PENDING, BTN_WORKERS,
    BTN_MODELS, BTN_SETTINGS, BTN_HELP,
}


def main_menu(user: User) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text=BTN_REPORT)
    kb.button(text=BTN_RECENT)
    if user.is_owner:
        kb.button(text=BTN_CLIENTS)
        kb.button(text=BTN_EXPENSES)
        kb.button(text=BTN_DEBTS)
        kb.button(text=BTN_ADVICE)
        kb.button(text=BTN_PENDING)
        kb.button(text=BTN_WORKERS)
        kb.button(text=BTN_MODELS)
        kb.button(text=BTN_SETTINGS)
    kb.button(text=BTN_HELP)
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True, input_field_placeholder="Ovoz, matn yoki rasm yuboring")


# --- Callback ma'lumotlari --------------------------------------------------

class DraftCb(CallbackData, prefix="d"):
    action: str  # save | fix | cancel | today | yesterday


class ResolveCb(CallbackData, prefix="r"):
    kind: str  # worker | model | client
    line: int
    item_id: int  # 0 — yangi qo'shish


class EntryCb(CallbackData, prefix="e"):
    action: str  # edit | del | del_yes | del_no | approve | reject
    entry_id: int


class DebtDirCb(CallbackData, prefix="dd"):
    line: int
    direction: str  # in | out


class ClientKindCb(CallbackData, prefix="ck"):
    line: int
    kind: str  # sale | payment | return


class MoneyCb(CallbackData, prefix="m"):
    action: str  # del | del_yes | del_no
    kind: str  # p — xarajat/daromad, d — qarz, c — mijoz yozuvi
    entry_id: int


class PersonalReportCb(CallbackData, prefix="pr"):
    period: str  # day | week | month | recent


class DebtsCb(CallbackData, prefix="db"):
    action: str  # refresh | recent


class ReportCb(CallbackData, prefix="rep"):
    period: str  # day | week | month


class AdviceCb(CallbackData, prefix="adv"):
    action: str  # show | pct | ai | lesson
    value: int = 0


class SettingsCb(CallbackData, prefix="s"):
    action: str  # approval | cur | main | autoimg | remind
    value: str = ""


class WorkerCb(CallbackData, prefix="wk"):
    action: str  # add | link | toggle
    user_id: int = 0


class ModelCb(CallbackData, prefix="md"):
    action: str  # add | price | toggle | noprice
    model_id: int = 0


# --- Tugmalar ---------------------------------------------------------------

def add_app_button(kb: InlineKeyboardBuilder, view: str = "") -> None:
    """Pastki qatorga «Ilovani ochish». view — ilova qaysi bo'limda ochilsin (debts, wallet, entries, clients)."""
    url = settings.webapp_url
    if url:
        url += f"?view={view}" if view else ""
        kb.row(InlineKeyboardButton(text="📱 Ilovani ochish", web_app=WebAppInfo(url=url)))


def draft_kb(with_date_buttons: bool = False, save_text: str = "✅ Saqlash") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=save_text, callback_data=DraftCb(action="save"))
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


def entry_kb(entry: WorkEntry, viewer: User, app: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if viewer.is_owner and entry.status == EntryStatus.PENDING:
        kb.button(text="✅ Tasdiqlash", callback_data=EntryCb(action="approve", entry_id=entry.id))
        kb.button(text="🚫 Rad etish", callback_data=EntryCb(action="reject", entry_id=entry.id))
    kb.button(text="✏️ Tahrirlash", callback_data=EntryCb(action="edit", entry_id=entry.id))
    kb.button(text="🗑 O'chirish", callback_data=EntryCb(action="del", entry_id=entry.id))
    kb.adjust(2)
    if app:
        add_app_button(kb, "entries")
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
    add_app_button(kb)
    return kb.as_markup()


def debt_direction_kb(line: int, person: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=f"📥 {person[:20]}dan oldim", callback_data=DebtDirCb(line=line, direction="in"))
    kb.button(text=f"📤 {person[:20]}ga berdim", callback_data=DebtDirCb(line=line, direction="out"))
    kb.button(text="❌ Bekor", callback_data=DraftCb(action="cancel"))
    kb.adjust(1)
    return kb.as_markup()


def client_kind_kb(line: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📦 Mol berdim", callback_data=ClientKindCb(line=line, kind="sale"))
    kb.button(text="💵 To'lov oldim", callback_data=ClientKindCb(line=line, kind="payment"))
    kb.button(text="↩️ Mol qaytardi", callback_data=ClientKindCb(line=line, kind="return"))
    kb.button(text="❌ Bekor", callback_data=DraftCb(action="cancel"))
    kb.adjust(2, 1, 1)
    return kb.as_markup()


def money_entry_kb(kind: str, entry_id: int, app: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 O'chirish", callback_data=MoneyCb(action="del", kind=kind, entry_id=entry_id))
    if app:
        add_app_button(kb, {"d": "debts", "c": "clients"}.get(kind, "wallet"))
    return kb.as_markup()


def app_kb(view: str = "") -> InlineKeyboardMarkup | None:
    """Faqat «Ilovani ochish» tugmasi (ilova manzili bo'lmasa — tugmasiz)."""
    if not settings.webapp_url:
        return None
    kb = InlineKeyboardBuilder()
    add_app_button(kb, view)
    return kb.as_markup()


def clients_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    add_app_button(kb, "clients")
    return kb.as_markup()


def money_confirm_delete_kb(kind: str, entry_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Ha, o'chirilsin", callback_data=MoneyCb(action="del_yes", kind=kind, entry_id=entry_id))
    kb.button(text="↩️ Yo'q", callback_data=MoneyCb(action="del_no", kind=kind, entry_id=entry_id))
    return kb.as_markup()


def personal_report_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Bugun", callback_data=PersonalReportCb(period="day"))
    kb.button(text="Shu hafta", callback_data=PersonalReportCb(period="week"))
    kb.button(text="Shu oy", callback_data=PersonalReportCb(period="month"))
    kb.button(text="🕘 Oxirgi yozuvlar", callback_data=PersonalReportCb(period="recent"))
    kb.adjust(3, 1)
    add_app_button(kb, "wallet")
    return kb.as_markup()


def debts_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Yangilash", callback_data=DebtsCb(action="refresh"))
    kb.button(text="🕘 Oxirgi yozuvlar", callback_data=DebtsCb(action="recent"))
    kb.adjust(2)
    add_app_button(kb, "debts")
    return kb.as_markup()


def advice_kb(savings_pct: int, lesson: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for pct in SAVINGS_CHOICES:
        mark = "✅ " if pct == savings_pct else ""
        kb.button(text=f"{mark}🏦 {pct}%", callback_data=AdviceCb(action="pct", value=pct))
    kb.button(text="🤖 Shaxsiy maslahat", callback_data=AdviceCb(action="ai"))
    kb.button(text="📚 Keyingi saboq", callback_data=AdviceCb(action="lesson", value=lesson + 1))
    kb.button(text="🔄 Yangilash", callback_data=AdviceCb(action="show"))
    kb.adjust(len(SAVINGS_CHOICES), 1, 2)
    add_app_button(kb, "wallet")
    return kb.as_markup()


def lesson_kb(lesson: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📚 Keyingi saboq", callback_data=AdviceCb(action="lesson", value=lesson + 1))
    kb.button(text="💡 Maslahat", callback_data=AdviceCb(action="show"))
    kb.adjust(2)
    return kb.as_markup()


def reminder_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="💡 Maslahat va reja", callback_data=AdviceCb(action="show"))
    add_app_button(kb, "wallet")
    return kb.as_markup()
