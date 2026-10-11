"""Sana va pul bilan ishlash uchun yordamchilar."""
from datetime import date, datetime, timedelta
from decimal import Decimal

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
