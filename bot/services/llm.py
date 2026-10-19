"""Claude: erkin matn yoki rasmdan (bank skrinshoti, chek, daftar) tuzilgan ma'lumot (JSON) ajratib olish."""
import base64
import json
import logging
from datetime import date
from typing import Literal, get_args, get_origin

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
# Yangi maydonlar bo'sh satr yoki 0 bilan «aytilmagan»: null (anyOf) sxemani og'irlashtiradi,
# structured outputs esa union turli maydonlar sonini cheklaydi
CurrencyOrEmpty = Literal["UZS", "USD", "KRW", ""]


ITEM_DATE = (
    "Shu yozuvning o'z sanasi YYYY-MM-DD — faqat yozuvlar turli kunlarga tegishli bo'lsa (masalan, bank "
    "tarixida). Umumiy sana bilan bir xil yoki noma'lum bo'lsa null"
)
ITEM_DATE_TEXT = (
    "Shu yozuvning o'z sanasi YYYY-MM-DD — faqat yozuvlar turli kunlarga tegishli bo'lsa (masalan, yozishma "
    "skrinshotida). Umumiy sana bilan bir xil yoki noma'lum bo'lsa bo'sh satr"
)


class WorkItem(BaseModel):
    worker_name: str | None = Field(description="Ishchi ismi, aytilmagan bo'lsa null")
    model_name: str | None = Field(
        description="Model nomi/kodi, aytilmagan bo'lsa null (o'zingdan qo'shma: kroy, brsovkada model aytilmaydi)"
    )
    job: str = Field(
        description="Qanday ish qilingani — kasb nomi \"Kasblar\" ro'yxatidan (kroy — Kroychi, brsovka — "
        "Brsovkachi, tikish — Tikuvchi...). Faqat ish turi aytilsa, aks holda bo'sh satr"
    )
    quantity: int | None = Field(description="Dona soni, aytilmagan bo'lsa null")
    date: str = Field(description=ITEM_DATE_TEXT)


class PriceItem(BaseModel):
    model_name: str = Field(
        description="Model nomi/kodi; aytilmagan bo'lsa bo'sh satr (\"kroy 2000dan\" — kasbning hamma modellar uchun narxi)"
    )
    job: str = Field(
        description="Qaysi kasb uchun narx — \"Kasblar\" ro'yxatidan. Kasb aytilmasa (umumiy narx) bo'sh satr"
    )
    price: float = Field(description="Bir dona uchun ish haqi, aytilmagan bo'lsa 0")
    currency: CurrencyOrEmpty = Field(description="Aytilmasa bo'sh satr")


class MaterialItem(BaseModel):
    kind: str = Field(description="Xom ashyo turi, bosh harf bilan: Teri, Material, Kley, Padosh, Ip...")
    name: str = Field(description="Nomi yoki markasi (\"nomi Shirin gul\", \"Lider\"), aytilmasa bo'sh satr")
    quantity: float = Field(description="Miqdori (1 list — 1, 20 kg — 20), aytilmasa 0")
    unit: str = Field(
        description="O'lchov birligi: list, metr, kg, litr, dona, juft, rulon, pachka; aytilmasa bo'sh satr"
    )
    price_per_unit: float = Field(
        description="Bir birlik narxi (\"listi 300 ming\", \"300 mingdan\"), aytilmasa 0"
    )
    total: float = Field(description="Jami summa (\"10 millionlik\", \"jami 10 million\"), aytilmasa 0")
    currency: CurrencyOrEmpty = Field(description="Aytilmasa bo'sh satr")
    supplier: str = Field(
        description="Kimdan yoki qayerdan olingani (\"Ta'minotchilar\" ro'yxatidagi yozilishi), aytilmasa bo'sh satr"
    )


class PayItem(BaseModel):
    worker_name: str = Field(
        description="Ishchi ismi \"Ishchilar\" ro'yxatidan; ishchi o'zi yozsa — uning ismi; aytilmasa bo'sh satr"
    )
    kind: Literal["pay", "return", "balance", ""] = Field(
        description="pay — ishchiga pul berildi, ishchi pul oldi (avans, oylik, ish haqi); "
        "return — ishchi pulni qaytardi; balance — pul berilmadi, faqat hisob holati (qoldiq) aytildi"
    )
    amount: float = Field(description="Summa, aytilmasa 0. balance da — qoldiq summasi, hisob yopilgan bo'lsa 0")
    whole: bool = Field(
        description="Faqat pay: summa aytilmay \"qolgan hamma pulini berdim\", \"hisobini to'liq berdim\" "
        "deyilsa true, aks holda false"
    )
    worker_owes: bool = Field(
        description="Faqat balance: true — ishchi egasiga qarzdor (ortiqcha olgan); false — egasi ishchiga "
        "qarzdor (ishchining haqqi qolgan) yoki hisob yopilgan"
    )
    currency: CurrencyOrEmpty = Field(description="Aytilmasa bo'sh satr")
    note: str = Field(description="Qisqa izoh (avans, oylik, bonus), bo'lmasa bo'sh satr")
    date: str = Field(description=ITEM_DATE_TEXT)


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
    section: Literal["work", "pay", "price", "material", "client", "personal", "debt", "unknown"]
    date: str | None = Field(description="YYYY-MM-DD. Sana aytilmagan bo'lsa null")
    work: list[WorkItem]
    pays: list[PayItem]
    prices: list[PriceItem]
    materials: list[MaterialItem]
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
Sen moliyaviy hisob botisan: tikuv korxonasi (kiyim yoki oyoq kiyim) hisobi va egasining shaxsiy \
hamyoni. Foydalanuvchi o'zbek tilida (lotin yoki kirill, og'zaki, ba'zan xato bilan) yozadi yoki \
gapiradi. Matn ovozdan avtomatik o'girilgan bo'lishi mumkin, shuning uchun ism, model va kasb nomlari \
biroz buzilgan bo'lishi mumkin.

Vazifang: matndan yozuvlarni ajratib, berilgan JSON sxemada qaytarish.

Bo'limlar:
- work — ishchi ish topshirdi: "Alisher A-12 dan 50 ta topshirdi", "bugun B5 modeldan 30 ta tikdim", \
"Vali A-12 dan 40 ta brsovka qildi", "kecha 100 ta kroy qildim A-12". Kroychi va brsovkachi ko'pincha modelsiz \
aytadi: "50 ta ish qildim", "bugun 80 ta kroy", "Nodira 50 ta brsovka qildi" — model_name null.
- pay — ishchi bilan pul hisob-kitobi: ishchiga avans yoki ish haqi berildi, ishchi pul oldi yoki \
qaytardi, ishchi bilan hisob holati aytildi: "Robiyaga 500 ming berdim", "Robiya 500 ming avans oldi", \
"Robiya 200 ming qaytardi", "Robiyaga 900 ming qarzman", "Robiya bilan hisob yopildi".
- price — egasi model uchun bir dona ish haqi narxini belgilaydi (kasb bo'yicha yoki umumiy): \
"A-12 modelga brsovka 2000dan", "A-12: kroy 1500, tikuv 5000, tortish 3000", "B-5 narxi 4000 bo'lsin". \
Model aytilmay faqat kasb narxi — kasbning hamma modellar uchun narxi, model_name bo'sh satr: "kroy 2000dan", \
"kroychiga 2000 so'mdan", "brsovka 1500 bo'lsin". Ishchi ismi va dona soni aytilmaydi.
- material — korxona uchun xom ashyo sotib olindi: teri, material (mato), podklad, kley (yelim), padosh \
(tag), ip, furnitura (zamok, tugma, lenta), upakovka (karobka, paket): "1 list teri oldim", \
"10 millionlik teri oldim, nomi Shirin gul", "Lider terisidan 5 list, listi 300 ming", \
"Alisherdan 20 kg kley oldim", "Xayrulloh Uz charmdan 1 millionlik material oldim". Bu shaxsiy xarajat \
(personal) emas.
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
- Xabar SMS kabi qisqa bo'lishi mumkin: "15 loaf 15 sapog", "loaf 20, sapog 10" — model nomi va dona soni \
(har biri alohida work elementi); "500.000 berdim", "500 000 oldim" — pul.
- Ishchi bilan pul (pay):
  * "Ishchilar" ro'yxatidagi odamga pul berilsa, undan pul olinsa yoki u bilan hisob aytilsa — pay, debt emas. \
Ishchi ro'yxatda bo'lmasa — debt.
  * Egasi tilidan: "Robiyaga 500 ming berdim", "Robiyaga avans berdim", "Robiya 500 ming oldi" — pay; \
"Robiya 200 ming qaytardi", "Robiyadan 200 ming oldim" — return.
  * Hisob holati (pul berilmagan) — balance: "Robiyaga 900 ming qarzman", "Robiyaning 900 ming haqqi bor", \
"Robiya bilan hisob 900 ming, men qarz" — worker_owes=false; "Robiya menga 200 ming qarz", "Robiya 200 ming \
ortiqcha olgan" — worker_owes=true; "Robiya bilan hisob yopildi", "hisob-kitob qildik, qarz yo'q" — amount=0.
  * Ish va pul bitta xabarda bo'lsa ("Robiya 15 ta loaf qildi, 500 ming berdim") — work va pays ikkalasini \
to'ldir, section=work. Faqat pul bo'lsa — section=pay.
- Kasb (job) — ishchi qanday ish qilgani yoki narx qaysi ish uchunligi. "Kasblar" ro'yxatidagi eng mos nomni \
yoz: kroy, kroyka, kesish, bichish — Kroychi; brsovka, brsovshi, brsovkachi, shilish (tikishga tayyorlab \
shilish) — Brsovkachi; tikish, tikuv — Tikuvchi; tortish, padosh (tag) yopishtirish — Tortuvchi; pardoz, \
sotuvga tayyorlash — Pardozchi. Ro'yxatda mosi bo'lmasa — eshitilgancha, bosh harf bilan. Ish turi \
aytilmasa — null (o'zingdan qo'shma).
- price: har bir model va kasb juftligi — alohida element ("A-12 ga kroy 1500, brsovka 2000" — ikkita). \
"2000dan", "2 mingdan", "2 ming so'm" — price. Kasb aytilmasa job=null (umumiy narx). \
work va price farqi: ishchi ismi yoki dona soni bo'lsa — work, faqat model, kasb va narx bo'lsa — price.
- material: har bir xom ashyo — alohida element. kind — "Xom ashyo turlari" ro'yxatidan mosi, bo'lmasa \
bosh harf bilan (Teri, Material, Podklad, Kley, Padosh, Ip, Furnitura, Upakovka). name — nomi yoki markasi \
("nomi Shirin gul", "Lider terisi" — "Lider"), "Xom ashyo nomlari" ro'yxatidagi yozilishiga keltir. \
"listi 300 ming", "kilosi 50 ming", "300 mingdan" (miqdor bilan) — price_per_unit; "10 millionlik", \
"10 millionga", "jami", "hammasi" — total. Narxni o'zing hisoblama, faqat aytilganini yoz. \
supplier — kimdan olingani, qo'shimchasiz ("Xayrullohdan" — Xayrulloh); "Ta'minotchilar" ro'yxatidagi \
odamdan olinsa — ro'yxatdagi to'liq yozilishi ("Xayrullohdan", "Uz charmdan" — Xayrulloh Uz charm). \
"Ta'minotchilar" ro'yxatidagi odamdan xom ashyo yoki mol olinsa — material, debt emas.
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
- Ism, model va kasb nomlarini "Ma'lum nomlar" ro'yxatidagi eng mos yozuvga keltir; mos keladigani \
bo'lmasa, eshitilgancha lotin yozuvida, bosh harf bilan yoz. O'zingdan ism yoki son to'qima.
- Ovozda ism biroz buzilgan bo'lsa ham ("Rabiya", "Робия", "Robiyaxon" — ro'yxatda Robiya) ro'yxatdagi \
yozilishini yoz. Ismni qo'shimchasiz va hurmat so'zisiz yoz: "Robiyaga", "Robiyadan", "Robiya opa" — Robiya.
- Sonlarni raqamga aylantir: "ellik" -> 50, "bir yarim million" -> 1500000, "200 ming" -> 200000, \
"500.000" yoki "500,000" -> 500000 (nuqta va vergul — minglik ajratgich).
- Valyuta: so'm -> UZS, dollar/$ -> USD, von/won/₩ -> KRW. Aytilmasa null.
- Sanani bugungi sanaga nisbatan hisobla: "kecha", "o'tgan juma", "5-oktabr". Aytilmasa null.
"""

_LABELS = {
    "workers": "Ishchilar",
    "models": "Modellar",
    "jobs": "Kasblar",
    "material_kinds": "Xom ashyo turlari",
    "material_names": "Xom ashyo nomlari",
    "suppliers": "Ta'minotchilar (xom ashyo kimdan olinadi)",
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


# Structured outputs javobni sxemaga majburan moslaydi. API sxemani rad etsa (masalan, «Schema is too complex
# for compilation»), so'rov oddiy JSON bilan takrorlanadi va server qayta ishga tushguncha shu usul ishlatiladi
_strict = True


def _schema_text() -> str:
    """Oddiy JSON so'rovi uchun sxema — ixcham, sarlavhalarsiz."""

    def strip(node):
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return json.dumps(strip(Extraction.model_json_schema()), ensure_ascii=False, separators=(",", ":"))


JSON_RULES = (
    "Javobni faqat bitta JSON obyekt qilib yoz — boshqa matn, izoh va ``` belgisisiz. JSON quyidagi sxemaga "
    "mos bo'lsin, hamma maydonlar bo'lsin (bo'sh ro'yxat — []):\n" + _schema_text()
)


def _empty(annotation) -> object:
    """Javobda tushib qolgan yoki null bo'lgan maydon uchun «aytilmagan» qiymati (sxemadagidek)."""
    args = get_args(annotation)
    if annotation is str:
        return ""
    if annotation in (int, float):
        return 0
    if annotation is bool:
        return False
    if type(None) in args:
        return None
    if get_origin(annotation) is list:
        return []
    if get_origin(annotation) is Literal and "" in args:
        return ""
    return None


def _complete(model: type[BaseModel], data: object) -> object:
    """Yetishmagan maydonlarni to'ldiradi — JSON sxemaga majburan mos bo'lmagan javob uchun."""
    if not isinstance(data, dict):
        return data
    for name, field in model.model_fields.items():
        empty = _empty(field.annotation)
        if data.get(name) is None:
            if name not in data or empty is not None:
                data[name] = empty
        elif data[name] == "" and empty is None:  # «aytilmagan» null o'rniga bo'sh satr (masalan, valyuta)
            data[name] = None
        elif get_origin(field.annotation) is list and isinstance(data[name], list):
            (item,) = get_args(field.annotation)
            if isinstance(item, type) and issubclass(item, BaseModel):
                data[name] = [_complete(item, x) for x in data[name]]
    return data


def parse_json(text: str) -> Extraction:
    """Oddiy matndagi JSON javob: ``` ichida yoki atrofida gap bo'lsa ham."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Javobda JSON topilmadi")
    return Extraction.model_validate(_complete(Extraction, json.loads(text[start : end + 1])))


JSON_ATTEMPTS = 2


async def _call_json(system: str, request: dict) -> Extraction:
    """Sxemasiz so'rov: JSON matndan o'qiladi. Buzuq kelsa — yana bir marta so'raladi."""
    for attempt in range(1, JSON_ATTEMPTS + 1):
        try:
            response = await client.messages.create(system=f"{system}\n\n{JSON_RULES}", **request)
        except anthropic.APIError as e:
            log.exception("Claude API xatosi")
            raise LlmError(str(e)) from e
        if response.stop_reason == "refusal":
            log.warning("Claude javob bermadi: stop_reason=%s", response.stop_reason)
            raise LlmError(f"stop_reason={response.stop_reason}")
        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            return parse_json(text)
        except ValueError as e:  # JSON buzuq yoki sxemaga mos emas (ValidationError ham ValueError)
            if attempt == JSON_ATTEMPTS:
                log.exception("Claude javobini o'qib bo'lmadi (stop_reason=%s)", response.stop_reason)
                raise LlmError(str(e)) from e
            log.warning("Claude JSON javobi buzuq, qayta so'ralmoqda: %s", e)
    raise AssertionError("unreachable")


async def _call(
    user_content: str | list[dict],
    extra_rules: str = "",
    *,
    effort: str = "low",
    max_tokens: int = 4000,
    timeout: float | None = None,
) -> Extraction:
    global _strict
    system = SYSTEM_PROMPT + (f"\n{extra_rules}" if extra_rules else "")
    request = {
        "model": settings.llm_model,
        "max_tokens": max_tokens,
        "output_config": {"effort": effort},
        "messages": [{"role": "user", "content": user_content}],
        "timeout": timeout or anthropic.not_given,
    }
    if not _strict:
        return await _call_json(system, request)
    try:
        response = await client.messages.parse(system=system, output_format=Extraction, **request)
    except anthropic.BadRequestError as e:
        log.warning("Claude sxemali so'rovni rad etdi, oddiy JSON bilan qayta urinilmoqda: %s", e)
        result = await _call_json(system, request)
        _strict = False
        return result
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
        f"Bu xabarni ishchi {worker_name} yozdi. Bo'lim faqat work yoki pay bo'lishi mumkin "
        f"(yoki tushunarsiz bo'lsa unknown). worker_name har doim \"{worker_name}\". "
        "Ishchi tilidan: \"500 ming oldim\", \"avans oldim\", \"oylikni oldim\" — pay; \"200 ming qaytardim\", "
        "\"200 ming berdim\" — return; \"boshlig'im menga 900 ming qarz\", \"egam menga 900 ming qarz\", \"900 ming haqqim bor\" — balance, "
        "worker_owes=false; \"men 200 ming qarzman\" — balance, worker_owes=true."
    )


def _client_rules(client_name: str) -> str:
    return (
        f"Bu xabarni korxonaning mijozi {client_name} yozdi — u korxonadan optom mol oladi va pul to'laydi. "
        f"Bo'lim faqat client bo'lishi mumkin (yoki tushunarsiz bo'lsa unknown). client_name har doim "
        f"\"{client_name}\". Mijoz tilidan: \"10 komplekt oldim\", \"Maksimadan 5 komplekt oldim, 140 mingdan\", "
        "\"1 millionlik mol oldim\" — sale (korxona mijozga mol berdi); \"2 million berdim\", \"2 million "
        "to'ladim\", \"pul tashladim\", \"pul o'tkazdim\" — payment; \"3 komplekt qaytardim\", \"molni "
        "qaytardim\" — return. Mol aytilmay faqat pul bilan \"oldim\" (\"500 ming oldim\") — mol emas, unknown: "
        "question'da \"Mol oldingizmi yoki pul berdingizmi?\" deb so'ra."
    )


def _side_rules(worker_name: str | None, client_name: str | None) -> str:
    if worker_name:
        return _worker_rules(worker_name)
    return _client_rules(client_name) if client_name else ""


async def extract(
    text: str,
    today: date,
    known: dict[str, list[str]],
    worker_name: str | None = None,
    info: str = "",
    client_name: str | None = None,
) -> Extraction:
    """worker_name berilsa — yozuvchi ishchi, bo'lim har doim "work" va ishchi o'zi. client_name — yozuvchi
    korxonaning mijozi (taklif havolasi orqali ulangan), bo'lim faqat "client"."""
    extra = _side_rules(worker_name, client_name)
    content = f"{_context_block(today, known, info)}\n\nXabar:\n«{text}»"
    return await _call(content, extra)


async def correct(
    draft: dict,
    correction: str,
    today: date,
    known: dict[str, list[str]],
    worker_name: str | None = None,
    info: str = "",
    client_name: str | None = None,
) -> Extraction:
    """Mavjud qoralamaga foydalanuvchi tuzatishini qo'llaydi."""
    extra = "Senga joriy qoralama va tuzatish beriladi. Tuzatishni qo'llab, TO'LIQ yangilangan natijani qaytar."
    if side := _side_rules(worker_name, client_name):
        extra += " " + side
    content = (
        f"{_context_block(today, known, info)}\n\n"
        f"Joriy qoralama:\n{json.dumps(draft, ensure_ascii=False)}\n\n"
        f"Foydalanuvchi tuzatishi:\n«{correction}»"
    )
    return await _call(content, extra)


NO_TEXT = "YOZUV_YOQ"

# Qog'ozga qo'lda yozilgan matn: rasmni to'g'ridan-to'g'ri tahlil qilishda ham, matnga ko'chirishda ham
HANDWRITING_RULES = """\
Qo'lyozma (qog'oz, daftar): avval yozuv kirillmi yoki lotinmi — butun so'zlarga qarab aniqla. Kirill yozma \
harflari lotinchaga o'xshaydi: т — m, п — n, и — u, д — g, з — 3, л — λ; с, а, о, е, к, р, х, у — o'sha \
shakldagi lotin harflari. Shuning uchun «20 ma canok» aslida kirillcha «20 та сапок» (20 ta sapog). \
Qiyshiq, xira yoki chiziqli qog'oz bo'lsa ham har bir qatorni o'qi; sonlarni (1 va 7, 4 va 9, 5 va 6) \
diqqat bilan ajrat. O'qib bo'lmagan joyni to'qima."""


IMAGE_RULES = f"""\
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
bilan ajrat. Korxona uchun xom ashyo (teri, kley, padosh...) xaridi cheki yoki daftari — section=material.
{HANDWRITING_RULES}
- Daftardagi "20 та сапок", "15 ta lofer" kabi qatorlar (son + ta/та/dona/шт + nom) — model nomi va dona soni, \
har bir qator alohida work elementi. Model nomini lotinga o'gir (сапок — sapog, лофер — lofer, лоропиано — \
loro piano) va "Modellar" ro'yxatidagi eng mos nomga keltir (lofer — Loaf yoki Lofer, qaysi biri ro'yxatda bo'lsa).
Ishchi bilan yozishma (SMS yoki Telegram chat skrinshoti): tepadagi suhbatdosh ismi — odatda ishchi, o'ng \
tomondagi (yuborilgan) xabarlarni foydalanuvchining o'zi yozgan. "15 loaf 15 sapog" — ish (work), \
"500.000 berdim" — pul (pays), "900.000 men qarz" — hisob holati (balance). Har bir xabarning sanasi \
(kun sarlavhasi: "среда, 7 октября", "7-oktabr") — date maydoniga, section=work.
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
    text = f"{_context_block(today, known, info)}\n\nRasmlar soni: {len(images)}."
    if caption:
        text += f"\nFoydalanuvchi izohi:\n«{caption}»"
    content = [*_image_blocks(images), {"type": "text", "text": text}]
    # Ko'p qatorli bank tarixi: aniq o'qish uchun chuqurroq o'ylaydi va uzunroq javob yozadi
    return await _call(content, extra, effort="medium", max_tokens=16000, timeout=180)


def _image_blocks(images: list[tuple[bytes, str]]) -> list[dict]:
    return [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(data).decode()},
        }
        for data, media_type in images
    ]


READ_PROMPT = f"""\
Senga rasm(lar) beriladi: qog'ozga yoki daftarga qo'lda yozilgan yozuv, chek, bank ilovasi skrinshoti yoki \
yozishma. Rasmdagi hamma yozuvni qanday yozilgan bo'lsa shunday, qatorma-qator ko'chirib yoz.
{HANDWRITING_RULES}
- Kirillcha yozuvni kirillcha, lotincha yozuvni lotincha ko'chir. Sonlarni, sanalarni va summalarni aniq yoz.
- O'qib bo'lmagan so'z o'rniga [?] qo'y, o'zingdan so'z yoki son qo'shma.
- Faqat ko'chirilgan matnni yoz: izoh, sarlavha, tarjima va ``` belgisisiz. Bir nechta rasm bo'lsa — \
ketma-ket, rasmlar orasida bo'sh qator.
- Rasmda yozuv bo'lmasa — faqat {NO_TEXT} deb yoz."""


async def read_images(images: list[tuple[bytes, str]]) -> str:
    """Rasmdagi yozuvni matnga ko'chiradi (qo'lyozma, kirill yoki lotin). Yozuv bo'lmasa — bo'sh satr.
    Rasmni to'g'ridan-to'g'ri tahlil qilib bo'lmasa, ko'chirilgan matn oddiy xabardek tahlil qilinadi."""
    try:
        response = await client.messages.create(
            model=settings.llm_model,
            max_tokens=8000,
            system=READ_PROMPT,
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": [*_image_blocks(images), {"type": "text", "text": "Ko'chir."}]}],
            timeout=120,
        )
    except anthropic.APIError as e:
        log.exception("Claude API xatosi (rasmni matnga ko'chirish)")
        raise LlmError(str(e)) from e
    text = "".join(block.text for block in response.content if block.type == "text").strip().strip("`").strip()
    if response.stop_reason == "refusal" or not text:
        log.warning("Claude rasmni ko'chirmadi: stop_reason=%s", response.stop_reason)
        raise LlmError(f"stop_reason={response.stop_reason}")
    return "" if text == NO_TEXT else text


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
- Foydalanuvchi halol moliyaga amal qiladi: foizli bank omonati, foizli kredit yoki foiz (ribo) keltiradigan \
narsani hech qachon tavsiya qilma. Jamg'armani o'stirish uchun halol investitsiyani tavsiya qil: kichik summa bilan \
ham biznesga sherik bo'lish mumkin bo'lgan tashkilotlar (foyda va zarar sheriklikda bo'linadi — mushoraka, \
mudoraba), oltin. Investitsiyadan oldin zaxira fond yig'ilsin.
- Aniq kompaniya, aksiya yoki kriptovalyuta nomini aytma. Tashkilot tanlashda tekshirishni eslat: shariat \
kengashi yoki fatvo, litsenziya, shaffof hisobot; "kafolatlangan foyda" va'dasi — firibgarlik belgisi.
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
