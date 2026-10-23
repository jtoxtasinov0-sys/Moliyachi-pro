"""Hisobot PDF fayl: har bir foydalanuvchi o'z hisobotini telefoniga saqlab oladi.

Egada — ishxona (ishchilar, modellar), daromad-xarajat, mijozlar va qarzlar;
ishchida — faqat o'zi topshirgan ishlar va ish haqi. Fayl bir marta yuklab olingach,
ilova yoki internet bo'lmasa ham ochiladi.
"""
import asyncio
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fpdf import FPDF, FontFace
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db import repo
from bot.db.models import MaterialEntry, PayKind, PersonalEntry, PersonalKind, User, WorkerPayment
from bot.services.utils import fmt_money, fmt_number, fmt_totals, today

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

PERIODS = {
    "day": "Bugun",
    "week": "Shu hafta",
    "month": "Shu oy",
    "prev_month": "O'tgan oy",
    "year": "Shu yil",
    "all": "Hammasi",
}

# «Hammasi»: shu sanadan bugungacha so'raladi, hisobotda esa birinchi yozuv sanasidan ko'rsatiladi
ALL_START = date(2000, 1, 1)

NAVY = (15, 31, 87)
BLUE = (43, 79, 201)
MUTED = (105, 115, 145)
SOFT = (238, 242, 251)
WHITE = (255, 255, 255)

# Shriftda yo'q belgilar (emoji, koreyscha harflar) — PDFda bo'sh kvadrat bo'lib chiqmasin
_UNSUPPORTED = re.compile("[\U00010000-\U0010ffff\u1100-\u11ff\u3000-\u9fff\uac00-\ud7af\ufe00-\ufe0f\u200d]")


def period_bounds(period: str, t: date | None = None) -> tuple[date, date]:
    t = t or today()
    if period == "day":
        return t, t
    if period == "week":
        start = t - timedelta(days=t.weekday())
        return start, start + timedelta(days=6)
    if period == "month":
        start = t.replace(day=1)
        return start, (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    if period == "prev_month":
        end = t.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if period == "year":
        return date(t.year, 1, 1), date(t.year, 12, 31)
    if period == "all":
        return ALL_START, t
    raise ValueError(period)


def range_text(start: date, end: date) -> str:
    if start == end:
        return start.strftime("%d.%m.%Y")
    # Har xil yilda bo'lsa (masalan «Hammasi») — boshlanishida ham yil
    head = start.strftime("%d.%m" if start.year == end.year else "%d.%m.%Y")
    return f"{head} – {end.strftime('%d.%m.%Y')}"


def file_name(user: User, period: str) -> str:
    start, end = period_bounds(period)
    if period == "day":
        tag = start.isoformat()
    elif period in ("month", "prev_month"):
        tag = start.strftime("%Y-%m")
    elif period == "year":
        tag = str(start.year)
    elif period == "all":
        tag = f"hammasi-{end.isoformat()}"
    else:
        tag = f"{start.isoformat()}_{end.isoformat()}"
    # Fayl nomida faqat lotin harflari: kirillcha ism bo'lsa ham telefon nomni buzmasin
    slug = re.sub(r"[^A-Za-z0-9]+", "-", user.name).strip("-")[:30]
    return f"hisobot-{slug + '-' if slug else ''}{tag}.pdf"


def caption(period: str) -> str:
    start, end = period_bounds(period)
    when = "boshidan bugungacha" if period == "all" else range_text(start, end)
    return (
        f"📄 <b>Hisobot — {PERIODS[period].lower()}</b> ({when})\n\n"
        "Faylni bosing → «⋮» → «Yuklab olish» (iPhone'da «Saqlash»). "
        "Telefoningizda saqlanadi — ilova yopilsa ham, internet bo'lmasa ham ochiladi."
    )


# --- Ma'lumot yig'ish --------------------------------------------------------

@dataclass
class ReportData:
    name: str
    user_id: int
    is_owner: bool
    period: str
    start: date
    end: date
    created: datetime
    show_work: bool = True
    work: list[repo.WorkStatRow] = field(default_factory=list)
    pending: int = 0
    pending_pay: int = 0
    # Ishchi bilan pul oldi-berdisi (davrda) va bugungi qoldiq: ishchi id -> qoldiq
    payments: list[WorkerPayment] = field(default_factory=list)
    balances: dict[int, dict[str, Decimal]] = field(default_factory=dict)
    worker_names: dict[int, str] = field(default_factory=dict)
    personal: list[PersonalEntry] = field(default_factory=list)
    clients: list[repo.ClientStats] = field(default_factory=list)
    materials: list[MaterialEntry] = field(default_factory=list)
    debts: list[repo.DebtBalance] = field(default_factory=list)


async def _collect(session: AsyncSession, user: User, period: str) -> ReportData:
    start, end = period_bounds(period)
    acc = user.account_id
    worker_id = None if user.is_owner else user.id
    data = ReportData(
        name=user.name,
        user_id=user.id,
        is_owner=user.is_owner,
        period=period,
        start=start,
        end=end,
        created=datetime.now(settings.tz),
    )
    data.work = await repo.work_stat_rows(session, acc, start, end, worker_id)
    data.pending = await repo.count_pending(session, acc, worker_id)
    data.pending_pay = await repo.count_pending_payments(session, acc, worker_id)
    data.payments = await repo.payments_between(session, acc, start, end, worker_id)
    balances = await repo.worker_balances(session, acc, None if user.is_owner else [user.id])
    data.balances = {wid: {c: v for c, v in b.balance.items() if v} for wid, b in balances.items()}
    if user.is_owner:
        data.worker_names = {w.id: w.name for w in await repo.list_workers(session, acc, active_only=False)}
        data.show_work = bool(data.work) or await repo.has_work(session, acc)
        personal = await repo.personal_entries_between(session, acc, start, end)
        data.personal = sorted(personal, key=lambda e: (e.entry_date, e.id))
        data.clients = [
            st
            for st in await repo.client_stats(session, acc, start, end)
            if st.sold or st.paid or any(st.balance.values())
        ]
        data.materials = await repo.material_entries_between(session, acc, start, end)
        # Umumiy qarzlar: odamlar, ta'minotchilar, ishchilar va mijozlar — ilovadagi «Qarzlar»dagidek
        data.debts = [b for b in await repo.debt_overview(session, acc) if not b.settled]
    if period == "all":
        dates = [
            *(r.work_date for r in data.work),
            *(p.entry_date for p in data.payments),
            *(e.entry_date for e in data.personal),
            *(e.entry_date for e in data.materials),
        ]
        if user.is_owner and (first := await repo.first_client_date(session, acc)):
            dates.append(first)
        data.start = min(dates, default=end)
    return data


async def build(session: AsyncSession, user: User, period: str) -> tuple[bytes, str]:
    """(PDF fayl, fayl nomi)."""
    data = await _collect(session, user, period)
    # PDF chizish — protsessor ishi, bot boshqa xabarlarni kutib qolmasin
    pdf = await asyncio.to_thread(_render, data)
    return pdf, file_name(user, period)


# --- Chizish -----------------------------------------------------------------

def _clean(text: str | None) -> str:
    return " ".join(_UNSUPPORTED.sub("", text or "").split())


def _money(totals: dict[str, Decimal]) -> str:
    # Valyutalar doim bir xil tartibda: so'm, $, won
    order = {c: i for i, c in enumerate(repo.ALL_CURRENCIES)}
    return fmt_totals({c: totals[c] for c in sorted(totals, key=lambda c: order.get(c, 99)) if totals[c]})


def _add(totals: dict[str, Decimal], currency: str | None, amount: Decimal | None) -> None:
    if amount is not None and currency:
        totals[currency] += amount


class _Pdf(FPDF):
    def __init__(self, footer_text: str):
        super().__init__(format="A4")
        self.footer_text = footer_text
        self.add_font("DejaVu", "", str(FONT_DIR / "DejaVuSans.ttf"))
        self.add_font("DejaVu", "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
        self.set_margins(14, 14, 14)
        self.set_auto_page_break(True, margin=16)
        self.set_creator("Moliyachi")
        self.set_author("Moliyachi")

    def footer(self) -> None:
        self.set_y(-11)
        self.set_font("DejaVu", "", 7.5)
        self.set_text_color(*MUTED)
        self.cell(0, 5, f"{self.footer_text}  ·  {self.page_no()}/{{nb}}", align="C")

    # --- Bloklar ---

    def title_block(self, d: ReportData) -> None:
        self.set_fill_color(*NAVY)
        self.rect(0, 0, self.w, 36, style="F")
        self.set_xy(14, 9)
        self.set_text_color(170, 190, 245)
        self.set_font("DejaVu", "B", 8.5)
        self.cell(0, 5, "M O L I Y A C H I")
        self.set_xy(14, 15)
        self.set_text_color(*WHITE)
        self.set_font("DejaVu", "B", 19)
        self.cell(0, 10, "Hisobot")
        self.set_font("DejaVu", "B", 11)
        self.set_xy(14, 9)
        self.cell(0, 6, PERIODS[d.period], align="R")
        self.set_font("DejaVu", "", 10)
        self.set_xy(14, 16)
        self.cell(0, 6, range_text(d.start, d.end), align="R")
        self.set_xy(14, 22)
        self.set_text_color(170, 190, 245)
        self.set_font("DejaVu", "", 8.5)
        self.cell(0, 6, f"Tuzilgan: {d.created.strftime('%d.%m.%Y %H:%M')}", align="R")

        self.set_xy(14, 42)
        self.set_text_color(*NAVY)
        self.set_font("DejaVu", "B", 13)
        self.cell(0, 7, _clean(d.name) or "—")
        self.ln(7)
        self.set_text_color(*MUTED)
        self.set_font("DejaVu", "", 9)
        role = "Korxona egasi — to'liq hisobot" if d.is_owner else "Ishchi — topshirgan ishlar va ish haqi"
        self.cell(0, 5, role)
        self.ln(8)

    def section(self, title: str) -> None:
        if self.get_y() > self.h - 60:
            self.add_page()
        self.ln(3)
        self.set_text_color(*NAVY)
        self.set_font("DejaVu", "B", 13)
        self.cell(0, 8, title)
        self.ln(8)
        self.set_draw_color(*BLUE)
        self.set_line_width(0.6)
        self.line(self.l_margin, self.get_y(), self.l_margin + 18, self.get_y())
        self.ln(4)

    def subtitle(self, title: str) -> None:
        if self.get_y() > self.h - 40:
            self.add_page()
        self.ln(1)
        self.set_text_color(*NAVY)
        self.set_font("DejaVu", "B", 10.5)
        self.cell(0, 6, title)
        self.ln(7)

    def note(self, text: str, color: tuple[int, int, int] = MUTED) -> None:
        self.set_text_color(*color)
        self.set_font("DejaVu", "", 9)
        self.multi_cell(0, 5, text)
        self.ln(2)

    def cards(self, items: list[tuple[str, str]]) -> None:
        """Yuqoridagi qisqa raqamlar: [(qiymat, izoh), ...]."""
        gap = 4
        width = (self.epw - gap * (len(items) - 1)) / len(items)
        lines = [value.split(" + ") for value, _ in items]
        height = 11 + 5.5 * max(len(v) for v in lines)
        if self.get_y() + height > self.h - 20:
            self.add_page()
        y = self.get_y()
        for i, ((_, label), values) in enumerate(zip(items, lines)):
            x = self.l_margin + i * (width + gap)
            self.set_fill_color(*SOFT)
            self.rect(x, y, width, height, style="F", round_corners=True, corner_radius=2.5)
            self.set_xy(x + 3, y + 2.5)
            self.set_text_color(*NAVY)
            self.set_font("DejaVu", "B", 11 if len(values) == 1 else 10)
            for value in values:
                self.set_x(x + 3)
                self.cell(width - 6, 5.5, value)
                self.ln(5.5)
            self.set_xy(x + 3, y + height - 7)
            self.set_text_color(*MUTED)
            self.set_font("DejaVu", "", 8)
            self.cell(width - 6, 5, label)
        self.set_y(y + height + 5)

    def grid(
        self,
        headers: list[str],
        rows: list[list[str]],
        widths: tuple[int, ...],
        align: tuple[str, ...],
        total: list[str] | None = None,
    ) -> None:
        self.set_font("DejaVu", "", 8.5)
        self.set_text_color(25, 30, 50)
        self.set_draw_color(*SOFT)
        self.set_line_width(0.2)
        with self.table(
            col_widths=widths,
            text_align=align,
            line_height=5.2,
            padding=(1.4, 2),
            borders_layout="HORIZONTAL_LINES",
            headings_style=FontFace(emphasis="BOLD", color=WHITE, fill_color=NAVY),
            cell_fill_color=(247, 249, 254),
            cell_fill_mode="ROWS",
            width=self.epw,
        ) as table:
            table.row(headers)
            for r in rows:
                table.row([_clean(c) for c in r])
            if total:
                row = table.row()
                for c in total:
                    row.cell(c, style=FontFace(emphasis="BOLD", fill_color=SOFT))
        self.ln(4)


def _work_name(r: repo.WorkStatRow) -> str:
    """Ish: model va kasb («A-12 · Tikuvchi»), modelsiz ishda — kasbning o'zi."""
    return " · ".join(filter(None, [r.model_name, r.job_name])) or "—"


def _pay_text(r: repo.WorkStatRow) -> str:
    return fmt_money(r.pay, r.currency) if r.pay is not None and r.currency else "narx yo'q"


def _balance_text(balance: dict[str, Decimal], owner: bool) -> str:
    """Ishchi qoldig'i: musbat — ishchi olishi kerak, manfiy — ortiqcha olgan (avans)."""
    due = {c: v for c, v in balance.items() if v > 0}
    over = {c: -v for c, v in balance.items() if v < 0}
    parts = []
    if due:
        parts.append(f"{'berasiz' if owner else 'olasiz'} {_money(due)}")
    if over:
        parts.append(f"avans {_money(over)}")
    return " · ".join(parts) or "hisob yopiq"


def _work_section(pdf: _Pdf, d: ReportData) -> None:
    rows = d.work
    pdf.section("Ishxona" if d.is_owner else "Topshirgan ishlarim")
    qty = sum(r.quantity for r in rows)
    pay: dict[str, Decimal] = defaultdict(Decimal)
    for r in rows:
        _add(pay, r.currency, r.pay)
    days = len({r.work_date for r in rows})
    if d.is_owner:
        third = (str(days), "ish kuni")
    else:
        mine = _balance_text(d.balances.get(d.user_id, {}), owner=False)
        third = (mine[0].upper() + mine[1:], "hisob-kitob (bugungi qoldiq)")
    pdf.cards(
        [
            (f"{fmt_number(qty)} ta", "jami dona"),
            (_money(pay), "ish haqi fondi" if d.is_owner else "ish haqim (shu davrda)"),
            third,
        ]
    )

    if d.is_owner:
        by_worker: dict[int, dict] = {}
        for r in rows:
            g = by_worker.setdefault(r.worker_id, {"qty": 0, "pay": defaultdict(Decimal)})
            g["qty"] += r.quantity
            _add(g["pay"], r.currency, r.pay)
        # Shu davrda ishlamagan, lekin hisobi ochiq ishchilar ham
        for worker_id, balance in d.balances.items():
            if balance:
                by_worker.setdefault(worker_id, {"qty": 0, "pay": defaultdict(Decimal)})
        if by_worker:
            pdf.subtitle("Ishchilar bo'yicha")
            pdf.grid(
                ["Ishchi", "Dona", "Ish haqi", "Qoldiq (bugun)"],
                [
                    [
                        d.worker_names.get(worker_id, "?"),
                        fmt_number(g["qty"]),
                        _money(g["pay"]),
                        _balance_text(d.balances.get(worker_id, {}), owner=True),
                    ]
                    for worker_id, g in sorted(by_worker.items(), key=lambda kv: -kv[1]["qty"])
                ],
                (40, 16, 40, 44),
                ("LEFT", "RIGHT", "RIGHT", "RIGHT"),
                total=["Jami", fmt_number(qty), _money(pay), ""],
            )
            pdf.note("«Qoldiq» — butun hisob bo'yicha bugungi holat: berasiz — ishchining haqqi, avans — ortiqcha olgani.")

    if not rows:
        pdf.note("Bu davrda tasdiqlangan ish yo'q.")
    else:
        by_work: dict[str, dict] = {}
        for r in rows:
            g = by_work.setdefault(_work_name(r), {"qty": 0, "pay": defaultdict(Decimal)})
            g["qty"] += r.quantity
            _add(g["pay"], r.currency, r.pay)
        pdf.subtitle("Ish turlari bo'yicha (model · kasb)")
        pdf.grid(
            ["Ish", "Dona", "Ish haqi"],
            [[name, fmt_number(g["qty"]), _money(g["pay"])] for name, g in sorted(by_work.items())],
            (50, 18, 50),
            ("LEFT", "RIGHT", "RIGHT"),
            total=["Jami", fmt_number(qty), _money(pay)],
        )

        pdf.subtitle("Kunlar bo'yicha batafsil")
        detail = sorted(rows, key=lambda r: (r.work_date, r.worker_name.lower(), _work_name(r).lower()))
        if d.is_owner:
            pdf.grid(
                ["Sana", "Ishchi", "Ish", "Dona", "Ish haqi"],
                [
                    [r.work_date.strftime("%d.%m.%Y"), r.worker_name, _work_name(r), fmt_number(r.quantity), _pay_text(r)]
                    for r in detail
                ],
                (22, 34, 40, 14, 34),
                ("LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT"),
            )
        else:
            pdf.grid(
                ["Sana", "Ish", "Dona", "Ish haqi"],
                [[r.work_date.strftime("%d.%m.%Y"), _work_name(r), fmt_number(r.quantity), _pay_text(r)] for r in detail],
                (24, 54, 16, 40),
                ("LEFT", "LEFT", "RIGHT", "RIGHT"),
            )
    if d.pending:
        pdf.note(f"Tasdiq kutayotgan ishlar: {d.pending} ta — tasdiqlanmaguncha hisobga kirmaydi.")


# Pul oldi-berdi turi: (egaga, ishchiga)
PAY_LABELS = {
    PayKind.PAY: ("berildi", "oldim"),
    PayKind.RETURN: ("qaytardi", "qaytardim"),
    PayKind.BALANCE: ("hisob holati", "hisob holati"),
}


def _pay_amount(p: WorkerPayment) -> str:
    """«−» — qoldiqdan ayriladi (olingan pul), «+» — qo'shiladi (qaytarilgan), hisob holatida — belgisiz."""
    sign = "−" if p.kind == PayKind.PAY else "+" if p.kind == PayKind.RETURN else ""
    return sign + fmt_money(abs(p.amount), p.currency)


def _payments_section(pdf: _Pdf, d: ReportData) -> None:
    pdf.section("Ishchilarga berilgan pul" if d.is_owner else "Olgan pullarim")
    given: dict[str, Decimal] = defaultdict(Decimal)
    returned: dict[str, Decimal] = defaultdict(Decimal)
    for p in d.payments:
        if p.kind == PayKind.PAY:
            given[p.currency] += p.amount
        elif p.kind == PayKind.RETURN:
            returned[p.currency] += p.amount
    pdf.cards(
        [
            (_money(given), "berildi" if d.is_owner else "oldim"),
            (_money(returned), "qaytarildi" if d.is_owner else "qaytardim"),
        ]
    )
    who = 0 if d.is_owner else 1
    if not d.payments:
        pdf.note("Bu davrda pul oldi-berdi yozilmagan.")
    elif d.is_owner:
        pdf.grid(
            ["Sana", "Ishchi", "Turi", "Izoh", "Summa"],
            [
                [
                    p.entry_date.strftime("%d.%m.%Y"),
                    p.worker.name,
                    PAY_LABELS.get(p.kind, ("", ""))[who],
                    p.note or "",
                    _pay_amount(p),
                ]
                for p in d.payments
            ],
            (22, 34, 24, 40, 30),
            ("LEFT", "LEFT", "LEFT", "LEFT", "RIGHT"),
        )
    else:
        pdf.grid(
            ["Sana", "Turi", "Izoh", "Summa"],
            [
                [p.entry_date.strftime("%d.%m.%Y"), PAY_LABELS.get(p.kind, ("", ""))[who], p.note or "", _pay_amount(p)]
                for p in d.payments
            ],
            (24, 28, 54, 34),
            ("LEFT", "LEFT", "LEFT", "RIGHT"),
        )
    if d.payments:
        pdf.note("«−» — qoldiqdan ayrilgan (olingan pul), «+» — qo'shilgan (qaytarilgan pul).")
    if d.pending_pay:
        pdf.note(f"Tasdiq kutayotgan pul yozuvlari: {d.pending_pay} ta — tasdiqlanmaguncha hisobga kirmaydi.")


def _personal_section(pdf: _Pdf, d: ReportData) -> None:
    pdf.section("Daromad va xarajat")
    income: dict[str, Decimal] = defaultdict(Decimal)
    expense: dict[str, Decimal] = defaultdict(Decimal)
    for e in d.personal:
        (income if e.kind == PersonalKind.INCOME else expense)[e.currency] += e.amount
    net = {c: income.get(c, Decimal(0)) - expense.get(c, Decimal(0)) for c in {*income, *expense}}
    pdf.cards([(_money(income), "daromad"), (_money(expense), "xarajat"), (_money(net), "qoldiq")])
    if not d.personal:
        pdf.note("Bu davrda daromad yoki xarajat yozilmagan.")
        return

    for kind, title in ((PersonalKind.EXPENSE, "Xarajatlar — kategoriyalar"), (PersonalKind.INCOME, "Daromadlar — kategoriyalar")):
        groups: dict[str, dict] = {}
        for e in d.personal:
            if e.kind != kind:
                continue
            g = groups.setdefault(e.category, {"count": 0, "sum": defaultdict(Decimal)})
            g["count"] += 1
            g["sum"][e.currency] += e.amount
        if not groups:
            continue
        pdf.subtitle(title)
        pdf.grid(
            ["Kategoriya", "Soni", "Summa"],
            [
                [cat, str(g["count"]), _money(g["sum"])]
                for cat, g in sorted(groups.items(), key=lambda kv: -max(kv[1]["sum"].values()))
            ],
            (60, 16, 50),
            ("LEFT", "RIGHT", "RIGHT"),
            total=["Jami", str(sum(g["count"] for g in groups.values())), _money(income if kind == PersonalKind.INCOME else expense)],
        )

    pdf.subtitle("Barcha yozuvlar")
    pdf.grid(
        ["Sana", "Turi", "Kategoriya", "Izoh", "Summa"],
        [
            [
                e.entry_date.strftime("%d.%m.%Y"),
                "Daromad" if e.kind == PersonalKind.INCOME else "Xarajat",
                e.category,
                e.note or "",
                ("+" if e.kind == PersonalKind.INCOME else "−") + fmt_money(e.amount, e.currency),
            ]
            for e in d.personal
        ],
        (22, 18, 30, 46, 32),
        ("LEFT", "LEFT", "LEFT", "LEFT", "RIGHT"),
    )


def _clients_section(pdf: _Pdf, d: ReportData) -> None:
    pdf.section("Mijozlar")
    owed: dict[str, Decimal] = defaultdict(Decimal)
    sold: dict[str, Decimal] = defaultdict(Decimal)
    paid: dict[str, Decimal] = defaultdict(Decimal)
    rows = []
    for st in d.clients:
        for c, v in st.balance.items():
            if v > 0:
                owed[c] += v
        for c, v in st.sold.items():
            sold[c] += v
        for c, v in st.paid.items():
            paid[c] += v
        debt = {c: v for c, v in st.balance.items() if v > 0}
        prepaid = {c: -v for c, v in st.balance.items() if v < 0}
        balance = " · ".join(
            filter(None, [f"qarz {_money(debt)}" if debt else "", f"oldindan {_money(prepaid)}" if prepaid else ""])
        )
        goods = _money(st.sold) + (f" ({st.sets} kompl.)" if st.sets else "") if st.sold else "—"
        rows.append([st.client.name, goods, _money(st.paid) if st.paid else "—", balance or "hisob-kitob yopiq"])
    pdf.cards([(_money(owed), "mijozlar qarzi (jami)"), (_money(sold), "mol berildi"), (_money(paid), "to'lov olindi")])
    pdf.grid(
        ["Mijoz", "Mol berildi", "To'lov olindi", "Qoldiq (bugungi)"],
        rows,
        (34, 40, 32, 42),
        ("LEFT", "RIGHT", "RIGHT", "RIGHT"),
    )
    pdf.note("«Mol berildi» va «To'lov olindi» — tanlangan davr uchun, «Qoldiq» — butun hisob bo'yicha bugungi holat.")


def _material_what(e: MaterialEntry) -> str:
    return f"{e.kind or '?'} «{e.name}»" if e.name else (e.kind or "?")


def _material_qty(e: MaterialEntry) -> str:
    return f"{fmt_number(e.quantity)} {e.unit or ''}".strip() if e.quantity else ""


def _material_amount(e: MaterialEntry) -> str:
    if e.amount is None:
        return "narxi aytilmagan"
    return fmt_money(e.amount, e.currency) + (" (qarzga)" if e.on_credit else "")


def _materials_section(pdf: _Pdf, d: ReportData) -> None:
    pdf.section("Xom ashyo")
    total: dict[str, Decimal] = defaultdict(Decimal)
    credit: dict[str, Decimal] = defaultdict(Decimal)
    kinds: dict[str, dict] = {}
    for e in d.materials:
        g = kinds.setdefault(e.kind or "?", {"count": 0, "sum": defaultdict(Decimal)})
        g["count"] += 1
        if e.amount is not None:
            total[e.currency] += e.amount
            g["sum"][e.currency] += e.amount
            if e.on_credit:
                credit[e.currency] += e.amount
    pdf.cards(
        [
            (_money(total), "jami xarid"),
            (_money(credit), "shundan qarzga olingan"),
            (f"{len(d.materials)} ta", "xaridlar soni"),
        ]
    )
    pdf.subtitle("Turlar bo'yicha")
    pdf.grid(
        ["Turi", "Soni", "Summa"],
        [
            [kind, str(g["count"]), _money(g["sum"]) if g["sum"] else "narxi aytilmagan"]
            for kind, g in sorted(kinds.items(), key=lambda kv: -max(kv[1]["sum"].values(), default=0))
        ],
        (60, 16, 50),
        ("LEFT", "RIGHT", "RIGHT"),
        total=["Jami", str(len(d.materials)), _money(total)],
    )
    pdf.subtitle("Barcha xaridlar")
    pdf.grid(
        ["Sana", "Xom ashyo", "Miqdor", "Kimdan", "Summa"],
        [
            [e.entry_date.strftime("%d.%m.%Y"), _material_what(e), _material_qty(e), e.supplier or "", _material_amount(e)]
            for e in d.materials
        ],
        (22, 42, 22, 30, 34),
        ("LEFT", "LEFT", "RIGHT", "LEFT", "RIGHT"),
    )


# Umumiy qarzlar bo'limlari (repo.DEBT_SOURCES)
DEBT_SOURCES = {"debt": "Qarz", "material": "Ta'minotchi", "worker": "Ishchi", "client": "Mijoz"}


def _debts_section(pdf: _Pdf, d: ReportData) -> None:
    pdf.section("Qarzlar (bugungi holat)")
    owed_to_me, i_owe = repo.debt_totals(d.debts)
    pdf.cards([(_money(owed_to_me), "sizga qarzlar"), (_money(i_owe), "sizning qarzingiz")])
    groups = repo.debt_groups(d.debts)
    if len(groups) > 1:
        pdf.subtitle("Bo'limlar bo'yicha")
        pdf.grid(
            ["Bo'lim", "Sizga qarz", "Sizning qarzingiz"],
            [
                [DEBT_SOURCES.get(source, source), _money(to_me) if to_me else "—", _money(mine) if mine else "—"]
                for source, (to_me, mine) in groups.items()
            ],
            (40, 50, 50),
            ("LEFT", "RIGHT", "RIGHT"),
            total=["Jami", _money(owed_to_me), _money(i_owe)],
        )
    rows = []
    for b in d.debts:
        last = b.last_date.strftime("%d.%m.%Y") if b.last_date else ""
        source = DEBT_SOURCES.get(b.source, "")
        to_me = {c: v for c, v in b.balance.items() if v > 0}
        mine = {c: -v for c, v in b.balance.items() if v < 0}
        if to_me:
            rows.append([b.person, source, "sizga qarzdor", _money(to_me), last])
        if mine:
            rows.append([b.person, source, "siz qarzdorsiz", _money(mine), last])
    pdf.subtitle("Kim bilan")
    pdf.grid(
        ["Kim", "Bo'lim", "Holat", "Summa", "Oxirgi"],
        rows,
        (38, 24, 28, 38, 22),
        ("LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT"),
    )
    pdf.note("Ilovadagi «Qarzlar»dagidek: odamlar bilan qarzlar, ta'minotchilar, ishchilar va mijozlar qoldig'i.")


def _render(d: ReportData) -> bytes:
    pdf = _Pdf(f"Moliyachi · {_clean(d.name)} · {PERIODS[d.period]} ({range_text(d.start, d.end)})")
    pdf.set_title(f"Hisobot — {PERIODS[d.period]} ({range_text(d.start, d.end)})")
    pdf.add_page()
    pdf.title_block(d)
    if d.is_owner:
        if d.show_work:
            _work_section(pdf, d)
            if d.payments or d.pending_pay:
                _payments_section(pdf, d)
        _personal_section(pdf, d)
        if d.clients:
            _clients_section(pdf, d)
        if d.materials:
            _materials_section(pdf, d)
        if d.debts:
            _debts_section(pdf, d)
    else:
        _work_section(pdf, d)
        _payments_section(pdf, d)
    return bytes(pdf.output())
