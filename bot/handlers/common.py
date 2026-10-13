from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import LinkPreviewOptions, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kbs
from bot.config import settings
from bot.db import repo
from bot.db.models import User
from bot.handlers.entries import notify_owner
from bot.middlewares import Guest, Registered
from bot.web.auth import WEB_TOKEN_DAYS, make_web_token

router = Router(name="common")

OWNER_HELP = """\
<b>Qanday ishlatiladi</b>

🎙 Ovozli yoki matnli xabar yuboring, masalan:
• <i>Alisher A-12 modeldan 50 ta topshirdi</i>
• <i>Kecha Vali B-5 dan 30 ta, C-7 dan 20 ta</i>
• <i>Bozorga 50 ming won, benzinga 40 ming</i> — shaxsiy xarajat
• <i>Abbosdan 100 ming won oldim</i> — qarz (kimligi so'ralmaydi)
• <i>Tohirga 10 komplekt maksima berdim, 140 mingdan</i> — mijozga mol
• <i>Tohir 2 million to'ladi</i> — mijoz to'lovi

Bot yozuvni tushunib, «To'g'rimi?» deb so'raydi. Xato bo'lsa — shunchaki
tuzatishni ayting: <i>«soni 45 ta»</i>, <i>«kecha edi»</i>.

🖼 Bank ilovasidan skrinshot, chek yoki daftar rasmini yuboring — bot o'zi o'qib,
xarajat va daromadlarni kategoriyalarga ajratib hisobga qo'shadi. Bir nechta skrinshotni
birga (albom) yuborsangiz ham bo'ladi, avval kiritilganlari qayta qo'shilmaydi.

Sizning ma'lumotlaringizni faqat siz ko'rasiz.

<b>Menyu</b>
📊 Hisobot — ishchilar: kunlik / haftalik / oylik
🕘 Oxirgi yozuvlar — tahrirlash va o'chirish
💸 Xarajatlar — kategoriyalar bo'yicha hisobot
🛍 Mijozlar — kim qancha mol oldi va qancha qarzi bor
🤝 Qarzlar — kim kimga qancha qarz
⏳ Tasdiq kutayotganlar — ishchilar kiritgan yozuvlar
👷 Ishchilar — qo'shish, taklif havolasi
🧵 Modellar — qo'shish, narx belgilash
⚙️ Sozlamalar — tasdiqlash tartibi, rasmdan saqlash, valyutalar
"""

WORKER_HELP = """\
<b>Qanday ishlatiladi</b>

🎙 Topshirgan ishingizni ovoz yoki matn bilan yuboring:
• <i>A-12 modeldan 50 ta topshirdim</i>
• <i>Kecha B-5 dan 30 ta</i>
🖼 Daftaringiz rasmini yuborsangiz ham bo'ladi.

Bot «To'g'rimi?» deb so'raydi. Xato bo'lsa — tuzatishni ayting.

📊 Hisobot — o'z natijangiz va ish haqingiz
🕘 Oxirgi yozuvlar — tahrirlash va o'chirish
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


@router.message(CommandStart(), Registered())
async def start(message: Message, state: FSMContext, user: User) -> None:
    await state.clear()
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
