"""Sana va pul bilan ishlash uchun yordamchilar."""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from bot.config import settings

WEEKDAYS = ["dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba"]

CURRENCY_LABELS = {"UZS": "so'm", "USD": "$", "KRW": "won"}


def today() -> date:
    return datetime.now(settings.tz).date()


def period_range(period: str) -> tuple[date, date]:
    """period: day | week | month -> (boshlanish, tugash), ikkalasi ham kiradi."""
    t = today()
    if period == "day":
        return t, t
    if period == "week":
        start = t - timedelta(days=t.weekday())
        return start, start + timedelta(days=6)
    if period == "month":
        start = t.replace(day=1)
        next_month = (start + timedelta(days=32)).replace(day=1)
        return start, next_month - timedelta(days=1)
    raise ValueError(period)


def fmt_date(d: date) -> str:
    text = d.strftime("%d.%m.%Y")
    diff = (today() - d).days
    if diff == 0:
        return f"{text} (bugun)"
    if diff == 1:
        return f"{text} (kecha)"
    return f"{text} ({WEEKDAYS[d.weekday()]})"


def fmt_number(value: Decimal | int | float) -> str:
    value = Decimal(str(value))
    if value == value.to_integral_value():
        return f"{int(value):,}".replace(",", " ")
    return f"{value:,.2f}".replace(",", " ")


def fmt_money(amount: Decimal | int | float, currency: str) -> str:
    return f"{fmt_number(amount)} {CURRENCY_LABELS.get(currency, currency)}"


def fmt_totals(totals: dict[str, Decimal]) -> str:
    """{'UZS': 100, 'USD': 5} -> "100 so'm + 5 $"."""
    parts = [fmt_money(v, c) for c, v in totals.items() if v]
    return " + ".join(parts) if parts else "—"


_CURRENCY_WORDS = {
    "UZS": ("so'm", "som", "sum", "сўм", "сум", "uzs"),
    "USD": ("$", "usd", "dollar", "доллар"),
    "KRW": ("won", "von", "krw", "₩", "вон"),
}
_MULTIPLIERS = {"ming": 1_000, "минг": 1_000, "k": 1_000, "mln": 1_000_000, "million": 1_000_000, "млн": 1_000_000}


def parse_price(text: str, default_currency: str) -> tuple[Decimal, str] | None:
    """"5000", "5 000 so'm", "2.5$", "3 ming", "1,5 mln" -> (summa, valyuta)."""
    t = text.lower().replace("‘", "'").replace("’", "'").replace("ʻ", "'")
    # «2000dan», «2 mingdan», «5 ming so'mdan» — «-dan» qo'shimchasi narxga ta'sir qilmaydi
    t = re.sub(r"(?<=[\w'$₩])(dan|дан)\b", "", t)
    currency = default_currency
    for code, words in _CURRENCY_WORDS.items():
        if any(w in t for w in words):
            currency = code
            for w in words:
                t = t.replace(w, " ")
    multiplier = 1
    for word, mult in _MULTIPLIERS.items():
        if re.search(rf"\d\s*{re.escape(word)}\b", t):
            multiplier = mult
            t = re.sub(rf"\s*{re.escape(word)}\b", " ", t)
            break
    m = re.search(r"\d[\d\s]*(?:[.,]\d+)?", t)
    if not m:
        return None
    number = m.group(0).replace(" ", "").replace(",", ".")
    try:
        value = Decimal(number) * multiplier
    except InvalidOperation:
        return None
    if value < 0:
        return None
    return value, currency
