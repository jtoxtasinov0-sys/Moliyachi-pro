"""Mijozning o'zi: taklif havolasi (start=cli_…) orqali ulangan mijoz korxona bilan oldi-berdini botda ko'radi
va o'zi ham yozadi («10 komplekt oldim», «2 million berdim») — darhol hisobga kiradi, egasiga xabar boradi.

Mijoz users jadvalida emas (ta'minotchidek): bu yerdagi xabar handlerlari faqat botda ro'yxatdan o'tmagan
(ega yoki ishchi bo'lmagan), lekin mijoz sifatida ulangan odamga ishlaydi. Ega yoki ishchi ham kimningdir
mijozi bo'lsa — xabarlar unga ham keladi, hisob-kitobni xabardagi «🧾 Hisob-kitob» tugmasi bilan ko'radi."""
import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from bot import drafts, intake
from bot import keyboards as kbs
from bot.db import repo
from bot.db.models import Client, User
from bot.handlers.entries import notify_owner, notify_owner_linked, refuse_link
from bot.handlers.money import client_balance_line, client_side_balance, client_side_ledger, client_side_text, client_text
from bot.monitor import heard
from bot.services import stt

log = logging.getLogger(__name__)

router = Router(name="clients")

MAX_VOICE_SECONDS = 300
HEARD_PREVIEW = 300
MAX_LEDGERS = 5


class ClientGuest(Filter):
    """Botda ro'yxatdan o'tmagan, lekin mijoz sifatida ulangan odam. Handlerga links — uning ulanishlari
    (har biri boshqa korxonada) beriladi."""

    async def __call__(self, event: TelegramObject, session: AsyncSession, user: User | None = None) -> bool | dict:
        if (user is not None and user.is_active) or event.from_user is None:
            return False
        links = await repo.clients_by_tg(session, event.from_user.id)
        return {"links": links} if links else False


class ClientSG(StatesGroup):
    draft = State()


async def _owner_names(session: AsyncSession, links: list[Client]) -> list[tuple[int, str]]:
    """(mijoz id, korxona egasining ismi)."""
    out = []
    for c in links:
        owner = await session.get(User, c.owner_id)
        out.append((c.id, owner.name if owner else c.name))
    return out


def _help(owner_names: list[str]) -> str:
    who = ", ".join(f"<b>{escape(name)}</b>" for name in owner_names)
    return (
        f"🛍 Siz {who}ning mijozisiz.\n\n"
        "🧾 Oldi-berdingiz shu yerda ko'rinadi: korxona sizga mol bersa yoki to'lovingizni yozsa — xabar keladi.\n\n"
        "✍️ O'zingiz ham yozing (ovoz yoki matn):\n"
        "• <i>10 komplekt oldim</i> — olgan molingiz\n"
        "• <i>Maksimadan 5 komplekt oldim, 140 mingdan</i>\n"
        "• <i>2 million berdim</i> — to'lovingiz\n"
        "• <i>3 komplekt qaytardim</i>\n"
        "Yozganingiz darhol hisobga kiradi va korxonaga xabar boradi. Xato bo'lsa — «🗑 O'chirish».\n\n"
        "🧾 Hisob-kitob — hamma oldi-berdi va qarzingiz."
    )


# --- Ulanish: taklif havolasi ------------------------------------------------

@router.message(CommandStart(deep_link=True, magic=F.args.startswith("cli_")))
async def start_client(
    message: Message, command: CommandObject, state: FSMContext, session: AsyncSession, user: User | None
) -> None:
    """Mijozning taklif havolasi. Ishchi yoki ega ham bosishi mumkin — u holda menyusi o'zgarmaydi."""
    token = command.args.removeprefix("cli_")
    linked = await repo.link_client(session, token, message.from_user.id)
    if linked is None:
        await refuse_link(message, session, Client, token)
        return
    client, previous = linked
    owner = await session.get(User, client.owner_id)
    owner_name = owner.name if owner else "Korxona"
    registered = user is not None and user.is_active
    await state.clear()
    text = f"Xush kelibsiz, <b>{escape(client.name)}</b>! Siz <b>{escape(owner_name)}</b>ning mijozi sifatida ulandingiz."
    if registered:
        text += (
            "\n\n🧾 U siz bilan oldi-berdini yozganda shu yerga xabar keladi. Hisob-kitobni xabardagi "
            "«🧾 Hisob-kitob» tugmasi bilan ko'rasiz."
        )
        await message.answer(text, reply_markup=kbs.my_client_kb(client.id))
    else:
        links = await repo.clients_by_tg(session, message.from_user.id)
        names = [name for _, name in await _owner_names(session, links)]
        await message.answer(f"{text}\n\n{_help(names)}", reply_markup=kbs.client_menu())
        await message.answer(await client_side_ledger(session, client))
    await notify_owner_linked(
        message.bot, session, client, "cli", previous, message.from_user.id,
        f"🛍 {escape(client.name)} botga ulandi — endi oldi-berdingizni u ham ko'radi va o'zi ham yozishi "
        "mumkin («10 komplekt oldim», «2 million berdim»).",
    )


# --- Menyu: yordam va hisob-kitob -------------------------------------------

@router.message(CommandStart(magic=F.args.is_(None)), ClientGuest())
@router.message(Command("yordam", "help"), ClientGuest())
@router.message(F.text == kbs.BTN_HELP, ClientGuest())
async def client_help(message: Message, state: FSMContext, session: AsyncSession, links: list[Client]) -> None:
    await state.clear()
    names = [name for _, name in await _owner_names(session, links)]
    await message.answer(_help(names), reply_markup=kbs.client_menu())


@router.message(F.text == kbs.BTN_LEDGER, ClientGuest())
@router.message(Command("hisob"), ClientGuest())
async def client_ledger(message: Message, state: FSMContext, session: AsyncSession, links: list[Client]) -> None:
    await state.clear()
    for client in links[:MAX_LEDGERS]:
        await message.answer(await client_side_ledger(session, client), reply_markup=kbs.client_menu())


async def _own_link(call: CallbackQuery, session: AsyncSession, client_id: int) -> Client | None:
    """Tugmadagi ulanish shu odamnikimi (xabar boshqa akkauntga o'tib qolgan bo'lishi mumkin)."""
    client = await session.get(Client, client_id)
    if client is None or not client.is_active or client.tg_id != call.from_user.id:
        await call.answer("Bu korxona bilan ulanish topilmadi.", show_alert=True)
        return None
    return client


@router.callback_query(kbs.MyClientCb.filter(F.action == "ledger"))
async def on_ledger(call: CallbackQuery, callback_data: kbs.MyClientCb, session: AsyncSession) -> None:
    client = await _own_link(call, session, callback_data.client_id)
    if client is None:
        return
    await call.message.answer(await client_side_ledger(session, client))
    await call.answer()


@router.callback_query(kbs.MyClientCb.filter(F.action.in_({"del", "del_yes", "del_no"})))
async def on_delete(call: CallbackQuery, callback_data: kbs.MyClientCb, session: AsyncSession) -> None:
    """Mijoz o'zi yozgan yozuvni o'chiradi (egasi yozganini — faqat egasi)."""
    client = await _own_link(call, session, callback_data.client_id)
    if client is None:
        return
    entry = await repo.get_client_entry(session, client.owner_id, callback_data.entry_id)
    if entry is None or entry.client_id != client.id:
        await call.answer("Yozuv topilmadi yoki o'chirilgan.", show_alert=True)
        return
    if not entry.by_client:
        await call.answer("Bu yozuvni korxona kiritgan — faqat u o'chira oladi.", show_alert=True)
        return
    if callback_data.action == "del":
        await call.message.edit_reply_markup(reply_markup=kbs.my_client_confirm_kb(client.id, entry.id))
        await call.answer("Rostdan o'chirilsinmi?")
        return
    if callback_data.action == "del_no":
        await call.message.edit_reply_markup(reply_markup=kbs.my_client_kb(client.id, entry.id))
        await call.answer()
        return
    await repo.soft_delete(session, entry)
    balance = await repo.client_balance(session, client.owner_id, client.id)
    await call.message.edit_text(
        f"<s>{client_side_text(entry)}</s>\n\n🗑 O'chirildi\n\n{client_side_balance(balance)}",
        reply_markup=kbs.my_client_kb(client.id),
    )
    await call.answer("O'chirildi")
    await notify_owner(
        call.bot, session, client.owner_id,
        f"🗑 {escape(client.name)} o'zi yozgan yozuvni o'chirdi:\n\n<s>{client_text(entry)}</s>\n\n"
        + await client_balance_line(session, client.owner_id, client),
    )


# --- Mijoz yozadi: ovoz yoki matn ---------------------------------------------

async def _show_or_save(
    target: Message, bot: Bot, state: FSMContext, session: AsyncSession, links: list[Client], draft: dict
) -> None:
    """Hammasi bor bo'lsa — darhol hisobga yoziladi, aks holda yetishmagani so'raladi. target — tahrirlanadigan xabar."""
    shown = drafts.present_for_client(draft, await _owner_names(session, links))
    if shown is not None:
        await state.set_state(ClientSG.draft)
        await state.update_data(draft=draft)
        text, kb = shown
        await target.edit_text(text, reply_markup=kb)
        return
    saved = await intake.save_for_client(session, bot, links, draft)
    await state.clear()
    await target.edit_text(saved.header)
    for text, kb in saved.cards:
        await target.answer(text, reply_markup=kb)


async def _process(
    status: Message, bot: Bot, text: str, state: FSMContext, session: AsyncSession, links: list[Client]
) -> None:
    draft = None
    if await state.get_state() == ClientSG.draft.state:
        draft = (await state.get_data()).get("draft")
    try:
        new_draft = await intake.analyze_for_client(session, links, text, draft)
        await _show_or_save(status, bot, state, session, links, new_draft)
    except intake.IntakeError as e:
        if e.drop:
            await state.clear()
        await status.edit_text(e.message)


@router.message(F.voice | F.audio, ClientGuest())
async def on_voice(
    message: Message, bot: Bot, state: FSMContext, session: AsyncSession, links: list[Client]
) -> None:
    media = message.voice or message.audio
    if media.duration and media.duration > MAX_VOICE_SECONDS:
        await message.answer("Ovozli xabar juda uzun (5 daqiqagacha bo'lsin).")
        return
    status = await message.answer("🎧 Eshitilmoqda…")
    try:
        buf = await bot.download(media)
        text = await stt.transcribe(buf.read())
    except stt.SttError as e:
        await status.edit_text(e.notice or "⚠️ Ovozni matnga aylantirib bo'lmadi. Qayta yuboring yoki matn bilan yozing.")
        return
    if not text:
        await status.edit_text("🤷 Ovozda gap eshitilmadi. Qayta yuboring.")
        return
    heard(message, text)
    shown = text if len(text) <= HEARD_PREVIEW else text[:HEARD_PREVIEW] + "…"
    await status.edit_text(f"🎧 «{escape(shown)}»\n\n⏳ Tahlil qilinmoqda…")
    await _process(status, bot, text, state, session, links)


@router.message(F.text & ~F.text.startswith("/"), ClientGuest())
async def on_text(message: Message, bot: Bot, state: FSMContext, session: AsyncSession, links: list[Client]) -> None:
    status = await message.answer("⏳ Tahlil qilinmoqda…")
    await _process(status, bot, message.text, state, session, links)


@router.message(~F.text.startswith("/"), ClientGuest())
async def on_other(message: Message) -> None:
    await message.answer(
        "Ovozli yoki matnli xabar yuboring: <i>«10 komplekt oldim»</i>, <i>«2 million berdim»</i>.",
        reply_markup=kbs.client_menu(),
    )


# --- Qoralama tugmalari: qaysi korxona, mol yoki pul, bekor --------------------

@router.callback_query(kbs.MyClientCb.filter(F.action.in_({"pick", "kind", "cancel"})), ClientGuest())
async def on_draft_button(
    call: CallbackQuery,
    callback_data: kbs.MyClientCb,
    bot: Bot,
    state: FSMContext,
    session: AsyncSession,
    links: list[Client],
) -> None:
    if callback_data.action == "cancel":
        await state.clear()
        await call.message.edit_text("❌ Bekor qilindi.")
        await call.answer()
        return
    draft = (await state.get_data()).get("draft")
    if draft is None or await state.get_state() != ClientSG.draft.state or drafts.is_stale(draft):
        await state.clear()
        await call.answer("Bu qoralama eskirgan. Qaytadan yozing.", show_alert=True)
        await call.message.edit_reply_markup(reply_markup=None)
        return
    if callback_data.action == "pick":
        link = next((c for c in links if c.id == callback_data.client_id), None)
        if link is None:
            await call.answer("Bu korxona bilan ulanish topilmadi.", show_alert=True)
            return
        await intake.pick_client_link(session, draft, link)
    elif not intake.set_client_side_kind(draft, callback_data.value):
        await call.answer()
        return
    try:
        await _show_or_save(call.message, bot, state, session, links, draft)
    except intake.IntakeError as e:
        if e.drop:
            await state.clear()
            await call.message.edit_text(e.message)
        else:
            await call.answer(e.message, show_alert=True)
            return
    await call.answer()
