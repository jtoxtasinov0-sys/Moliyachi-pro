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

class WorkItem(BaseModel):
    worker_name: str | None = Field(description="Ishchi ismi, aytilmagan bo'lsa null")
    model_name: str | None = Field(description="Model nomi/kodi, aytilmagan bo'lsa null")
    quantity: int | None = Field(description="Dona soni, aytilmagan bo'lsa null")


class ClientItem(BaseModel):
    client_name: str | None
    direction: Literal["received", "given"] | None = Field(
        description="received — mijozdan oldim; given — mijozga berdim/topshirdim"
    )
    model_name: str | None
    quantity: int | None
    amount: float | None = Field(description="Umumiy summa")
    currency: Literal["UZS", "USD", "KRW"] | None


class PersonalItem(BaseModel):
    kind: Literal["expense", "income"] | None
    category: str | None
    amount: float | None
    currency: Literal["UZS", "USD", "KRW"] | None
    note: str | None


class Extraction(BaseModel):
    section: Literal["work", "client", "personal", "unknown"]
    date: str | None = Field(description="YYYY-MM-DD. Sana aytilmagan bo'lsa null")
    work: list[WorkItem]
    client: list[ClientItem]
    personal: list[PersonalItem]
    question: str | None = Field(
        description="Faqat section=unknown bo'lsa: foydalanuvchiga o'zbekcha qisqa izoh/savol"
    )


SYSTEM_PROMPT = """\
Sen tikuv korxonasining moliyaviy hisob botisan. Foydalanuvchi o'zbek tilida (lotin yoki kirill, \
og'zaki, ba'zan xato bilan) yozadi yoki gapiradi. Matn ovozdan avtomatik o'girilgan bo'lishi mumkin, \
shuning uchun ism va model nomlari biroz buzilgan bo'lishi mumkin.

Vazifang: matndan yozuvlarni ajratib, berilgan JSON sxemada qaytarish.

Bo'limlar:
- work — ishchi ish topshirdi: "Alisher A-12 dan 50 ta topshirdi", "bugun B5 modeldan 30 ta tikdim".
- client — mijoz bilan savdo: "Karimdan 200 ta oldim", "Valiga 100 ta topshirdim, 5 million so'm".
- personal — egasining shaxsiy xarajat/daromadi: "benzinga 200 ming", "oylik tushdi 10 million".
- unknown — yuqoridagilarning hech biri emas yoki tushunarsiz.

Qoidalar:
- Faqat tegishli bo'lim ro'yxatini to'ldir, qolgan ro'yxatlar bo'sh bo'lsin.
- Bitta xabarda bir nechta yozuv bo'lishi mumkin ("A-12 dan 50, B-5 dan 30") — har birini alohida element qil.
- Ism va model nomlarini "Ma'lum nomlar" ro'yxatidagi eng mos yozuvga keltir; mos keladigani \
bo'lmasa, eshitilgancha lotin yozuvida yoz. O'zingdan ism yoki son to'qima.
- Sonlarni raqamga aylantir: "ellik" -> 50, "bir yarim million" -> 1500000, "200 ming" -> 200000.
- Valyuta: so'm -> UZS, dollar/$ -> USD, von/won/₩ -> KRW. Aytilmasa null.
- Sanani bugungi sanaga nisbatan hisobla: "kecha", "o'tgan juma", "5-oktabr". Aytilmasa null.
"""


def _context_block(today: date, known: dict[str, list[str]]) -> str:
    lines = [f"Bugun: {today.isoformat()} ({WEEKDAYS[today.weekday()]})."]
    labels = {"workers": "Ishchilar", "models": "Modellar", "clients": "Mijozlar", "categories": "Kategoriyalar"}
    for key, names in known.items():
        if names:
            lines.append(f"{labels.get(key, key)}: {', '.join(names)}")
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


async def extract(
    text: str, today: date, known: dict[str, list[str]], worker_name: str | None = None
) -> Extraction:
    """worker_name berilsa — yozuvchi ishchi, bo'lim har doim "work" va ishchi o'zi."""
    extra = ""
    if worker_name:
        extra = (
            f"Bu xabarni ishchi {worker_name} yozdi. Bo'lim faqat work bo'lishi mumkin "
            f"(yoki tushunarsiz bo'lsa unknown). worker_name har doim \"{worker_name}\"."
        )
    content = f"{_context_block(today, known)}\n\nXabar:\n«{text}»"
    return await _call(content, extra)


async def correct(
    draft: dict, correction: str, today: date, known: dict[str, list[str]], worker_name: str | None = None
) -> Extraction:
    """Mavjud qoralamaga foydalanuvchi tuzatishini qo'llaydi."""
    extra = "Senga joriy qoralama va tuzatish beriladi. Tuzatishni qo'llab, TO'LIQ yangilangan natijani qaytar."
    if worker_name:
        extra += f" Bo'lim faqat work, worker_name har doim \"{worker_name}\"."
    content = (
        f"{_context_block(today, known)}\n\n"
        f"Joriy qoralama:\n{json.dumps(draft, ensure_ascii=False)}\n\n"
        f"Foydalanuvchi tuzatishi:\n«{correction}»"
    )
    return await _call(content, extra)
