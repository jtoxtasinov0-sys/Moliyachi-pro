import logging
from html import escape
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, LinkPreviewOptions, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kbs
from bot.config import settings
from bot.db import repo
from bot.db.models import User
from bot.handlers.entries import notify_owner
from bot.middlewares import Guest, Registered
from bot.web.auth import WEB_TOKEN_DAYS, make_web_token

log = logging.getLogger(__name__)

router = Router(name="common")

GUIDE_DIR = Path(__file__).resolve().parent.parent / "assets"
GUIDE_VIDEO = GUIDE_DIR / "qollanma.mp4"
GUIDE_THUMB = GUIDE_DIR / "qollanma_thumb.jpg"
# Telegram file_id: bir marta yuklangach, keyingi safar fayl qayta yuborilmaydi
_guide_file_id: str | None = None

OWNER_HELP = """\
<b>Qanday ishlatiladi</b>

🎙 Ovozli yoki matnli xabar yuboring, masalan:
• <i>Alisher A-12 modeldan 50 ta topshirdi</i>
• <i>Kecha Vali B-5 dan 30 ta, C-7 dan 20 ta</i>
• <i>Vali A-12 dan 40 ta brsovka qildi</i> — kasbi bilan
• <i>Robiyaga 500 ming berdim</i> — ishchiga pul: uning qoldig'idan ayriladi
• <i>Robiyaga 900 ming qarzman</i>, <i>Robiya bilan hisob yopildi</i> — ishchi bilan hisob holati
• <i>A-12 modelga brsovka 2000dan, kroy 1500</i> — model narxi kasb bo'yicha
• <i>10 millionlik teri oldim, nomi Shirin gul</i> — xom ashyo
• <i>Bozorga 50 ming won, benzinga 40 ming</i> — shaxsiy xarajat
• <i>Abbosdan 100 ming won oldim</i> — qarz (kimligi so'ralmaydi)
• <i>Tohirga 10 komplekt maksima berdim, 140 mingdan</i> — mijozga mol
• <i>Tohir 2 million to'ladi</i> — mijoz to'lovi

Bot yozuvni tushunib, «To'g'rimi?» deb so'raydi. Xato bo'lsa — shunchaki
tuzatishni ayting: <i>«soni 45 ta»</i>, <i>«kecha edi»</i>.

🖼 Bank ilovasidan skrinshot, chek yoki daftar rasmini yuboring — bot o'zi o'qib
(koreyscha, lotin va kirill yozuvi ham), xarajat va daromadlarni kategoriyalarga ajratib
hisobga qo'shadi. Bir nechta skrinshotni birga (albom) yuborsangiz ham bo'ladi, avval
kiritilganlari qayta qo'shilmaydi. Ilovada — 📷 tugma.

Sizning ma'lumotlaringizni faqat siz ko'rasiz.

<b>Menyu</b>
📊 Hisobot — ishchilar: kunlik / haftalik / oylik
🕘 Oxirgi yozuvlar — tahrirlash va o'chirish
💸 Xarajatlar — kategoriyalar bo'yicha hisobot
🛍 Mijozlar — kim qancha mol oldi va qancha qarzi bor
🤝 Qarzlar — kim kimga qancha qarz
💡 Maslahat — topgan pulingizdan xarajatga va jamg'armaga qancha ajratish,
     kunlik limit, moliyaviy saboqlar va shaxsiy maslahat
⏳ Tasdiq kutayotganlar — ishchilar kiritgan yozuvlar
👷 Ishchilar — qo'shish, taklif havolasi, kasbi (kroychi, brsovkachi, tikuvchi,
     tortuvchi, pardozchi yoki o'zingiz qo'shgan kasb), 🧾 hisob-kitobi: ishlari,
     olgan puli va qoldig'i
🧵 Modellar — qo'shish, narx: umumiy va har bir kasb uchun
🧱 Xom ashyo — teri, material, kley, padosh xaridlari: turlari bo'yicha qancha olindi
⚙️ Sozlamalar — ish va pul yozuvlarini tasdiqlash, rasmdan saqlash, valyutalar, kundalik eslatma vaqti

🔔 Har kuni kechqurun (sukut bo'yicha 21:00) bot «bugungi ishlatgan yoki topgan pulingizni
kiriting» deb eslatadi.
"""

WORKER_HELP = """\
<b>Qanday ishlatiladi</b>

🎙 Topshirgan ishingizni ovoz yoki matn bilan yuboring:
• <i>A-12 modeldan 50 ta topshirdim</i>
• <i>Kecha B-5 dan 30 ta</i>
• <i>A-12 dan 40 ta brsovka qildim</i> — boshqa ish qilgan bo'lsangiz, ish turini ayting
• <i>500 ming oldim</i> — pul olsangiz: hisobingizdan ayriladi
• <i>15 loaf 15 sapog, 500 ming oldim</i> — ish va pul birga
🖼 Daftaringiz rasmini yuborsangiz ham bo'ladi.

Bot «To'g'rimi?» deb so'raydi. Xato bo'lsa — tuzatishni ayting.

📊 Hisobot — o'z natijangiz va ish haqingiz
🕘 Oxirgi yozuvlar — tahrirlash va o'chirish
🧾 Hisob-kitob — ishlaringiz, olgan pulingiz va qolgan haqqingiz
"""


@router.message(CommandStart(deep_link=True), Guest())
async def start_with_invite(message: Message, command: CommandObject, session: AsyncSession) -> None:
    args = command.args or ""
    if not args.startswith("inv_"):
        await message.answer("Havola noto'g'ri.")
        return
    user = await repo.link_invite(session, args.removeprefix("inv_"), message.from_user.id)
    if user is None:
        await message.answer("Bu havola eskirgan yoki allaqachon ishlatilgan. Egasidan yangisini so'rang.")
        return
    await message.answer(
        f"Xush kelibsiz, <b>{escape(user.name)}</b>! Siz ishchi sifatida ulandingiz.\n\n{WORKER_HELP}",
        reply_markup=kbs.main_menu(user),
    )
    await notify_owner(message.bot, session, user.owner_id, f"👷 {escape(user.name)} botga ulandi.")


async def send_guide(message: Message, session: AsyncSession, user: User) -> bool:
    """Egaga qo'llanma videosi. Yuborib bo'lmasa — False (o'rniga matn yuboriladi)."""
    global _guide_file_id
    file_id = _guide_file_id or await repo.get_setting(session, user.id, "guide_video_id") or None
    caption = (
        f"Assalomu alaykum, <b>{escape(user.name)}</b>! 👋\n\n"
        "🎬 <b>Moliyachi qo'llanmasi</b> — 2 daqiqada: modellar, ishchilar, ishni kiritish, "
        "tasdiqlash, ish haqi hisobi va hisobot.\n\n"
        "Boshlash uchun 🎙 ovozli xabar yuboring. Batafsil yozma qo'llanma — «❓ Yordam»."
    )
    attempts = ([file_id] if file_id else []) + ([None] if GUIDE_VIDEO.exists() else [])
    for attempt in attempts:
        if attempt is None:
            await message.bot.send_chat_action(message.chat.id, "upload_video")
        video = attempt or FSInputFile(GUIDE_VIDEO, filename="moliyachi-qollanma.mp4")
        try:
            sent = await message.answer_video(
                video,
                caption=caption,
                width=720,
                height=1280,
                duration=133,
                supports_streaming=True,
                thumbnail=FSInputFile(GUIDE_THUMB) if attempt is None and GUIDE_THUMB.exists() else None,
                reply_markup=kbs.main_menu(user),
            )
        except TelegramAPIError:
            # Eskirgan file_id bo'lsa — faylni qayta yuklab ko'ramiz
            log.warning("Qo'llanma videosi yuborilmadi (file_id=%s)", bool(attempt), exc_info=True)
            continue
        if sent.video and sent.video.file_id != file_id:
            _guide_file_id = sent.video.file_id
            await repo.set_setting(session, user.id, "guide_video_id", _guide_file_id)
        return True
    return False


@router.message(CommandStart(), Registered())
async def start(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    if user.is_owner and await send_guide(message, session, user):
        return
    help_text = OWNER_HELP if user.is_owner else WORKER_HELP
    await message.answer(f"Assalomu alaykum, <b>{escape(user.name)}</b>!\n\n{help_text}", reply_markup=kbs.main_menu(user))


@router.message(Command("yordam", "help"), Registered())
@router.message(F.text == kbs.BTN_HELP, Registered())
async def help_cmd(message: Message, state: FSMContext, user: User) -> None:
    await state.clear()
    await message.answer(OWNER_HELP if user.is_owner else WORKER_HELP, reply_markup=kbs.main_menu(user))


@router.message(Command("web", "brauzer"), Registered())
async def web_link(message: Message, user: User) -> None:
    if not settings.base_url or not user.tg_id:
        await message.answer("Ilova manzili sozlanmagan (PUBLIC_URL).")
        return
    url = f"{settings.base_url}/app/?key={make_web_token(user.tg_id, settings.bot_token)}"
    await message.answer(
        "🌐 <b>Ilovani brauzerda ochish</b>\n\n"
        f"{escape(url)}\n\n"
        "Havolani kompyuter yoki telefon brauzerida oching — ilova sizning kabinetingiz bilan ochiladi. "
        "Ilovani ochib tursangiz, havola o'zi yangilanib turadi. Faqat "
        f"{WEB_TOKEN_DAYS} kundan ko'p ochmasangiz — /web bilan yangisini oling.\n\n"
        "⚠️ Bu shaxsiy havola — uni hech kimga bermang.",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


@router.message(Command("id"))
async def my_id(message: Message) -> None:
    await message.answer(f"Sizning Telegram ID: <code>{message.from_user.id}</code>")


@router.message(Guest())
async def guest(message: Message) -> None:
    await message.answer(
        "Siz hali ro'yxatda yo'qsiz. Korxona egasidan taklif havolasini so'rang.\n"
        f"Sizning ID: <code>{message.from_user.id}</code>"
    )
