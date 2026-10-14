"""Moliyaviy maslahatchi: topgan pulingizdan qanchasini xarajatga, qanchasini jamg'armaga ajratish.

Asos — 50/30/20 qoidasi: daromadning 50% — zarur xarajatlar (ovqat, uy, kommunal, yo'l...),
30% — boshqa xarajatlar (kafe, kiyim, ko'ngilochar...), 20% — jamg'arma. Jamg'arma ulushini egasi
10/20/30% qilib tanlaydi, boshqa xarajatlar ulushi shunga qarab o'zgaradi.

Valyutalar bir-biriga aylantirilmaydi: har bir valyuta bo'yicha alohida reja.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from html import escape

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import repo
from bot.db.models import PersonalEntry, PersonalKind
from bot.services.utils import CURRENCY_LABELS, fmt_money, fmt_totals, today

NEEDS_CATEGORIES = frozenset(
    {"Oziq-ovqat", "Uy-joy", "Kommunal", "Transport", "Mashina", "Aloqa", "Sog'liq", "Ta'lim"}
)
NEEDS_PCT = 50
# Oldingi necha oy o'rtacha daromad/xarajat uchun olinadi
HISTORY_MONTHS = 3
MONTHS = [
    "yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr",
]
# Tavsiya summalari shu aniqlikkacha yaxlitlanadi
_ROUND_TO = {"UZS": Decimal(1000), "KRW": Decimal(100), "USD": Decimal(1)}

LESSONS = [
    "<b>Avval o'zingizga to'lang.</b> Pul tushgan kuni darhol jamg'arma ulushini alohida karta yoki "
    "hisobga o'tkazing. Oy oxirida «nima qolsa» jamg'arish deyarli hech qachon ishlamaydi.",
    "<b>50/30/20 qoidasi.</b> Daromadning 50% — zarur xarajatlar (ovqat, uy, kommunal, yo'l), 30% — "
    "xohishlar (kafe, kiyim, dam olish), 20% — jamg'arma. Boshlash uchun eng oson taqsimot.",
    "<b>Zaxira fond.</b> Kamida 3 oylik, yaxshisi 6 oylik xarajatga teng pulni tegmasdan saqlang. "
    "Ish to'xtasa yoki kasal bo'lsangiz — qarz olmasdan yashaysiz.",
    "<b>Har bir xarajatni yozing.</b> Bir oy yozib borsangiz, pul qayerga «oqib» ketayotganini ko'rasiz. "
    "Ko'pchilik kafe va mayda xaridlarga o'ylaganidan 2 barobar ko'p sarflaydi.",
    "<b>24 soat qoidasi.</b> Rejada yo'q qimmat narsani darhol olmang — bir kun kuting. "
    "Ertasi kuni ham kerak bo'lsa, oling. Ko'p xohishlar bir kunda o'tib ketadi.",
    "<b>Kunlik limit.</b> Oylik xarajat rejasini oyning qolgan kunlariga bo'ling — shu kunlik limit. "
    "Bugun kam sarflasangiz, ertangi limit ko'payadi.",
    "<b>Mayda xarajatlar yig'iladi.</b> Kuniga 30 ming so'mlik kofe va gazak — oyiga 900 ming, "
    "yiliga 10 million so'mdan ortiq.",
    "<b>Qarz — salbiy jamg'arma.</b> Avval qimmat (foizli) qarzlarni yoping. Qarzni yopish ham — "
    "o'zingizga qilingan jamg'arma.",
    "<b>Maqsad qo'ying.</b> «Pul yig'aman» emas, «6 oyda 12 million — mashina uchun boshlang'ich to'lov». "
    "Aniq maqsad va muddat bo'lsa, oyiga qancha ajratish kerakligi ham aniq bo'ladi.",
    "<b>Ro'yxat bilan bozorga boring.</b> Ro'yxatsiz xarid odatda 20–30% qimmatga tushadi. "
    "Och qoringa do'konga kirmang.",
    "<b>Obunalarni tekshiring.</b> Telefon tarifi, internet, ilovalar — oyda bir marta qarab chiqing. "
    "Ishlatilmayotganini o'chiring.",
    "<b>Daromad oshsa — xarajat emas, jamg'arma oshsin.</b> Oylik ko'tarilsa, qo'shimcha pulning "
    "kamida yarmini jamg'armaga qo'shing.",
    "<b>Jamg'armani ko'rinmas joyda saqlang.</b> Har kuni ishlatiladigan kartada turgan pul tez tugaydi. "
    "Alohida omonat yoki alohida karta oching.",
    "<b>Bank omonati.</b> Zaxira fondni oddiy kartada emas, foizli omonatda saqlang — pul inflyatsiyadan "
    "biroz himoyalanadi. Omonat muddati va pulni erta olish shartlarini o'qing.",
    "<b>Inflyatsiya.</b> Narxlar har yili oshadi: yostiq ostidagi pul yildan-yilga kamroq narsaga yetadi. "
    "Uzoq muddatli jamg'armani foiz beradigan joyda saqlang.",
    "<b>Haftalik ko'rib chiqish.</b> Haftada bir marta 5 daqiqa ajratib, «💸 Xarajatlar → Shu hafta»ni "
    "ko'ring. Rejadan chiqqan bo'lsangiz, keyingi haftada tuzatasiz.",
    "<b>Katta xaridni rejalang.</b> Telefon, mebel, sovg'a — narxini oylarga bo'ling va har oy shuncha "
    "ajrating. Muddatli to'lov (nasiya)ga qaraganda arzonga tushadi.",
    "<b>Nasiya va kredit karta.</b> «Oyiga atigi 300 ming» — jami narxni hisoblang. Ustama ko'pincha "
    "20–50% bo'ladi. Imkon bo'lsa, yig'ib, naqd oling.",
    "<b>Bayram va to'ylar.</b> Ular har yili bor — oldindan har oy kichik summa ajratib boring, "
    "shunda bayram oyida qarzga botmaysiz.",
    "<b>Avtomatlashtiring.</b> Oylik tushgan kuni jamg'armaga avtomatik o'tkazma qo'ying. "
    "Iroda kerak bo'lmagan reja — eng ishonchli reja.",
    "<b>Zarur va xohish farqi.</b> Ovqat — zarur, restoran — xohish. Kiyim — zarur, uchinchi kurtka — xohish. "
    "Xarajat oldidan o'zingizdan so'rang: «Bu menga kerakmi yoki shunchaki yoqdimi?»",
    "<b>Naqd pul bilan limit.</b> Xarajat ko'p bo'layotgan bo'lsa, haftalik limitni naqd oling va faqat "
    "shundan ishlating. Naqd pulni berish kartadan to'lashdan «og'riqliroq» — kam sarflanadi.",
    "<b>Daromad manbaini ko'paytiring.</b> Faqat tejash bilan boyib bo'lmaydi. Qo'shimcha ko'nikma, "
    "kichik qo'shimcha ish — daromadning ikkinchi oqimi xavfsizlikni oshiradi.",
    "<b>Oilaviy byudjet.</b> Oilada pul haqida ochiq gaplashing: umumiy maqsad, umumiy limit. "
    "Bir kishi tejab, ikkinchisi sarflasa — reja ishlamaydi.",
    "<b>Qarz berishdan oldin.</b> Qaytmasa ham hayotingizga ta'sir qilmaydigan summanigina qarzga bering. "
    "Qarzlarni «🤝 Qarzlar» bo'limida yozib boring.",
    "<b>Sog'liq — eng arzon investitsiya.</b> Profilaktika davolanishdan ancha arzon. Sog'liq uchun ham "
    "byudjetda kichik ulush qoldiring.",
    "<b>O'zingizga sarmoya.</b> Kitob, kurs, kasbiy ko'nikma — daromadni oshiradigan xarajat. "
    "«Ta'lim» kategoriyasini xarajat emas, sarmoya deb biling.",
    "<b>Kichikdan boshlang.</b> 20% qiyin bo'lsa — 10% dan boshlang, har 3 oyda 2–3% qo'shing. "
    "Muhimi — har oy, to'xtamasdan.",
    "<b>Oy yakuni.</b> Oy oxirida 3 savolga javob bering: qancha topdim, qancha sarfladim, qancha "
    "jamg'ardim? «💡 Maslahat» bo'limi buni siz uchun hisoblaydi.",
    "<b>Murakkab foiz.</b> Har oy 1 million jamg'arib, yiliga 20% foiz olsangiz, 5 yilda ~100 million "
    "bo'ladi — shundan 40 milliondan ortig'i foizdan. Qancha erta boshlasangiz, shuncha ko'p.",
]


def lesson_of_day() -> int:
    return today().toordinal() % len(LESSONS)


def lesson_text(index: int) -> str:
    index %= len(LESSONS)
    return f"📚 <b>Saboq {index + 1}/{len(LESSONS)}</b>\n\n{LESSONS[index]}"


def month_start(d: date, back: int = 0) -> date:
    year, month = d.year, d.month - back
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def month_end(start: date) -> date:
    return (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def round_money(value: Decimal, currency: str) -> Decimal:
    step = _ROUND_TO.get(currency, Decimal(1))
    return (value / step).quantize(Decimal(1), rounding=ROUND_HALF_UP) * step


def is_need(category: str) -> bool:
    return category in NEEDS_CATEGORIES


@dataclass
class Budget:
    """Bitta valyuta bo'yicha shu oyning rejasi va haqiqiy holati."""

    currency: str
    savings_pct: int
    days_left: int  # bugun ham kiradi
    income_month: Decimal = Decimal(0)  # shu oy tushgan daromad
    income_avg: Decimal = Decimal(0)  # oldingi oylar o'rtacha daromadi
    expense_avg: Decimal = Decimal(0)  # oldingi oylar o'rtacha xarajati
    needs_spent: Decimal = Decimal(0)
    wants_spent: Decimal = Decimal(0)
    by_category: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    today_spent: Decimal = Decimal(0)
    today_income: Decimal = Decimal(0)

    @property
    def base(self) -> Decimal:
        """Reja shu daromadga tuziladi: shu oy tushgani, hali tushmagan bo'lsa — o'rtachasi."""
        return self.income_month if self.income_month > 0 else self.income_avg

    @property
    def from_avg(self) -> bool:
        return self.income_month <= 0 < self.income_avg

    @property
    def wants_pct(self) -> int:
        return 100 - NEEDS_PCT - self.savings_pct

    def share(self, pct: int) -> Decimal:
        return round_money(self.base * pct / 100, self.currency)

    @property
    def needs_budget(self) -> Decimal:
        return self.share(NEEDS_PCT)

    @property
    def wants_budget(self) -> Decimal:
        return self.share(self.wants_pct)

    @property
    def savings_budget(self) -> Decimal:
        return self.share(self.savings_pct)

    @property
    def spent(self) -> Decimal:
        return self.needs_spent + self.wants_spent

    @property
    def spend_left(self) -> Decimal:
        """Shu oy xarajat uchun qolgani (manfiy — rejadan oshgan)."""
        return self.needs_budget + self.wants_budget - self.spent

    @property
    def daily_limit(self) -> Decimal:
        return round_money(max(self.spend_left, Decimal(0)) / max(self.days_left, 1), self.currency)

    @property
    def saved(self) -> Decimal:
        """Shu oy hozircha ortib qolgani: tushgan daromad − xarajat."""
        return self.income_month - self.spent

    def money(self, value: Decimal) -> str:
        return fmt_money(value, self.currency)


async def budgets(session: AsyncSession, owner_id: int) -> list[Budget]:
    """Shu oy va oldingi HISTORY_MONTHS oy yozuvlari bo'yicha har bir valyutaga reja."""
    t = today()
    current = month_start(t)
    rows = await repo.personal_stat_rows(session, owner_id, month_start(t, HISTORY_MONTHS), month_end(current))
    pct = await repo.savings_pct(session, owner_id)
    days_left = (month_end(current) - t).days + 1

    result: dict[str, Budget] = {}
    past_income: dict[str, Decimal] = defaultdict(Decimal)
    past_expense: dict[str, Decimal] = defaultdict(Decimal)
    past_months: dict[str, set[date]] = defaultdict(set)
    for r in rows:
        b = result.get(r.currency)
        if b is None:
            b = result[r.currency] = Budget(r.currency, pct, days_left)
        if r.entry_date < current:
            # Faqat yozuv bo'lgan oylar o'rtachaga kiradi: botni yaqinda boshlaganni bo'sh oylar kamaytirmasin
            past_months[r.currency].add(month_start(r.entry_date))
            if r.kind == PersonalKind.INCOME:
                past_income[r.currency] += r.amount
            else:
                past_expense[r.currency] += r.amount
            continue
        if r.kind == PersonalKind.INCOME:
            b.income_month += r.amount
            if r.entry_date == t:
                b.today_income += r.amount
            continue
        if r.entry_date > t:
            continue  # kelajak sanali xarajat bu kungi limitni buzmasin
        if is_need(r.category):
            b.needs_spent += r.amount
        else:
            b.wants_spent += r.amount
        b.by_category[r.category] += r.amount
        if r.entry_date == t:
            b.today_spent += r.amount

    for currency, b in result.items():
        months = len(past_months[currency])
        if months:
            b.income_avg = round_money(past_income[currency] / months, currency)
            b.expense_avg = round_money(past_expense[currency] / months, currency)

    order = await repo.enabled_currencies(session, owner_id)
    return sorted(result.values(), key=lambda b: order.index(b.currency) if b.currency in order else len(order))


# --- Matnlar ----------------------------------------------------------------

def _status(spent: Decimal, budget: Decimal, b: Budget) -> str:
    if spent > budget:
        return f"sarflandi {b.money(spent)} · ⚠️ <b>{b.money(spent - budget)} oshdi</b>"
    return f"sarflandi {b.money(spent)} · qoldi {b.money(budget - spent)} ✅"


def _limit_line(b: Budget) -> str:
    if b.spend_left > 0:
        return (
            f"📅 Oy oxirigacha {b.days_left} kun, xarajat uchun {b.money(b.spend_left)} qoldi.\n"
            f"Kunlik limit: <b>{b.money(b.daily_limit)}</b> — shundan oshirmang."
        )
    return f"⛔ Bu oylik xarajat rejasi tugadi: {b.money(-b.spend_left)} oshib ketdi."


def _tips(b: Budget, i_owe: Decimal) -> list[str]:
    tips = []
    if b.needs_spent > b.needs_budget:
        cat, value = max(((c, v) for c, v in b.by_category.items() if is_need(c)), key=lambda kv: kv[1])
        tips.append(
            f"Zarur xarajatlar rejadan oshdi. Eng kattasi — «{escape(cat)}» ({b.money(value)}): arzonroq "
            "variant izlang (bozor, ulgurji xarid, jamoat transporti)."
        )
    wants = sorted(((c, v) for c, v in b.by_category.items() if not is_need(c)), key=lambda kv: kv[1], reverse=True)
    if wants and b.wants_spent > b.wants_budget:
        cat, value = wants[0]
        tips.append(
            f"Boshqa xarajatlar rejadan oshdi. Eng ko'pi «{escape(cat)}»ga ketdi ({b.money(value)}) — oy oxirigacha "
            "shuni kamaytirsangiz, jamg'arma saqlanib qoladi."
        )
    elif wants and b.wants_budget > 0 and wants[0][1] * 2 > b.wants_budget:
        cat, value = wants[0]
        tips.append(
            f"«{escape(cat)}»ga {b.money(value)} ketdi — boshqa xarajatlar rejasining yarmidan ko'pi. "
            "Oy hali tugamadi, ehtiyot bo'ling."
        )
    if b.from_avg:
        tips.append(
            f"Daromad tushgan kuni darhol jamg'arma ulushini (<b>{b.money(b.savings_budget)}</b>) alohida karta "
            "yoki omonatga o'tkazing — avval o'zingizga to'lang."
        )
    elif b.saved >= b.savings_budget > 0:
        tips.append(
            f"Barakalla! Bu oy hozircha {b.money(b.saved)} ortib turibdi — rejadagi jamg'arma bor. "
            f"Jamg'arma ulushini ({b.money(b.savings_budget)}) hoziroq alohida hisobga o'tkazib qo'ying."
        )
    elif b.savings_budget > 0:
        tips.append(
            f"Jamg'arma rejasi — {b.money(b.savings_budget)}, hozircha ortib turgani {b.money(max(b.saved, Decimal(0)))}. "
            f"Kunlik limitga amal qilsangiz, oy oxirida reja bajariladi."
        )
    if i_owe > 0:
        tips.append(
            f"Qarzingiz bor: {b.money(i_owe)}. Jamg'arma ulushining bir qismini qarzni yopishga yo'naltiring — "
            "qarzdan qutulish ham jamg'arma."
        )
    if b.expense_avg > 0:
        tips.append(
            f"🛟 Zaxira fond maqsadi: kamida 3 oylik xarajat — <b>{b.money(b.expense_avg * 3)}</b> "
            f"(yaxshisi 6 oylik — {b.money(b.expense_avg * 6)}). Ish yoki daromad to'xtasa, qarzsiz yashaysiz."
        )
    return tips


def _budget_block(b: Budget, i_owe: Decimal, with_title: bool) -> str:
    title = f"<b>— {CURRENCY_LABELS.get(b.currency, b.currency)} —</b>\n" if with_title else ""
    if b.base <= 0:
        return (
            f"{title}💸 Bu oy xarajat: <b>{b.money(b.spent)}</b>\n"
            "Daromad hali yozilmagan. Taqsimot uchun topgan pulingizni ham yozing: "
            "<i>«oylik tushdi 5 million»</i>, <i>«bugun 300 ming ishladim»</i>."
        )
    if b.from_avg:
        income = f"💰 O'rtacha oylik daromad: <b>{b.money(b.base)}</b>\n<i>(bu oy hali daromad yozilmagan)</i>"
    else:
        income = f"💰 Bu oy daromad: <b>{b.money(b.base)}</b>"
    plan = (
        "<b>Shu puldan ajrating:</b>\n"
        f"🏠 Zarur xarajatlarga ({NEEDS_PCT}%) — <b>{b.money(b.needs_budget)}</b>\n"
        f"      {_status(b.needs_spent, b.needs_budget, b)}\n"
        f"🎉 Boshqa xarajatlarga ({b.wants_pct}%) — <b>{b.money(b.wants_budget)}</b>\n"
        f"      {_status(b.wants_spent, b.wants_budget, b)}\n"
        f"🏦 Jamg'armaga ({b.savings_pct}%) — <b>{b.money(b.savings_budget)}</b>"
    )
    parts = [title + income, plan, _limit_line(b) + f"\nBugun sarflandi: {b.money(b.today_spent)}"]
    tips = _tips(b, i_owe)
    if tips:
        parts.append("📌 <b>Maslahat:</b>\n" + "\n".join(f"• {tip}" for tip in tips))
    return "\n\n".join(parts)


async def _i_owe(session: AsyncSession, owner_id: int) -> dict[str, Decimal]:
    _, i_owe = repo.debt_totals(await repo.debt_balances(session, owner_id))
    return i_owe


async def advice_text(session: AsyncSession, owner_id: int) -> str:
    t = today()
    title = f"💡 <b>Moliyaviy maslahat — {MONTHS[t.month - 1]}</b>"
    items = await budgets(session, owner_id)
    if not items:
        return (
            f"{title}\n\nHali daromad yoki xarajat yozilmagan.\n\n"
            "Avval topgan pulingizni yozing: <i>«oylik tushdi 5 million»</i> — bot uni qanday taqsimlashni "
            "(xarajatga qancha, jamg'armaga qancha) ko'rsatadi. Keyin har kuni xarajatlaringizni yozib boring: "
            "<i>«bozorga 80 ming, taksiga 20 ming»</i>.\n\n" + lesson_text(lesson_of_day())
        )
    i_owe = await _i_owe(session, owner_id)
    blocks = [_budget_block(b, i_owe.get(b.currency, Decimal(0)), len(items) > 1) for b in items]
    rule = (
        f"<i>Qoida: daromadning {NEEDS_PCT}% — zarur, {items[0].wants_pct}% — boshqa xarajatlar, "
        f"{items[0].savings_pct}% — jamg'arma. Jamg'arma ulushini pastdagi tugmalar bilan o'zgartiring.</i>"
    )
    text = "\n\n".join([title, *blocks, rule, lesson_text(lesson_of_day())])
    if len(text) > 4000:  # Telegram xabari 4096 belgigacha: uch valyutada saboqsiz
        text = "\n\n".join([title, *blocks])
    return text


async def reminder_text(session: AsyncSession, owner_id: int) -> str:
    t = today()
    items = await budgets(session, owner_id)
    spent = {b.currency: b.today_spent for b in items if b.today_spent}
    earned = {b.currency: b.today_income for b in items if b.today_income}
    parts = [f"🔔 <b>Bugungi hisob</b> — {t.strftime('%d.%m')}"]
    if spent or earned:
        line = []
        if spent:
            line.append(f"💸 {fmt_totals(spent)}")
        if earned:
            line.append(f"💰 {fmt_totals(earned)}")
        parts.append(
            "Bugun yozildi: " + " · ".join(line) + "\nYana xarajat yoki daromad bo'lgan bo'lsa — qo'shib qo'ying."
        )
    else:
        parts.append(
            "Bugun ishlatgan yoki topgan pulingizni kiriting — ovoz, matn yoki chek rasmi bilan.\n"
            "Masalan: <i>«bozorga 80 ming, taksiga 20 ming»</i>, <i>«bugun 500 ming ishladim»</i>.\n"
            "Bugun xarajat bo'lmagan bo'lsa — bu ham yutuq 👍"
        )
    for b in items:
        if b.base > 0:
            parts.append(_limit_line(b))
    parts.append("💡 " + LESSONS[lesson_of_day()])
    return "\n\n".join(parts)


async def saved_hint(session: AsyncSession, owner_id: int, entries: list[PersonalEntry]) -> str:
    """Yangi yozuvdan keyingi qisqa tavsiya: daromadni qanday taqsimlash yoki qolgan kunlik limit."""
    if not entries:
        return ""
    current = month_start(today())
    incomes: dict[str, Decimal] = defaultdict(Decimal)
    expense_currencies = []
    for e in entries:
        if e.kind == PersonalKind.INCOME:
            incomes[e.currency] += e.amount
        elif e.entry_date >= current and e.currency not in expense_currencies:
            expense_currencies.append(e.currency)
    pct = await repo.savings_pct(session, owner_id)
    wants_pct = 100 - NEEDS_PCT - pct
    lines = []
    for currency, total in incomes.items():
        def part(p: int) -> str:
            return fmt_money(round_money(total * p / 100, currency), currency)

        lines.append(
            f"💡 Shu daromaddan ({fmt_money(total, currency)}): 🏦 jamg'armaga — <b>{part(pct)}</b> ({pct}%), "
            f"🏠 zarur xarajatlarga — {part(NEEDS_PCT)}, 🎉 boshqasiga — {part(wants_pct)}. "
            "Jamg'arma ulushini hoziroq alohida hisobga o'tkazing."
        )
    if expense_currencies:
        for b in await budgets(session, owner_id):
            if b.currency in expense_currencies and b.base > 0:
                lines.append(_limit_line(b))
    return "\n\n".join(lines)


# --- Claude uchun ma'lumot -------------------------------------------------

async def advice_context(session: AsyncSession, owner_id: int) -> str:
    """Shaxsiy maslahat uchun: so'nggi oylar daromad/xarajati, reja va qarzlar — oddiy matn."""
    t = today()
    start = month_start(t, HISTORY_MONTHS)
    rows = await repo.personal_stat_rows(session, owner_id, start, t)
    # (oy, valyuta) -> tur -> kategoriya -> summa
    data: dict[tuple[date, str], dict[str, dict[str, Decimal]]] = defaultdict(
        lambda: {PersonalKind.INCOME: defaultdict(Decimal), PersonalKind.EXPENSE: defaultdict(Decimal)}
    )
    for r in rows:
        data[(month_start(r.entry_date), r.currency)][r.kind][r.category] += r.amount

    lines = [f"Bugun: {t.isoformat()}. Oy oxirigacha {(month_end(month_start(t)) - t).days + 1} kun qoldi."]
    for (month, currency), kinds in sorted(data.items()):
        label = f"{MONTHS[month.month - 1]} {month.year}" + (" (joriy oy, hali tugamagan)" if month == month_start(t) else "")
        lines.append(f"\n{label}, {currency}:")
        for kind, name in ((PersonalKind.INCOME, "Daromad"), (PersonalKind.EXPENSE, "Xarajat")):
            cats = kinds[kind]
            if not cats:
                continue
            total = sum(cats.values(), Decimal(0))
            detail = ", ".join(
                f"{c} {fmt_money(v, currency)}" + ("" if kind == PersonalKind.INCOME else (" [zarur]" if is_need(c) else " [boshqa]"))
                for c, v in sorted(cats.items(), key=lambda kv: kv[1], reverse=True)
            )
            lines.append(f"  {name}: {fmt_money(total, currency)} — {detail}")

    i_owe = await _i_owe(session, owner_id)
    for b in await budgets(session, owner_id):
        if b.base <= 0:
            continue
        lines.append(
            f"\nJoriy reja ({b.currency}, {NEEDS_PCT}/{b.wants_pct}/{b.savings_pct}): daromad asosi "
            f"{b.money(b.base)}{" (oldingi oylar o'rtachasi)" if b.from_avg else ''}; zarur — {b.money(b.needs_budget)} "
            f"(sarflandi {b.money(b.needs_spent)}), boshqa — {b.money(b.wants_budget)} (sarflandi {b.money(b.wants_spent)}), "
            f"jamg'arma — {b.money(b.savings_budget)}; kunlik limit {b.money(b.daily_limit)}."
        )
    owed_to_me, _ = repo.debt_totals(await repo.debt_balances(session, owner_id))
    if i_owe:
        lines.append(f"Mening qarzlarim: {fmt_totals(i_owe)}.")
    if owed_to_me:
        lines.append(f"Menga qarzlar (boshqalar menga qarzdor): {fmt_totals(owed_to_me)}.")
    return "\n".join(lines)
