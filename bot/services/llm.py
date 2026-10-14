"""Claude: erkin matn yoki rasmdan (bank skrinshoti, chek, daftar) tuzilgan ma'lumot (JSON) ajratib olish."""
import base64
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


ITEM_DATE = (
    "Shu yozuvning o'z sanasi YYYY-MM-DD — faqat yozuvlar turli kunlarga tegishli bo'lsa (masalan, bank "
    "tarixida). Umumiy sana bilan bir xil yoki noma'lum bo'lsa null"
)


class PersonalItem(BaseModel):
    kind: Literal["expense", "income"] | None = Field(description="expense — xarajat, income — daromad/kirim")
    category: str | None = Field(description="Qisqa kategoriya nomi, masalan: Oziq-ovqat, Mashina")
    amount: float | None
    currency: Currency | None
    note: str | None = Field(description="Qisqa izoh (nimaga sarflandi), bo'lmasa null")
    date: str | None = Field(description=ITEM_DATE)


class DebtItem(BaseModel):
    person: str | None = Field(description="Odam ismi, aytilmagan bo'lsa null")
    direction: Literal["in", "out"] | None = Field(
        description="in — men undan pul oldim yoki men unga qarzdorman (qarz oldim, u qarzini qaytardi, "
        "\"qarzim bor\", \"uning haqqi bor\"); "
        "out — men unga pul berdim yoki u menga qarzdor (qarz berdim, qarzimni qaytardim, "
        "\"undan haqqim bor\", \"undan olishim kerak\")"
    )
    stated: bool = Field(
        description="true — pul hozir berilmadi/olinmadi, faqat kim kimga qarzdorligi aytildi "
        "(\"haqqim bor\", \"qarzim bor\", \"mendan qarzi bor\"); false — pul berildi yoki olindi"
    )
    amount: float | None
    currency: Currency | None
    note: str | None = Field(
        description="Qisqa izoh egasi tilidan (men — egasi), qo'shimcha ma'lumot bo'lmasa null. "
        "Gapni qayta aytma va odamlarni almashtirib yuborma"
    )
    date: str | None = Field(description=ITEM_DATE)


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
- Qarz holati (pul hozir berilmagan, kim kimga qarzdorligi aytilgan) ham debt:
  * U menga qarzdor -> out: "Hayrulloda 65 million haqqim bor", "Valida 100 dollar pulim bor", "Vali mendan 50 ming qarz", "Validan 2 million olishim kerak", "Valining menga qarzi bor".
  * Men unga qarzdorman -> in: "Validan 50 ming qarzim bor", "Valiga 2 million qarzman", "Valiga berishim kerak", "Valining mendan haqqi bor".
  * "haqqim bor" — men pul olishim kerak (u menga qarzdor), "haqqi bor" — u pul olishi kerak (men qarzdorman).
  * Bunday qarz holatida stated=true. Pul haqiqatan berilgan/olingan bo'lsa stated=false.
- debt note: odatda null. Yozsang — egasi tilidan, ma'noni o'zgartirmay ("u menga qarzdor" yoki "men qarzdorman"), "haqqim"ni "haqqi"ga almashtirma.
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


async def _call(
    user_content: str | list[dict],
    extra_rules: str = "",
    *,
    effort: str = "low",
    max_tokens: int = 4000,
    timeout: float | None = None,
) -> Extraction:
    system = SYSTEM_PROMPT + (f"\n{extra_rules}" if extra_rules else "")
    try:
        response = await client.messages.parse(
            model=settings.llm_model,
            max_tokens=max_tokens,
            system=system,
            output_config={"effort": effort},
            messages=[{"role": "user", "content": user_content}],
            output_format=Extraction,
            timeout=timeout or anthropic.not_given,
        )
    except anthropic.APIError as e:
        log.exception("Claude API xatosi")
        raise LlmError(str(e)) from e
    except ValueError as e:  # javob sxemaga mos kelmadi (masalan, max_tokens da uzilib qoldi)
        log.exception("Claude javobini o'qib bo'lmadi")
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


IMAGE_RULES = """\
Senga rasm(lar) beriladi: bank ilovasi skrinshoti (to'lovlar tarixi, chek, o'tkazma), bank SMS xabari, \
do'kon cheki yoki qo'lda yozilgan daftar sahifasi (telefon kamerasida qiyshiq yoki xira tushgan bo'lishi \
mumkin). Rasmdagi har bir moliyaviy yozuvni ajrat.

Yozuv va til: rasmdagi matn o'zbekcha (lotin yoki kirill), ruscha, koreyscha (한글) yoki inglizcha bo'lishi, \
qo'lda yozilgan bo'lishi mumkin — hammasini diqqat bilan o'qi.
- Javobdagi hamma matn lotin yozuvidagi o'zbek tilida. Kirill yozuvidagi ism, joy va izohni lotinga o'gir \
(Алишер — Alisher, Тошкент — Toshkent, Ҳамидулло — Hamidullo).
- Koreyscha nom: asl yozuvini qoldir va qavsda o'zbekcha qisqa ma'nosini qo'sh — "이마트 (supermarket)", \
"김밥천국 (kafe)", "택시 (taksi)". Lotin harfli brend nomini (GS25, CU, Coupang) o'zgartirma.
- category — har doim o'zbekcha, kategoriyalar ro'yxatidan.
- Sana: Koreya ilovalari, SMS va cheklarida oy.kun (10.13, 10/13, 10월 13일 — 13-oktabr; 2026.10.13); O'zbekiston va \
Rossiya ilovalarida kun.oy (13.10, 13 окт, 13-okt).
- Summa: bo'shliq, vergul yoki nuqta — minglik ajratgich yoki tiyin. 1 250 000,00 so'm = 1250000; \
86,500원 = 86500; ₩1,550 = 1550; "50 минг" / "50 ming" = 50000.

Bank ilovasi, SMS va chek (Payme, Click, Uzum, O'zbekiston banklari; KB국민, 신한, 우리, 하나, 토스, \
카카오뱅크 va boshqa Koreya banklari):
- Har bir tranzaksiya — personal ro'yxatida alohida element, section=personal.
- Chiqim (minus belgisi, "To'lov", "Chiqim", "Xarid", "Тўлов", "Чиқим", "Списание", "Оплата", "Перевод", \
출금, 결제, 승인, 이체, 송금, 지출) — expense. Kirim (plus belgisi, "Kirim", "Tushum", "Кирим", "Пополнение", \
"Зачисление", 입금, 수입, 급여, oylik) — income.
- Kategoriyani do'kon yoki xizmat nomidan aniqla: supermarket, bozor, oziq-ovqat do'koni, 편의점 \
(Korzinka, Makro, Havas, CU, GS25, 이마트) — Oziq-ovqat; kafe, restoran, ovqat yetkazish (Evos, Wolt, \
Yandex Eats, 배달의민족) — Kafe; AYOQSH, benzin, metan, propan, 주유소 — Mashina; taksi, metro, avtobus, \
poyezd, aviabilet (Yandex Go, 교통, 택시) — Transport; telefon, internet (Beeline, Ucell, Uzmobile, Mobiuz, \
KT, SKT, LG U+) — Aloqa; gaz, svet, suv, kommunal, 관리비 — Kommunal; dorixona, klinika, 약국, 병원 — \
Sog'liq; boshqa odamning kartasiga o'tkazma — Pul o'tkazma.
- note — do'kon yoki qabul qiluvchi/jo'natuvchi nomi, qisqa (yuqoridagi yozuv qoidasi bilan).
- amount — hisobdan yechilgan yoki hisobga tushgan summa (musbat son). Komissiya alohida ko'rsatilgan \
bo'lsa — summaga qo'sh.
- Har bir tranzaksiyaning sanasi rasmda ko'rinsa — date maydoniga yoz. Yil ko'rsatilmagan bo'lsa — \
bugungi kundan oldingi eng yaqin shunday sana.
- Yozuv emas, tashlab ket: karta qoldig'i/balans ("Qoldiq", "Қолдиқ", "Остаток", "Баланс", 잔액), keshbek \
va bonus ballar, bekor qilingan, rad etilgan yoki o'tmagan to'lovlar ("Bekor qilindi", "Отменено", 취소, \
거절, 실패), o'z kartalari yoki hisoblari orasidagi o'tkazma.
- Valyuta rasmdan: so'm, сум, UZS — UZS; $, USD — USD; 원, ₩, KRW — KRW. Ko'rinmasa null.
- Bitta tranzaksiyani ikki marta yozma, u bir nechta rasmda takrorlansa ham.
- Odamga o'tkazma — qarz emas, Pul o'tkazma xarajati. Foydalanuvchi izohida qarz deyilgan bo'lsa — debt.

Qo'lda yozilgan daftar yoki boshqa matnli rasm — undagi yozuvlarni oddiy xabar kabi yuqoridagi qoidalar \
bilan ajrat.
Rasmda moliyaviy yozuv bo'lmasa — section=unknown, question'da rasmda nima ko'ringanini qisqa ayt.
Foydalanuvchi izohi berilsa — undagi ko'rsatmaga amal qil (masalan, sana yoki qaysi yozuvlar kerakligi)."""


async def extract_images(
    images: list[tuple[bytes, str]],
    today: date,
    known: dict[str, list[str]],
    worker_name: str | None = None,
    info: str = "",
    caption: str = "",
) -> Extraction:
    """images — (rasm, media turi) ro'yxati: bitta yoki bir nechta skrinshot bitta qoralamaga."""
    extra = IMAGE_RULES + ("\n" + _worker_rules(worker_name) if worker_name else "")
    content: list[dict] = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(data).decode()},
        }
        for data, media_type in images
    ]
    text = f"{_context_block(today, known, info)}\n\nRasmlar soni: {len(images)}."
    if caption:
        text += f"\nFoydalanuvchi izohi:\n«{caption}»"
    content.append({"type": "text", "text": text})
    # Ko'p qatorli bank tarixi: aniq o'qish uchun chuqurroq o'ylaydi va uzunroq javob yozadi
    return await _call(content, extra, effort="medium", max_tokens=16000, timeout=180)


# --- Shaxsiy moliyaviy maslahat --------------------------------------------

ADVICE_PROMPT = """\
Sen shaxsiy moliya bo'yicha samimiy va amaliy maslahatchisan. Foydalanuvchiga uning so'nggi oylardagi \
daromad va xarajatlari (kategoriyalar bo'yicha, [zarur] yoki [boshqa] belgisi bilan), qarzlari va \
50/30/20 qoidasi bo'yicha joriy oylik reja beriladi (zarur xarajatlar / boshqa xarajatlar / jamg'arma).

Vazifang — shu raqamlarga qarab shaxsiy maslahat berish:
- qaysi kategoriyada pul ko'p ketyapti va uni oyiga qanchaga kamaytirish mumkin;
- oyiga qancha jamg'arish kerak, zaxira fond (3–6 oylik xarajat) maqsadi va unga necha oyda yetadi;
- qarz bo'lsa — uni yopish tartibi;
- oy oxirigacha kunlik limit.

Qoidalar:
- O'zbek tilida, lotin yozuvida, sodda va samimiy yoz, "siz" deb murojaat qil.
- 4–7 ta qisqa band, har biri yangi qatordan "• " bilan boshlansin. Oxirida bitta qisqa rag'batlantiruvchi gap.
- Aniq summalar bilan yoz. Faqat berilgan raqamlarga tayan, raqam to'qima. Valyutalarni bir-biriga aylantirma.
- Ma'lumot kam bo'lsa (masalan, daromad yozilmagan) — shuni ayt va nimani yozib borish kerakligini tushuntir.
- Aniq aksiya, kriptovalyuta yoki moliyaviy mahsulot tavsiya qilma — umumiy tamoyillarni ayt (zaxira fond, \
bank omonati, qarzni yopish).
- Markdown, jadval, sarlavha va ** belgilarini ishlatma — oddiy matn. Javob 1200 belgidan oshmasin."""


async def advise(context: str) -> str:
    """Daromad/xarajat ma'lumotiga qarab shaxsiy maslahat (oddiy matn)."""
    try:
        response = await client.messages.create(
            model=settings.llm_advice_model,
            max_tokens=8000,
            system=ADVICE_PROMPT,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": context}],
            timeout=90,
        )
    except anthropic.APIError as e:
        log.exception("Claude API xatosi (maslahat)")
        raise LlmError(str(e)) from e
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if response.stop_reason == "refusal" or not text:
        log.warning("Claude maslahat bermadi: stop_reason=%s", response.stop_reason)
        raise LlmError(f"stop_reason={response.stop_reason}")
    return text
