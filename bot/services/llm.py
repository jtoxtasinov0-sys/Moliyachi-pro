"""Claude: erkin matndan tuzilgan ma'lumot (JSON) ajratib olish."""
import json
import logging
from datetime import date
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from bot.config import settings
from bot.services.utils import WEEKDAYS

log = logging.getLogger(__name__)

client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=3, timeout=60)


class LlmError(Exception):
    pass


# --- Javob sxemasi ----------------------------------------------------------

Currency = Literal["UZS", "USD", "KRW"]


class WorkItem(BaseModel):
    worker_name: str | None = Field(description="Ishchi ismi, aytilmagan bo'lsa null")
    model_name: str | None = Field(description="Model nomi/kodi, aytilmagan bo'lsa null")
    quantity: int | None = Field(description="Dona soni, aytilmagan bo'lsa null")


class ClientItem(BaseModel):
    client_name: str | None = Field(description="Mijoz ismi, aytilmagan bo'lsa null")
    kind: Literal["sale", "payment", "return"] | None = Field(
        description="sale — mijozga mol berdim/sotdim; payment — mijoz pul to'ladi (men undan pul oldim); "
        "return — mijoz molni qaytardi"
    )
    product: str | None = Field(description="Mahsulot/model nomi (masalan: Maksima), aytilmasa null")
    sets: int | None = Field(description="Komplekt soni, aytilmasa null")
    pieces: int | None = Field(description="Faqat dona aytilganda (komplekt emas) — dona soni, aks holda null")
    price_per_set: float | None = Field(description="Bir komplekt narxi")
    price_per_piece: float | None = Field(description="Bir dona narxi")
    total: float | None = Field(description="Umumiy summa (to'lovda — to'langan summa)")
    currency: Currency | None
    note: str | None = Field(description="Qisqa izoh, bo'lmasa null")


class PersonalItem(BaseModel):
    kind: Literal["expense", "income"] | None = Field(description="expense — xarajat, income — daromad/kirim")
    category: str | None = Field(description="Qisqa kategoriya nomi, masalan: Oziq-ovqat, Mashina")
    amount: float | None
    currency: Currency | None
    note: str | None = Field(description="Qisqa izoh (nimaga sarflandi), bo'lmasa null")


class DebtItem(BaseModel):
    person: str | None = Field(description="Odam ismi, aytilmagan bo'lsa null")
    direction: Literal["in", "out"] | None = Field(
        description="in — men undan pul oldim (qarz oldim yoki u qarzini qaytardi); "
        "out — men unga pul berdim (qarz berdim yoki qarzimni qaytardim)"
    )
    amount: float | None
    currency: Currency | None
    note: str | None = Field(description="Qisqa izoh, bo'lmasa null")


class Extraction(BaseModel):
    section: Literal["work", "client", "personal", "debt", "unknown"]
    date: str | None = Field(description="YYYY-MM-DD. Sana aytilmagan bo'lsa null")
    work: list[WorkItem]
    client: list[ClientItem]
    personal: list[PersonalItem]
    debt: list[DebtItem]
    question: str | None = Field(
        description="Faqat section=unknown bo'lsa: foydalanuvchiga o'zbekcha qisqa izoh/savol"
    )


EXPENSE_CATEGORIES = (
    "Oziq-ovqat", "Kafe", "Mashina", "Transport", "Uy-joy", "Kommunal", "Aloqa", "Kiyim",
    "Sog'liq", "Ta'lim", "Ko'ngilochar", "Sovg'a", "Pul o'tkazma", "Boshqa",
)
INCOME_CATEGORIES = ("Oylik", "Biznes", "Sovg'a", "Boshqa")

SYSTEM_PROMPT = f"""\
Sen moliyaviy hisob botisan: tikuv korxonasi hisobi va egasining shaxsiy hamyoni. Foydalanuvchi \
o'zbek tilida (lotin yoki kirill, og'zaki, ba'zan xato bilan) yozadi yoki gapiradi. Matn ovozdan \
avtomatik o'girilgan bo'lishi mumkin, shuning uchun ism va model nomlari biroz buzilgan bo'lishi mumkin.

Vazifang: matndan yozuvlarni ajratib, berilgan JSON sxemada qaytarish.

Bo'limlar:
- work — ishchi ish topshirdi: "Alisher A-12 dan 50 ta topshirdi", "bugun B5 modeldan 30 ta tikdim".
- client — korxona mijozlar bilan optom savdo qiladi: mijozga mol (komplekt yoki dona) berish/sotish, \
mijozning to'lovi, mijoz molni qaytarishi: "Tohirga 10 komplekt maksima berdim, 140 mingdan", \
"Tohir 2 million to'ladi", "Tohir 3 komplekt qaytardi".
- personal — egasining shaxsiy xarajati yoki daromadi: "benzinga 200 ming", "bozorga 50 ming won", \
"oylik tushdi 10 million".
- debt — biror odam bilan qarz: "Abbosdan 100 ming won oldim", "Valiga 50 dollar qarz berdim", \
"Abbosga 30 mingini qaytardim", "Vali 20 dollar qaytardi".
- unknown — yuqoridagilarning hech biri emas yoki tushunarsiz.

Qoidalar:
- Faqat tegishli bo'lim ro'yxatini to'ldir, qolganlari bo'sh bo'lsin. Istisno: bitta xabarda ham \
xarajat/daromad, ham qarz bo'lsa — personal va debt ikkalasini to'ldir, section esa asosiysi.
- Bitta xabarda bir nechta yozuv bo'lishi mumkin ("A-12 dan 50, B-5 dan 30") — har birini alohida element qil.
- Mijoz bilan savdo (client):
  * Mol (komplekt, dona, model nomi) berilsa/sotilsa — har doim client, kind=sale, odam "Mijozlar" \
ro'yxatida bo'lmasa ham (yangi mijoz).
  * "Mijozlar" ro'yxatidagi odam pul to'lasa yoki undan pul olinsa — client, kind=payment. \
"mijoz", "to'lov qildi", "pul tashladi" so'zlari ham client payment.
  * Komplekt — bitta modelning har razmeridan bittadan. Komplekt soni — sets, dona soni — pieces.
  * Narx: "140 mingdan", "140 ming so'mlik" (komplekt soni bilan birga) — bir komplekt narxi \
(price_per_set). "donasi 28 ming" — price_per_piece. "jami", "hammasi", "umumiy" bilan — total. \
Narxni o'zing hisoblama, faqat aytilganini yoz.
- Odamdan pul olish yoki odamga pul berish (dona/tovar aytilmagan, odam mijoz emas) — bu debt. Odam kimligini \
so'rama, faqat ismini yoz. "undan oldim" -> in, "unga berdim"/"qaytardim" -> out, "u qaytardi" -> in.
- Do'kon, bozor, benzin, kafe, to'lov kabi narsaga pul sarflash — personal (expense), debt emas.
- personal kategoriyasi: avval "Kategoriyalar" ro'yxatidan mosini tanla, bo'lmasa quyidagilardan: \
xarajat — {", ".join(EXPENSE_CATEGORIES)}; daromad — {", ".join(INCOME_CATEGORIES)}. \
Benzin, moy, avtoservis, jarima, parkovka — Mashina.
- Ism va model nomlarini "Ma'lum nomlar" ro'yxatidagi eng mos yozuvga keltir; mos keladigani \
bo'lmasa, eshitilgancha lotin yozuvida, bosh harf bilan yoz. O'zingdan ism yoki son to'qima.
- Sonlarni raqamga aylantir: "ellik" -> 50, "bir yarim million" -> 1500000, "200 ming" -> 200000.
- Valyuta: so'm -> UZS, dollar/$ -> USD, von/won/₩ -> KRW. Aytilmasa null.
- Sanani bugungi sanaga nisbatan hisobla: "kecha", "o'tgan juma", "5-oktabr". Aytilmasa null.
"""

_LABELS = {
    "workers": "Ishchilar",
    "models": "Modellar",
    "clients": "Mijozlar",
    "categories": "Kategoriyalar",
    "people": "Qarz bo'yicha odamlar",
}


def _context_block(today: date, known: dict[str, list[str]], info: str = "") -> str:
    lines = [f"Bugun: {today.isoformat()} ({WEEKDAYS[today.weekday()]})."]
    if info:
        lines.append(info)
    for key, names in known.items():
        if names:
            lines.append(f"{_LABELS.get(key, key)}: {', '.join(names)}")
    return "Ma'lum nomlar:\n" + "\n".join(lines)


async def _call(user_content: str, extra_rules: str = "") -> Extraction:
    system = SYSTEM_PROMPT + (f"\n{extra_rules}" if extra_rules else "")
    try:
        response = await client.messages.parse(
            model=settings.llm_model,
            max_tokens=4000,
            system=system,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": user_content}],
            output_format=Extraction,
        )
    except anthropic.APIError as e:
        log.exception("Claude API xatosi")
        raise LlmError(str(e)) from e

    if response.stop_reason == "refusal" or response.parsed_output is None:
        log.warning("Claude javob bermadi: stop_reason=%s", response.stop_reason)
        raise LlmError(f"stop_reason={response.stop_reason}")
    return response.parsed_output


def _worker_rules(worker_name: str) -> str:
    return (
        f"Bu xabarni ishchi {worker_name} yozdi. Bo'lim faqat work bo'lishi mumkin "
        f"(yoki tushunarsiz bo'lsa unknown). worker_name har doim \"{worker_name}\"."
    )


async def extract(
    text: str, today: date, known: dict[str, list[str]], worker_name: str | None = None, info: str = ""
) -> Extraction:
    """worker_name berilsa — yozuvchi ishchi, bo'lim har doim "work" va ishchi o'zi."""
    extra = _worker_rules(worker_name) if worker_name else ""
    content = f"{_context_block(today, known, info)}\n\nXabar:\n«{text}»"
    return await _call(content, extra)


async def correct(
    draft: dict,
    correction: str,
    today: date,
    known: dict[str, list[str]],
    worker_name: str | None = None,
    info: str = "",
) -> Extraction:
    """Mavjud qoralamaga foydalanuvchi tuzatishini qo'llaydi."""
    extra = "Senga joriy qoralama va tuzatish beriladi. Tuzatishni qo'llab, TO'LIQ yangilangan natijani qaytar."
    if worker_name:
        extra += " " + _worker_rules(worker_name)
    content = (
        f"{_context_block(today, known, info)}\n\n"
        f"Joriy qoralama:\n{json.dumps(draft, ensure_ascii=False)}\n\n"
        f"Foydalanuvchi tuzatishi:\n«{correction}»"
    )
    return await _call(content, extra)
