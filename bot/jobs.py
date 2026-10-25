"""Kasblar (ish turlari): Kroychi, Brsovkachi, Tikuvchi, Tortuvchi, Pardozchi va egasi qo'shganlari.

Ovozda yoki matnda kasb turlicha aytiladi: «kroy qildim», «brsovka», «brsovshi», «padosh yopishtirdim».
Shu yerda ularni bazadagi kasbga bog'laymiz va «brsovka 2000, kroy 1500» kabi narx matnini o'qiymiz.
"""
import re
from decimal import Decimal

from bot.db.models import Job
from bot.services import matcher
from bot.services.utils import parse_price

# Oyoq kiyim ishlab chiqarish tartibida: kesish → shilish → tikish → padosh yopishtirish → sotuvga tayyorlash
DEFAULT_JOBS = ("Kroychi", "Brsovkachi", "Tikuvchi", "Tortuvchi", "Pardozchi")
# Bu ishlarda model so'ralmaydi: «50 ta ish qildim» — kasbning o'z narxi bilan hisoblanadi
MODEL_FREE_JOBS = ("Kroychi", "Brsovkachi")
# Modelning umumiy narxi — zagatovka (tikuv) narxi: faqat shu so'zli kasbga («Tikuvchi», «Zagatovkachi»)
SEWING_WORDS = ("tikuv", "tikish", "zagatov")

# Standart kasblarning boshqacha aytilishi (normallashgan ko'rinishda)
SYNONYMS = {
    "kroychi": ("kroy", "kroyka", "kroyshik", "kesish", "kesuvchi", "bichish", "bichuvchi"),
    "brsovkachi": ("brsovka", "brsovshi", "brsovchi", "brsovkashi", "birsovka", "brasovka", "shilish", "shiluvchi"),
    "tikuvchi": ("tikuv", "tikish"),
    "tortuvchi": ("tortish", "tortuv", "padosh", "padoshchi", "padosh yopishtirish", "tag yopishtirish"),
    "pardozchi": ("pardoz", "pardozlash", "sotuvga tayyorlash"),
}


def _stems(name: str) -> list[str]:
    """«Kroychi» -> «kroy», «Tikuvchi» -> «tikuv»: kasb ham, ish ham shu so'z bilan aytiladi."""
    n = matcher.normalize(name)
    return [n[: -len(suffix)] for suffix in ("chi", "shi") if n.endswith(suffix) and len(n) > len(suffix) + 2]


def is_sewing(name: str) -> bool:
    """Tikuvchi kasbi: «Tikuvchi», «Тикувчи», «Zagatovkachi»."""
    n = matcher.normalize(name)
    return any(word in n for word in SEWING_WORDS)


def takes_model_price(job: Job | None) -> bool:
    """Modelning umumiy (zagatovka) narxi shu ishga olinadimi: faqat tikuvchiga va kasbsiz ishga. Kroychi va
    boshqalarga — o'z narxi; yozilmagan bo'lsa ish narxsiz saqlanadi va egasiga eslatma chiqadi."""
    return job is None or is_sewing(job.name)


def price_example(name: str) -> str:
    """Narx aytish namunasi: «Kroychi» -> «kroy 2000dan», «Brsovkachi» -> «brsovka 2000dan»."""
    stems = _stems(name)
    return f"{stems[0] if stems else name.lower()} 2000dan"


def aliases(jobs: list[Job], saved: list[tuple[int, str]] | None = None) -> list[tuple[int, str]]:
    """Kasblarning boshqa nomlari: avval tanlangan to'g'rilashlar, so'z o'zagi, so'ng standart sinonimlar."""
    out = list(saved or [])
    for j in jobs:
        out.extend((j.id, stem) for stem in _stems(j.name))
    for j in jobs:
        out.extend((j.id, s) for s in SYNONYMS.get(matcher.normalize(j.name), ()))
    return out


def match(query: str, jobs: list[Job], saved: list[tuple[int, str]] | None = None) -> matcher.MatchResult:
    return matcher.match(query, [(j.id, j.name) for j in jobs], aliases(jobs, saved))


def clean_name(name: str) -> str:
    """Yangi kasb nomi: «  brsovkachi » -> «Brsovkachi»."""
    name = " ".join(name.split())[:50]
    return name[:1].upper() + name[1:]


# --- «brsovka 2000, kroy 1500» -------------------------------------------------

# Narx yonida keladigan, lekin kasb nomi bo'lmagan so'zlar
_FILLER = {
    "narx", "narxi", "narxini", "narxlar", "uchun", "bir", "dona", "donasi", "donaga", "ga", "ish", "haqi",
    "model", "modelga", "modelning", "bo'lsin", "qiling", "deb", "yoz", "yozing", "ta", "-", "—", ":", "=",
}
_GENERAL = {"umumiy", "hammasi", "hamma", "asosiy"}
_PRICE_WORD = re.compile(
    r"^(\$|₩|so'm\w*|som\w*|sum\w*|сўм\w*|сум\w*|uzs|usd|krw|dollar\w*|доллар\w*|won\w*|von\w*|вон\w*|"
    r"ming\w*|минг\w*|k|mln\w*|million\w*|млн\w*|dan|дан)$"
)


# «Loafer 1500», «Sapog BG: 2 ming», «A-12 1 500 so'm» — model nomi, oxirida narx (model kodida raqam bo'lishi mumkin)
_NAME_PRICE = re.compile(
    r"^(?P<name>.+?)\s*[-—:=]?\s+(?P<price>(?:\d{1,3}(?:[ \u00a0.,]\d{3})+|\d+)(?:[.,]\d+)?"
    r"(?:\s*(?:ming|минг|k|mln|million|млн)\w*)?(?:\s*(?:so'm|som|sum|сўм|сум|uzs|\$|usd|dollar\w*|won|krw))?"
    r"(?:dan|дан)?)$",
    re.IGNORECASE,
)


def parse_model_prices(
    text: str, models: list[tuple[int, str]], default_currency: str, saved: list[tuple[int, str]] | None = None
) -> tuple[list[tuple[int, Decimal | None, str]], str | None]:
    """«Loafer 1500, Sapog BG 2000» -> [(model id, narx yoki None — o'chirish, valyuta)].

    Tushunilmasa — ([], foydalanuvchiga xato matni)."""
    t = text.replace("‘", "'").replace("’", "'").replace("ʻ", "'")
    chunks = [c.strip() for c in re.split(r",(?!\d{3})|;|\n", t) if c.strip()]
    result: list[tuple[int, Decimal | None, str]] = []
    for chunk in chunks:
        m = _NAME_PRICE.match(chunk)
        if m is None:
            return [], f"«{chunk}» — model nomi va narxini yozing, masalan: Loafer 1500"
        name = m["name"].strip(" .:-—=")
        res = matcher.match(name, models, saved)
        if res.exact_id is None:
            return [], f"«{name}» — bunday model topilmadi. Modelni tugmadan tanlang."
        parsed = parse_price(m["price"], default_currency)
        if parsed is None:
            return [], f"«{m['price']}» — narxni tushunmadim. Masalan: 1500 yoki 2 ming"
        value, currency = parsed
        result.append((res.exact_id, value if value > 0 else None, currency))
    if not result:
        return [], "Masalan: «Loafer 1500, Sapog 2000»."
    return result, None


def split_prices(text: str) -> list[tuple[str, str]]:
    """«kroy 1500, brsovka 2 mingdan» -> [("kroy", "1500"), ("brsovka", "2 mingdan")].

    Kasb so'zi bo'lmagan juftlikda birinchi qism bo'sh — bu umumiy narx."""
    t = text.lower().replace("‘", "'").replace("’", "'").replace("ʻ", "'")
    # Ajratgichlar: vergul (o'nlik kasrdan tashqari), nuqtali vergul, yangi qator, «va»
    t = re.sub(r",(?!\d)|;|\n|\bva\b", " | ", t)
    pairs: list[tuple[str, str]] = []
    label: list[str] = []
    price: list[str] = []

    def flush() -> None:
        if label or price:
            pairs.append((" ".join(label), " ".join(price)))
        label.clear()
        price.clear()

    for tok in t.split():
        if tok == "|":
            flush()
        elif any(ch.isdigit() for ch in tok) or (price and _PRICE_WORD.match(tok)):
            price.append(tok)
        elif tok.strip(".:") in _FILLER:
            continue
        else:
            if price:  # narxdan keyin yangi kasb so'zi — keyingi juftlik
                flush()
            label.append(tok)
    flush()
    return pairs


def parse_prices(
    text: str, jobs: list[Job], default_currency: str, saved: list[tuple[int, str]] | None = None
) -> tuple[list[tuple[Job | None, Decimal | None, str]], str | None]:
    """Matndan (kasb yoki None — umumiy, narx yoki None — o'chirish, valyuta) ro'yxati.

    Tushunilmasa — ([], foydalanuvchiga xato matni)."""
    by_id = {j.id: j for j in jobs}
    result: list[tuple[Job | None, Decimal | None, str]] = []
    for label, price_text in split_prices(text):
        words = label.strip(" .:-")
        job = None
        if words and words not in _GENERAL:
            res = match(words, jobs, saved)
            if res.exact_id is None:
                names = ", ".join(j.name for j in jobs) or "hali kasb yo'q"
                return [], (
                    f"«{words}» — bunday kasb topilmadi. Kasblar: {names}. "
                    "Yangi kasb «👷 Ishchilar → 🏷 Kasblar»da qo'shiladi."
                )
            job = by_id[res.exact_id]
        if not price_text:
            return [], f"{job.name if job else 'Umumiy'} narxini son bilan yozing, masalan: 2000"
        parsed = parse_price(price_text, default_currency)
        if parsed is None:
            return [], f"«{price_text}» — narxni tushunmadim. Masalan: 2000 yoki 2 ming"
        value, currency = parsed
        result.append((job, value if value > 0 else None, currency))
    if not result:
        return [], "Narxni yozing, masalan: «brsovka 2000» yoki «kroy 1500, tikuv 5000»."
    return result, None
