"""Mini App uchun JSON API. Har bir so'rov Telegram initData bilan tasdiqlanadi."""
import asyncio
import logging
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession

from bot import advisor, drafts, intake, pdf_report
from bot import jobs as jobs_mod
from bot import keyboards as kbs
from bot.config import settings
from bot.db import repo
from bot.db.base import session_factory
from bot.db.models import (
    Client,
    ClientEntry,
    DebtEntry,
    EntryStatus,
    ItemModel,
    Job,
    ManagerRequest,
    MaterialEntry,
    ModelPrice,
    PayKind,
    PersonalEntry,
    PersonalKind,
    RequestStatus,
    Supplier,
    User,
    WorkEntry,
    WorkerPayment,
)
from bot.monitor import report_error, track_api
from bot.services import llm, stt
from bot.services.utils import fmt_money, today
from bot.web.auth import (
    WEB_TOKEN_DAYS,
    make_file_token,
    make_web_token,
    validate_file_token,
    validate_init_data,
    validate_web_token,
    web_token_expiry,
)

log = logging.getLogger(__name__)

routes = web.RouteTableDef()

# Model aytilmagan ish (kroy, brsovka) hisobotda shu nom bilan
NO_MODEL = "Modelsiz"

Handler = Callable[[web.Request, AsyncSession, User], Awaitable[web.StreamResponse]]


class ApiError(web.HTTPException):
    def __init__(self, status: int, message: str):
        self.status_code = status
        self.message = message
        super().__init__(text=web.json_response({"error": message}).text, content_type="application/json")


def _forbidden() -> ApiError:
    return ApiError(403, "Ruxsat yo'q")


# Ish boshqaruvchi uchun (endpoint manager=...): VIEW — egasidek ko'radi (funksiya javobdan ishchilarning puli va
# egasining pullarini o'zi olib tashlaydi); tavsif funksiyasi (Describe) — o'zgartirish darhol bajarilmaydi, egasining
# tasdig'iga boradi, tasdiqlansa shu funksiya egasi nomidan bajariladi (run_op). Qolganlari boshqaruvchiga yopiq.
VIEW = "view"
# (sessiya, boshqaruvchi, match_info, so'rov tanasi) -> egasiga ko'rsatiladigan tavsif; noto'g'ri bo'lsa — ApiError.
# Tanani to'ldirishi mumkin: toggle — qaysi holatga (tasdiqlanguncha egasi o'zi almashtirgan bo'lsa, teskarisi bo'lmasin)
Describe = Callable[[AsyncSession, User, dict, dict], Awaitable[str]]
_OPS: dict[str, tuple[Handler, Describe]] = {}


def endpoint(owner_only: bool = False, manager: str | Describe | None = None):
    """Sessiya ochadi, foydalanuvchini aniqlaydi, oxirida commit qiladi."""

    def wrap(fn: Handler):
        ask = callable(manager)
        if ask:
            _OPS[fn.__name__] = (fn, manager)

        async def inner(request: web.Request) -> web.StreamResponse:
            async with session_factory() as session:
                user = await _auth(request, session)
                queue = ask and user.is_manager
                if owner_only and not user.is_owner and not queue and not (manager == VIEW and user.is_manager):
                    raise _forbidden()
                try:
                    if queue:
                        response = await _ask_owner(request, session, user, fn.__name__)
                    else:
                        response = await fn(request, session, user)
                except web.HTTPException:
                    raise
                except Exception as e:
                    report_error(user.tg_id, e, f"Ilovada ({request.method} {request.path}): ")
                    raise
                await session.commit()
                await track_api(request, user, response)
                if "new_web_token" in request:
                    response.headers["X-Web-Token-New"] = request["new_web_token"]
                return response

        return inner

    return wrap


async def _auth(request: web.Request, session: AsyncSession) -> User:
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    tg_user = validate_init_data(init_data, settings.bot_token) if init_data else None
    if tg_user is None:
        # Brauzerda: botning /web buyrug'i bergan shaxsiy kalit
        web_token = request.headers.get("X-Web-Token", "")
        tg_id = validate_web_token(web_token, settings.bot_token) if web_token else None
        if tg_id is not None:
            tg_user = {"id": tg_id}
            # Ilova ishlatib turilsa kalit o'zi uzayadi (kuniga ko'pi bilan bir marta)
            if web_token_expiry(web_token) - time.time() < (WEB_TOKEN_DAYS - 1) * 24 * 3600:
                request["new_web_token"] = make_web_token(tg_id, settings.bot_token)
    if tg_user is None and settings.webapp_dev:
        tg_user = {"id": settings.owner_ids[0], "first_name": "Egasi"}
    if tg_user is None:
        raise ApiError(401, "Ilovani Telegram ichida oching yoki brauzer uchun botga /web deb yozing")

    tg_id = int(tg_user["id"])
    if tg_id in settings.owner_ids:
        name = " ".join(filter(None, [tg_user.get("first_name"), tg_user.get("last_name")])) or "Egasi"
        return await repo.ensure_owner(session, tg_id, name)
    user = await repo.get_user_by_tg(session, tg_id)
    if user is None or not user.is_active:
        raise ApiError(403, "Siz ro'yxatda yo'qsiz. Egasidan taklif havolasini so'rang.")
    return user


async def _json(request: web.Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        raise ApiError(400, "Noto'g'ri so'rov")
    if not isinstance(data, dict):
        raise ApiError(400, "Noto'g'ri so'rov")
    return data


_background: set[asyncio.Task] = set()


def _notify(request: web.Request, chat_id: int, text: str, markup: ReplyKeyboardMarkup | None = None) -> None:
    """Telegram xabarini fonda yuboradi — ilova javobni kutib qolmasin."""

    async def send() -> None:
        try:
            await request.app["bot"].send_message(chat_id, text, reply_markup=markup)
        except Exception:
            log.warning("Xabar yuborilmadi (chat %s)", chat_id, exc_info=True)

    task = asyncio.create_task(send())
    _background.add(task)
    task.add_done_callback(_background.discard)


def _num(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _iso(dt: datetime | None) -> str | None:
    """Vaqt belgisi — UTC bilan (SQLite vaqt mintaqasini saqlamaydi, u yerda hammasi UTC)."""
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


# --- Ish boshqaruvchi: o'zgartirish — egasining tasdig'iga --------------------------

async def _ask_owner(request: web.Request, session: AsyncSession, manager: User, op: str) -> web.Response:
    """Javob — 202 queued: ilova oynani yopadi. Boshliq tasdig'i o'chiq bo'lsa so'rov darhol bajarilgan (status
    done) — ilova ko'rinishni yangilaydi."""
    from bot import approvals  # aylanma importdan qochish uchun

    body = await _json(request) if request.can_read_body else {}
    req = await approvals.ask(session, request.app["bot"], manager, op, dict(request.match_info), body)
    return web.json_response({"queued": True, "request": _request_json(req, manager)}, status=202)


async def describe_op(session: AsyncSession, manager: User, op: str, match: dict, body: dict) -> str:
    if op not in _OPS:
        raise _forbidden()
    return await _OPS[op][1](session, manager, match, body)


class _Replay:
    """Tasdiqlangan so'rov — ilovadagi so'rov o'rnida: amal egasi nomidan qayta bajariladi."""

    def __init__(self, bot: Bot, match: dict, body: dict | None):
        self.app = {"bot": bot}
        self.match_info = match
        self.query: dict = {}
        self.can_read_body = body is not None
        self._body = body

    async def json(self) -> dict | None:
        return self._body


async def run_op(session: AsyncSession, bot: Bot, owner: User, payload: dict) -> None:
    """Boshqaruvchi so'ragan amal — egasi tasdiqlagach, egasi nomidan. Bajarib bo'lmasa — ApiError."""
    if payload.get("op") not in _OPS:
        raise ApiError(400, "Bu amal endi yo'q")
    fn, _ = _OPS[payload["op"]]
    await fn(_Replay(bot, payload.get("match") or {}, payload.get("body")), session, owner)


def _int_id(match: dict) -> int:
    try:
        return int(match["id"])
    except (KeyError, TypeError, ValueError):
        raise ApiError(400, "Noto'g'ri so'rov")


def _changes(title: str, changes: list[str]) -> str:
    """So'rov tavsifi: nima o'zgaradi. O'zgarish bo'lmasa — so'rov yuborilmaydi."""
    if not changes:
        raise ApiError(400, "O'zgarish yo'q")
    return title + "\n" + "\n".join(f"• {c}" for c in changes)


def _price_text(price: tuple[Decimal, str] | None) -> str:
    return fmt_money(price[0], price[1]) if price else "—"


def _price_change(label: str, old: tuple[Decimal, str] | None, new: tuple[Decimal, str] | None) -> list[str]:
    """«A-12: 1 500 so'm → 2 000 so'm» — narx o'zgarsa (bo'lmasa bo'sh)."""
    if old == new:
        return []
    return [f"{label}: {_price_text(old)} → <b>{_price_text(new)}</b>"]


def _money(totals: dict[str, Decimal]) -> dict[str, float]:
    return {c: float(v) for c, v in totals.items()}


# --- Davrlar ----------------------------------------------------------------

PERIODS = ("day", "week", "month", "year")


def _shift_month(d: date, months: int) -> date:
    m = d.month - 1 + months
    return date(d.year + m // 12, m % 12 + 1, 1)


def _period_bounds(period: str, t: date) -> dict:
    """Joriy davr, taqqoslash uchun oldingi davr (shu kungacha) va grafik oralig'i."""
    if period == "day":
        start = end = t
        prev_start = prev_end = t - timedelta(days=1)
        chart = (t - timedelta(days=6), t, "day")
    elif period == "week":
        start = t - timedelta(days=t.weekday())
        end = start + timedelta(days=6)
        prev_start = start - timedelta(days=7)
        prev_end = prev_start + (t - start)
        chart = (start, end, "day")
    elif period == "month":
        start = t.replace(day=1)
        end = _shift_month(start, 1) - timedelta(days=1)
        prev_start = _shift_month(start, -1)
        prev_end = min(prev_start + (t - start), start - timedelta(days=1))
        chart = (start, end, "day")
    else:
        start = date(t.year, 1, 1)
        end = date(t.year, 12, 31)
        prev_start = date(t.year - 1, 1, 1)
        try:
            prev_end = t.replace(year=t.year - 1)
        except ValueError:  # 29-fevral
            prev_end = date(t.year - 1, 2, 28)
        chart = (start, end, "month")
    return {"start": start, "end": end, "prev": (prev_start, prev_end), "chart": chart}


def _summarize(rows: list[repo.WorkStatRow]) -> tuple[int, dict[str, Decimal]]:
    qty = 0
    pay: dict[str, Decimal] = defaultdict(Decimal)
    for r in rows:
        qty += r.quantity
        if r.pay is not None and r.currency:
            pay[r.currency] += r.pay
    return qty, dict(pay)


def _series(rows: list[repo.WorkStatRow], start: date, end: date, bucket: str) -> list[dict]:
    def key(d: date) -> str:
        return d.strftime("%Y-%m") if bucket == "month" else d.isoformat()

    points: dict[str, dict] = {}
    d = start
    while d <= end:
        k = key(d)
        if k not in points:
            points[k] = {"key": k, "qty": 0, "pay": defaultdict(float)}
        d = _shift_month(d, 1) if bucket == "month" else d + timedelta(days=1)
    for r in rows:
        p = points.get(key(r.work_date))
        if p is None:
            continue
        p["qty"] += r.quantity
        if r.pay is not None and r.currency:
            p["pay"][r.currency] += float(r.pay)
    return [{"key": p["key"], "qty": p["qty"], "pay": dict(p["pay"])} for p in points.values()]


def _group(rows: list[repo.WorkStatRow], attr: str) -> list[dict]:
    groups: dict = {}
    for r in rows:
        k = getattr(r, attr)
        g = groups.setdefault(k, {"qty": 0, "pay": defaultdict(Decimal), "rows": []})
        g["qty"] += r.quantity
        if r.pay is not None and r.currency:
            g["pay"][r.currency] += r.pay
        g["rows"].append(r)
    out = []
    for k, g in groups.items():
        item = {"qty": g["qty"], "pay": _money(g["pay"])}
        if attr == "worker_id":
            item.update(id=k, name=g["rows"][0].worker_name)
        elif attr == "model_name" and k is None:
            item.update(name=NO_MODEL)  # kroy, brsovka — model aytilmagan ish
        else:
            item.update(name=k)
        out.append(item)
    out.sort(key=lambda x: x["qty"], reverse=True)
    return out


@routes.get("/api/dashboard")
@endpoint()
async def dashboard(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    period = request.query.get("period", "month")
    if period not in PERIODS:
        raise ApiError(400, "Noto'g'ri davr")
    t = today()
    b = _period_bounds(period, t)
    worker_id = None if user.manages else user.id
    acc = user.account_id

    chart_start, chart_end, bucket = b["chart"]
    lo = min(b["start"], b["prev"][0], chart_start)
    hi = max(b["end"], chart_end)
    all_rows = await repo.work_stat_rows(session, acc, lo, hi, worker_id)

    cur = [r for r in all_rows if b["start"] <= r.work_date <= b["end"]]
    prev = [r for r in all_rows if b["prev"][0] <= r.work_date <= b["prev"][1]]
    qty, pay = _summarize(cur)
    prev_qty, prev_pay = _summarize(prev)

    data = {
        "period": period,
        "start": b["start"].isoformat(),
        "end": b["end"].isoformat(),
        "today": t.isoformat(),
        "totals": {"qty": qty, "pay": _money(pay), "days": len({r.work_date for r in cur})},
        "prev": {"qty": prev_qty, "pay": _money(prev_pay)},
        "series": {
            "bucket": bucket,
            "points": _series([r for r in all_rows if chart_start <= r.work_date <= chart_end], chart_start, chart_end, bucket),
        },
        "by_model": _group(cur, "model_name"),
        "pending": await repo.count_pending(session, acc, worker_id),
    }
    if user.manages:
        data["by_worker"] = _group(cur, "worker_id")
        data["by_job"] = _group(cur, "job_name")
        data["has_team"] = await repo.has_work(session, acc)
        # Ish boshqaruvchining so'rovlari: egasiga — tasdiq kutayotgani, boshqaruvchiga — o'ziniki
        data["requests"] = await repo.count_requests(session, acc, None if user.is_owner else user.id)
    if user.is_owner:
        data["pending_pay"] = await repo.count_pending_payments(session, acc)
    return web.json_response(data)


# --- PDF hisobot ------------------------------------------------------------
# Har kim o'z hisobotini fayl qilib telefoniga saqlab oladi: ilova yopilsa ham fayl qoladi.

def _pdf_period(value) -> str:
    period = str(value or "month")
    if period not in pdf_report.PERIODS:
        raise ApiError(400, "Noto'g'ri davr")
    return period


@routes.post("/api/report/pdf")
@endpoint()
async def report_pdf(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """action=send — bot faylni Telegram chatga yuboradi; aks holda — yuklab olish havolasi."""
    data = await _json(request)
    period = _pdf_period(data.get("period"))
    if data.get("action") == "send":
        if not user.tg_id:
            raise ApiError(400, "Telegram akkaunt ulanmagan")
        pdf, name = await pdf_report.build(session, user, period)
        try:
            await request.app["bot"].send_document(
                user.tg_id, BufferedInputFile(pdf, filename=name), caption=pdf_report.caption(period)
            )
        except TelegramAPIError:
            log.warning("PDF yuborilmadi (chat %s)", user.tg_id, exc_info=True)
            raise ApiError(400, "Bot sizga fayl yubora olmadi — botni ochib /start bosing")
        return web.json_response({"ok": True})
    base = settings.base_url or f"{request.scheme}://{request.host}"
    token = make_file_token(user.id, period, settings.bot_token)
    return web.json_response(
        {"url": f"{base}/api/report/file?t={token}", "file_name": pdf_report.file_name(user, period)}
    )


@routes.get("/api/report/file")
async def report_file(request: web.Request) -> web.Response:
    """PDF faylning o'zi. Kirish — /api/report/pdf bergan qisqa muddatli imzoli havola orqali."""
    found = validate_file_token(request.query.get("t", ""), settings.bot_token)
    if found is None or found[1] not in pdf_report.PERIODS:
        raise ApiError(403, "Havola eskirgan — hisobotni ilovadan qayta yuklab oling")
    user_id, period = found
    async with session_factory() as session:
        user = await session.get(User, user_id)
        if user is None or not user.is_active:
            raise _forbidden()
        try:
            pdf, name = await pdf_report.build(session, user, period)
        except Exception as e:
            report_error(user.tg_id, e, "PDF hisobot: ")
            raise
    return web.Response(
        body=pdf,
        content_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            # Telegram Web'dagi «Yuklab olish» oynasi uchun
            "Access-Control-Allow-Origin": "https://web.telegram.org",
            "Cache-Control": "no-store",
        },
    )


# --- Foydalanuvchi va sozlamalar --------------------------------------------

@routes.get("/api/me")
@endpoint()
async def me(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    bot: Bot = request.app["bot"]
    bot_me = await bot.me()
    job = await repo.get_job(session, user.account_id, user.job_id)
    return web.json_response(
        {
            "id": user.id,
            "name": user.name,
            "role": user.role,
            "is_owner": user.is_owner,
            # Ish boshqaruvchi: egasidek ko'radi va boshqaradi (o'zgartirishlari egasi tasdiqlagach bajariladi)
            "is_manager": user.is_manager,
            # Boshliq tasdig'i: o'chiq bo'lsa — hamma yozuv darhol kiradi, tasdiqlash sozlamalari ko'rsatilmaydi
            "approvals": settings.approvals,
            "job": _job_ref(job),
            "bot_username": bot_me.username,
            "currencies": await repo.enabled_currencies(session, user.account_id),
            "require_approval": await repo.require_approval(session, user.account_id),
            "require_pay_approval": await repo.require_pay_approval(session, user.account_id),
            "set_size": await repo.set_size(session, user.account_id),
            "auto_save": await repo.auto_save(session, user.account_id),
            "auto_save_images": await repo.auto_save_images(session, user.account_id),
            "savings_pct": await repo.savings_pct(session, user.account_id),
            "reminder_hour": await repo.reminder_hour(session, user.account_id),
        }
    )


# Telegram profil rasmi: tg_id -> (olingan vaqt, rasm yoki None)
_avatars: dict[int, tuple[float, bytes | None]] = {}
AVATAR_TTL = 6 * 3600


async def _load_avatar(bot: Bot, tg_id: int) -> bytes | None:
    photos = await bot.get_user_profile_photos(tg_id, limit=1)
    if not photos.photos:
        return None
    sizes = photos.photos[0]
    # Kichik, lekin aniq ko'rinadigan o'lcham (160 px atrofida)
    size = next((s for s in sizes if s.width >= 160), sizes[-1])
    file = await bot.get_file(size.file_id)
    data = await bot.download_file(file.file_path)
    return data.read() if data else None


@routes.get("/api/avatar")
@endpoint()
async def avatar(request: web.Request, session: AsyncSession, user: User) -> web.StreamResponse:
    if not user.tg_id:
        raise ApiError(404, "Rasm yo'q")
    loop = asyncio.get_running_loop()
    cached = _avatars.get(user.tg_id)
    if cached is None or loop.time() - cached[0] > AVATAR_TTL:
        try:
            image = await _load_avatar(request.app["bot"], user.tg_id)
        except Exception:
            log.warning("Profil rasmi olinmadi (tg %s)", user.tg_id, exc_info=True)
            image = None
        cached = (loop.time(), image)
        _avatars[user.tg_id] = cached
    if cached[1] is None:
        raise ApiError(404, "Rasm yo'q")
    return web.Response(body=cached[1], content_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@routes.patch("/api/settings")
@endpoint(owner_only=True)
async def update_settings(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    data = await _json(request)
    if "require_approval" in data:
        await repo.set_setting(session, user.id, "require_approval", "1" if data["require_approval"] else "0")
    if "require_pay_approval" in data:
        await repo.set_setting(
            session, user.id, "require_pay_approval", "1" if data["require_pay_approval"] else "0"
        )
    if "auto_save" in data:
        await repo.set_setting(session, user.id, "auto_save", "1" if data["auto_save"] else "0")
    if "auto_save_images" in data:
        await repo.set_setting(session, user.id, "auto_save_images", "1" if data["auto_save_images"] else "0")
    if "currencies" in data:
        cur = [c for c in repo.ALL_CURRENCIES if c in (data["currencies"] or [])]
        if not cur:
            raise ApiError(400, "Kamida bitta valyuta yoqilgan bo'lishi kerak")
        await repo.set_currencies(session, user.id, cur)
    if "main_currency" in data:
        if data["main_currency"] not in repo.ALL_CURRENCIES:
            raise ApiError(400, "Valyuta noto'g'ri")
        await repo.set_main_currency(session, user.id, data["main_currency"])
    if "set_size" in data:
        try:
            size = int(data["set_size"])
        except (TypeError, ValueError):
            size = 0
        if not 1 <= size <= repo.MAX_SET_SIZE:
            raise ApiError(400, f"Komplektdagi dona soni 1 dan {repo.MAX_SET_SIZE} gacha bo'lsin")
        await repo.set_setting(session, user.id, "set_size", str(size))
    if "savings_pct" in data:
        if data["savings_pct"] not in repo.SAVINGS_CHOICES:
            raise ApiError(400, "Jamg'arma ulushi noto'g'ri")
        await repo.set_setting(session, user.id, "savings_pct", str(data["savings_pct"]))
    if "reminder_hour" in data:
        hour = data["reminder_hour"]
        if hour is not None and hour not in repo.REMINDER_HOURS:
            raise ApiError(400, "Eslatma vaqti noto'g'ri")
        await repo.set_setting(session, user.id, "reminder_hour", "off" if hour is None else str(hour))
    return web.json_response(
        {
            "currencies": await repo.enabled_currencies(session, user.id),
            "require_approval": await repo.require_approval(session, user.id),
            "require_pay_approval": await repo.require_pay_approval(session, user.id),
            "set_size": await repo.set_size(session, user.id),
            "auto_save": await repo.auto_save(session, user.id),
            "auto_save_images": await repo.auto_save_images(session, user.id),
            "savings_pct": await repo.savings_pct(session, user.id),
            "reminder_hour": await repo.reminder_hour(session, user.id),
        }
    )


# --- Moliyaviy maslahat ---------------------------------------------------

@routes.get("/api/advice")
@endpoint(owner_only=True)
async def advice(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    return web.json_response(await advisor.advice_data(session, user.id))


@routes.post("/api/advice/ai")
@endpoint(owner_only=True)
async def advice_ai(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    try:
        text = await llm.advise(await advisor.advice_context(session, user.id))
    except llm.LlmError:
        raise ApiError(502, "Hozir maslahat tayyorlab bo'lmadi. Birozdan keyin qayta urinib ko'ring.")
    return web.json_response({"text": text})


# --- Yozuvlar ---------------------------------------------------------------

def _job_ref(job: Job | None) -> dict | None:
    return {"id": job.id, "name": job.name} if job is not None else None


def _entry_json(e: WorkEntry) -> dict:
    total = e.unit_price * e.quantity if e.unit_price is not None else None
    return {
        "id": e.id,
        "date": e.work_date.isoformat(),
        "worker": {"id": e.worker_id, "name": e.worker.name},
        "model": e.model.name if e.model else None,  # model so'ralmaydigan kasbda (kroy, brsovka) — bo'sh
        "job": e.job.name if e.job else None,
        "qty": e.quantity,
        "unit_price": _num(e.unit_price),
        "currency": e.price_currency,
        "total": _num(total),
        "status": e.status,
        "raw_text": e.raw_text,
    }


async def _entry_for(request: web.Request, session: AsyncSession, user: User) -> WorkEntry:
    entry = await repo.get_work_entry(session, user.account_id, int(request.match_info["id"]))
    if entry is None or not (user.manages or entry.worker_id == user.id):
        raise ApiError(404, "Yozuv topilmadi")
    return entry


@routes.get("/api/entries")
@endpoint()
async def entries(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    q = request.query
    status = q.get("status") or None
    if status not in (None, EntryStatus.PENDING, EntryStatus.APPROVED, EntryStatus.REJECTED):
        raise ApiError(400, "Noto'g'ri holat")
    worker_id = user.id if not user.manages else (int(q["worker_id"]) if q.get("worker_id") else None)
    limit = min(int(q.get("limit", 30)), 100)
    offset = max(int(q.get("offset", 0)), 0)
    rows = await repo.list_work_entries(
        session, user.account_id, status=status, worker_id=worker_id, limit=limit + 1, offset=offset
    )
    return web.json_response({"items": [_entry_json(e) for e in rows[:limit]], "has_more": len(rows) > limit})


@routes.post(r"/api/entries/{id:\d+}/{action:approve|reject}")
@endpoint(owner_only=True)
async def review(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    from bot.handlers.entries import entry_text  # aylanma importdan qochish uchun

    entry = await _entry_for(request, session, user)
    approve = request.match_info["action"] == "approve"
    await repo.review_work_entry(session, entry, user, approve)
    if entry.worker.tg_id and entry.worker_id != user.id:
        verdict = "✅ tasdiqlandi" if approve else "🚫 rad etildi"
        _notify(request, entry.worker.tg_id, f"Yozuvingiz {verdict}:\n\n" + entry_text(entry))
    return web.json_response(_entry_json(entry))


async def _ask_delete_entry(session: AsyncSession, manager: User, match: dict, body: dict | None) -> str:
    from bot.handlers.entries import entry_text

    entry = await repo.get_work_entry(session, manager.account_id, _int_id(match))
    if entry is None:
        raise ApiError(404, "Yozuv topilmadi")
    return f"🗑 <b>Ish yozuvini o'chirish</b>\n\n{entry_text(entry)}"


@routes.delete(r"/api/entries/{id:\d+}")
@endpoint(manager=_ask_delete_entry)
async def delete_entry(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    from bot.handlers.entries import entry_text

    entry = await _entry_for(request, session, user)
    text = entry_text(entry)
    await repo.soft_delete(session, entry)
    if not user.is_owner and entry.status == EntryStatus.APPROVED:
        owner = await session.get(User, user.owner_id)
        if owner and owner.tg_id:
            _notify(request, owner.tg_id, f"🗑 {escape(user.name)} tasdiqlangan yozuvni o'chirdi:\n\n{text}")
    return web.json_response({"ok": True})


# --- Ishchilar --------------------------------------------------------------

async def _worker_json(
    request: web.Request,
    w: User,
    stats: dict[int, dict],
    jobs: dict[int, Job],
    balances: dict[int, repo.WorkerBalance] | None,
) -> dict:
    """balances=None — ish boshqaruvchiga: ishchilarning qoldig'i (haqqi) ko'rsatilmaydi."""
    # Havola doimiy: ulangandan keyin ham ko'rinadi — ishchi undan istalgan vaqt qayta kiradi
    link = None
    if w.is_active and w.invite_token:
        bot_me = await request.app["bot"].me()
        link = f"https://t.me/{bot_me.username}?start=inv_{w.invite_token}"
    s = stats.get(w.id, {"qty": 0, "pay": {}})
    return {
        "id": w.id,
        "name": w.name,
        "job": _job_ref(jobs.get(w.job_id)),
        "is_active": w.is_active,
        "is_manager": w.is_manager,
        "linked": w.tg_id is not None,
        "invite_link": link,
        "month_qty": s["qty"],
        "month_pay": s["pay"],
        "balance": _balance_json(balances.get(w.id)) if balances is not None else None,
    }


async def _with_tokens(session: AsyncSession, items: list) -> list:
    """Ishchi, mijoz, ta'minotchi: faollarining doimiy havolasi bo'lsin (avval ulanganlarda token yo'q edi)."""
    for item in items:
        if item.is_active:
            await repo.invite_token(session, item)
    return items


async def _one_worker_json(request: web.Request, session: AsyncSession, worker: User, money: bool = True) -> dict:
    """money=False — ish boshqaruvchiga: qoldig'isiz."""
    await _with_tokens(session, [worker])
    owner_id = worker.owner_id
    stats = await _month_stats(session, owner_id)
    balances = await repo.worker_balances(session, owner_id, [worker.id]) if money else None
    return await _worker_json(request, worker, stats, await _jobs_by_id(session, owner_id), balances)


async def _month_stats(session: AsyncSession, owner_id: int) -> dict[int, dict]:
    t = today()
    rows = await repo.work_stat_rows(session, owner_id, t.replace(day=1), t)
    return {g["id"]: g for g in _group(rows, "worker_id")}


async def _jobs_by_id(session: AsyncSession, owner_id: int) -> dict[int, Job]:
    return {j.id: j for j in await repo.list_jobs(session, owner_id)}


async def _job_arg(session: AsyncSession, owner_id: int, value) -> int | None:
    """So'rovdagi job_id: bo'sh — kasbsiz, aks holda shu kabinetdagi faol kasb bo'lishi shart."""
    if value in (None, "", 0):
        return None
    try:
        job_id = int(value)
    except (TypeError, ValueError):
        raise ApiError(400, "Kasb noto'g'ri")
    if await repo.get_job(session, owner_id, job_id) is None:
        raise ApiError(400, "Kasb topilmadi")
    return job_id


@routes.get("/api/workers")
@endpoint(owner_only=True, manager=VIEW)
async def workers(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    acc = user.account_id
    stats = await _month_stats(session, acc)
    jobs = await _jobs_by_id(session, acc)
    workers_ = await _with_tokens(session, await repo.list_workers(session, acc, active_only=False))
    balances = await repo.worker_balances(session, acc) if user.is_owner else None
    items = [await _worker_json(request, w, stats, jobs, balances) for w in workers_]
    items.sort(key=lambda w: (not w["is_active"], -w["month_qty"], w["name"].lower()))
    return web.json_response({"items": items, "jobs": await _jobs_list(session, acc)})


async def _worker_of(session: AsyncSession, owner_id: int, match: dict) -> User:
    worker = await repo.get_worker(session, owner_id, _int_id(match))
    if worker is None:
        raise ApiError(404, "Ishchi topilmadi")
    return worker


def _worker_name(data: dict) -> str:
    name = str(data.get("name") or "").strip()[:100]
    if not name:
        raise ApiError(400, "Ism bo'sh bo'lmasin")
    return name


async def _ask_add_worker(session: AsyncSession, manager: User, match: dict, body: dict | None) -> str:
    acc, data = manager.account_id, body or {}
    name = _worker_name(data)
    job = await repo.get_job(session, acc, await _job_arg(session, acc, data.get("job_id")))
    return f"👷 <b>Yangi ishchi</b>: {escape(name)}" + (f" · 🏷 {escape(job.name)}" if job else "")


@routes.post("/api/workers")
@endpoint(owner_only=True, manager=_ask_add_worker)
async def add_worker(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    data = await _json(request)
    name = str(data.get("name") or "").strip()[:100]
    if not name:
        raise ApiError(400, "Ism bo'sh bo'lmasin")
    job_id = await _job_arg(session, user.id, data.get("job_id"))
    worker = await repo.create_worker(session, user.id, name, job_id)
    return web.json_response(await _worker_json(request, worker, {}, await _jobs_by_id(session, user.id), {}))


async def _get_worker(request: web.Request, session: AsyncSession, user: User) -> User:
    return await _worker_of(session, user.account_id, request.match_info)


async def _ask_update_worker(session: AsyncSession, manager: User, match: dict, body: dict | None) -> str:
    acc, data = manager.account_id, body or {}
    worker = await _worker_of(session, acc, match)
    changes = []
    if "name" in data and (name := _worker_name(data)) != worker.name:
        changes.append(f"ismi → <b>{escape(name)}</b>")
    if "job_id" in data and (job_id := await _job_arg(session, acc, data["job_id"])) != worker.job_id:
        jobs = await _jobs_by_id(session, acc)
        old, new = jobs.get(worker.job_id), jobs.get(job_id)
        changes.append(
            f"kasbi: {escape(old.name) if old else 'kasbsiz'} → <b>{escape(new.name) if new else 'kasbsiz'}</b>"
        )
    return _changes(f"👷 <b>Ishchi {escape(worker.name)}</b>", changes)


@routes.patch(r"/api/workers/{id:\d+}")
@endpoint(owner_only=True, manager=_ask_update_worker)
async def update_worker(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    worker = await _get_worker(request, session, user)
    data = await _json(request)
    if "name" in data:
        worker.name = _worker_name(data)
    if "job_id" in data:
        worker.job_id = await _job_arg(session, user.id, data["job_id"])
    await session.flush()
    return web.json_response(await _one_worker_json(request, session, worker))


def _target_active(data: dict | None, current: bool, what: str) -> bool:
    """{is_active} — qaysi holatga (boshqaruvchi so'ragani), bo'lmasa — teskarisiga. Allaqachon shunday — xato."""
    active = bool(data["is_active"]) if data and "is_active" in data else not current
    if active == current:
        raise ApiError(400, f"{what} allaqachon {'faol' if active else 'o‘chirilgan'}")
    return active


async def _ask_toggle_worker(session: AsyncSession, manager: User, match: dict, body: dict | None) -> str:
    worker = await _worker_of(session, manager.account_id, match)
    body["is_active"] = _target_active(body, worker.is_active, "Ishchi")
    if not body["is_active"]:
        return f"⛔ <b>Ishchini o'chirish</b>: {escape(worker.name)} — botdan foydalana olmaydi"
    return f"✅ <b>Ishchini qayta faollashtirish</b>: {escape(worker.name)}"


@routes.post(r"/api/workers/{id:\d+}/toggle")
@endpoint(owner_only=True, manager=_ask_toggle_worker)
async def toggle_worker(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    worker = await _get_worker(request, session, user)
    data = await _json(request) if request.can_read_body else None
    worker.is_active = _target_active(data, worker.is_active, "Ishchi")
    await session.flush()
    return web.json_response(await _one_worker_json(request, session, worker))


@routes.post(r"/api/workers/{id:\d+}/invite")
@endpoint(owner_only=True, manager=VIEW)
async def reinvite_worker(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    worker = await _get_worker(request, session, user)
    if not worker.is_active:
        raise ApiError(400, "Avval ishchini faollashtiring")
    # Havola doimiy: bor bo'lsa — o'sha, almashtirilmaydi (yuborilgan havola eskirmaydi)
    await repo.invite_token(session, worker)
    return web.json_response(await _one_worker_json(request, session, worker, money=user.is_owner))


@routes.post(r"/api/workers/{id:\d+}/manager")
@endpoint(owner_only=True)
async def set_worker_manager(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """{is_manager} — ishchi ish boshqaruvchi bo'ladi (yoki yana oddiy ishchi); unga yangi menyu bilan xabar boradi."""
    from bot import approvals  # aylanma importdan qochish uchun

    worker = await _get_worker(request, session, user)
    on = bool((await _json(request)).get("is_manager"))
    if on and not worker.is_active:
        raise ApiError(400, "Avval ishchini faollashtiring")
    if on != worker.is_manager:
        await repo.set_manager(session, worker, on)
        if worker.tg_id and worker.is_active:
            _notify(request, worker.tg_id, approvals.role_notice(worker, user), kbs.main_menu(worker))
    return web.json_response(await _one_worker_json(request, session, worker))


# --- Kasblar ----------------------------------------------------------------

def _job_json(j: Job, counts: dict[int, int]) -> dict:
    return {
        "id": j.id,
        "name": j.name,
        "workers": counts.get(j.id, 0),
        "price": _num(j.price),
        "currency": j.currency,
        "model_free": j.model_free,
    }


async def _jobs_list(session: AsyncSession, owner_id: int) -> list[dict]:
    counts = await repo.job_worker_counts(session, owner_id)
    return [_job_json(j, counts) for j in await repo.list_jobs(session, owner_id)]


async def _jobs_response(session: AsyncSession, owner_id: int) -> web.Response:
    return web.json_response({"items": await _jobs_list(session, owner_id)})


def _job_name(data: dict) -> str:
    name = " ".join(str(data.get("name") or "").split())[:50]
    if not name:
        raise ApiError(400, "Kasb nomini yozing")
    return name


async def _job_of(session: AsyncSession, owner_id: int, match: dict) -> Job:
    job = await repo.get_job(session, owner_id, _int_id(match))
    if job is None:
        raise ApiError(404, "Kasb topilmadi")
    return job


async def _get_job(request: web.Request, session: AsyncSession, user: User) -> Job:
    return await _job_of(session, user.account_id, request.match_info)


async def _currency_arg(session: AsyncSession, owner_id: int, value) -> str:
    """So'rovdagi valyuta, aytilmasa — asosiy valyuta."""
    currency = value or (await repo.enabled_currencies(session, owner_id))[0]
    if currency not in repo.ALL_CURRENCIES:
        raise ApiError(400, "Valyuta noto'g'ri")
    return currency


@routes.get("/api/jobs")
@endpoint(owner_only=True, manager=VIEW)
async def jobs(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    return await _jobs_response(session, user.account_id)


async def _ask_add_job(session: AsyncSession, manager: User, match: dict, body: dict | None) -> str:
    name = _job_name(body or {})
    existing = await repo.get_job_by_name(session, manager.account_id, name)
    if existing is not None and existing.is_active:
        raise ApiError(400, "Bunday kasb bor")
    return f"🏷 <b>Yangi kasb</b>: {escape(name)}"


@routes.post("/api/jobs")
@endpoint(owner_only=True, manager=_ask_add_job)
async def add_job(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    name = _job_name(await _json(request))
    existing = await repo.get_job_by_name(session, user.id, name)
    if existing is not None and existing.is_active:
        raise ApiError(400, "Bunday kasb bor")
    await repo.create_job(session, user.id, name)
    return await _jobs_response(session, user.id)


async def _ask_update_job(session: AsyncSession, manager: User, match: dict, body: dict | None) -> str:
    acc, data = manager.account_id, body or {}
    job = await _job_of(session, acc, match)
    changes = []
    if "name" in data and (name := _job_name(data)) != job.name:
        other = await repo.get_job_by_name(session, acc, name)
        if other is not None and other.id != job.id and other.is_active:
            raise ApiError(400, "Bunday kasb bor")
        changes.append(f"nomi → <b>{escape(name)}</b>")
    currency = await _currency_arg(session, acc, data.get("currency"))
    if "price" in data:
        price = _parse_price(data["price"])
        old = (job.price, job.currency) if job.price is not None else None
        changes += _price_change("narxi (hamma modellar)", old, (price, currency) if price else None)
    if "model_prices" in data:
        prices = data["model_prices"]
        if not isinstance(prices, dict):
            raise ApiError(400, "Narxlar noto'g'ri")
        models = {m.id: m for m in await repo.list_models(session, acc, active_only=False)}
        current = await repo.model_prices(session, list(models))
        for key, value in prices.items():
            model = models.get(int(key)) if str(key).isdigit() else None
            if model is None:
                raise ApiError(400, "Model noto'g'ri")
            row = current.get(model.id, {}).get(job.id)
            price = _parse_price(value)
            new = (price, currency) if price else None
            changes += _price_change(escape(model.name), (row.price, row.currency) if row else None, new)
    if "model_free" in data and bool(data["model_free"]) != job.model_free:
        changes.append(f"✂️ modelsiz ham bo'ladi: <b>{'ha' if data['model_free'] else 'yo‘q'}</b>")
    return _changes(f"🏷 <b>Kasb {escape(job.name)}</b> (bir dona narxi)", changes)


@routes.patch(r"/api/jobs/{id:\d+}")
@endpoint(owner_only=True, manager=_ask_update_job)
async def update_job(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """{name, price, currency, model_free, model_prices} — berilganlari o'zgaradi. price — kasbning o'z narxi (hamma
    modellar uchun), null — olib tashlanadi; model_free — modelsiz ham bo'ladi (kroy, brsovka); model_prices —
    {model_id: narx yoki null}: shu kasbning modellar bo'yicha narxi (Loafer kroyi, Sapog kroyi)."""
    job = await _get_job(request, session, user)
    data = await _json(request)
    if "name" in data and not await repo.rename_job(session, job, _job_name(data)):
        raise ApiError(400, "Bunday kasb bor")
    currency = await _currency_arg(session, user.id, data.get("currency"))
    if "price" in data:
        price = _parse_price(data["price"])
        await repo.set_job_price(session, job, price if price else None, currency)
    if "model_prices" in data:
        prices = data["model_prices"]
        if not isinstance(prices, dict):
            raise ApiError(400, "Narxlar noto'g'ri")
        for key, value in prices.items():
            model = await repo.get_model(session, user.id, int(key)) if str(key).isdigit() else None
            if model is None:
                raise ApiError(400, "Model noto'g'ri")
            await repo.set_model_price(session, model, job.id, _parse_price(value) or None, currency)
    if "model_free" in data:
        job.model_free = bool(data["model_free"])
        await session.flush()
    return await _jobs_response(session, user.id)


async def _ask_delete_job(session: AsyncSession, manager: User, match: dict, body: dict | None) -> str:
    job = await _job_of(session, manager.account_id, match)
    n = (await repo.job_worker_counts(session, manager.account_id)).get(job.id)
    return f"🗑 <b>Kasbni o'chirish</b>: {escape(job.name)}" + (f"\n{n} ta ishchi kasbsiz qoladi" if n else "")


@routes.delete(r"/api/jobs/{id:\d+}")
@endpoint(owner_only=True, manager=_ask_delete_job)
async def delete_job(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    await repo.delete_job(session, await _get_job(request, session, user))
    return await _jobs_response(session, user.id)


# --- Modellar ---------------------------------------------------------------

def _model_json(m: ItemModel, prices: dict[int, ModelPrice]) -> dict:
    """prices — shu modelning kasb narxlari (job_id -> narx)."""
    return {
        "id": m.id,
        "name": m.name,
        "price": _num(m.price),
        "currency": m.currency,
        "is_active": m.is_active,
        "prices": [{"job_id": j, "price": float(r.price), "currency": r.currency} for j, r in prices.items()],
    }


def _worker_model_json(m: ItemModel, prices: dict[int, ModelPrice], job: Job | None) -> dict:
    """Ishchiga faqat o'z kasbining narxi (bo'lmasa kasbning o'z narxi, u ham bo'lmasa modelning umumiy narxi —
    faqat tikuvchiga: kroychiga zagatovka narxi ko'rinmaydi) ko'rinadi."""
    row = prices.get(job.id) if job else None
    if row:
        price, currency = row.price, row.currency
    elif job is not None and job.price is not None:
        price, currency = job.price, job.currency
    elif jobs_mod.takes_model_price(job):
        price, currency = m.price, m.currency
    else:
        price, currency = None, m.currency
    return {"id": m.id, "name": m.name, "price": _num(price), "currency": currency, "is_active": m.is_active}


def _parse_price(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        price = Decimal(str(value).replace(" ", "").replace(",", "."))
    except InvalidOperation:
        raise ApiError(400, "Narx noto'g'ri")
    if price < 0 or price >= Decimal("1e12"):
        raise ApiError(400, "Narx noto'g'ri")
    return price


async def _apply_prices(session: AsyncSession, model: ItemModel, data: dict) -> None:
    """data["prices"]: {job_id: narx yoki null} — kasb narxlari modelning valyutasida."""
    prices = data.get("prices")
    if prices is None:
        return
    if not isinstance(prices, dict):
        raise ApiError(400, "Narxlar noto'g'ri")
    for key, value in prices.items():
        job_id = await _job_arg(session, model.owner_id, key)
        if job_id is None:
            raise ApiError(400, "Kasb noto'g'ri")
        await repo.set_model_price(session, model, job_id, _parse_price(value), model.currency)


async def _apply_model(session: AsyncSession, model: ItemModel, data: dict) -> None:
    if "name" in data:
        name = _model_name(data)
        existing = await repo.get_model_by_name(session, model.owner_id, name)
        if existing and existing.id != model.id:
            raise ApiError(400, "Bunday model allaqachon bor")
        model.name = name
    if "price" in data:
        model.price = _parse_price(data["price"])
    if "currency" in data:
        if data["currency"] not in repo.ALL_CURRENCIES:
            raise ApiError(400, "Valyuta noto'g'ri")
        model.currency = data["currency"]
    if "is_active" in data:
        model.is_active = bool(data["is_active"])


async def _model_prices(session: AsyncSession, model: ItemModel) -> dict[int, ModelPrice]:
    return (await repo.model_prices(session, [model.id])).get(model.id, {})


@routes.get("/api/models")
@endpoint()
async def models(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    acc = user.account_id
    items = await repo.list_models(session, acc, active_only=not user.manages)
    prices = await repo.model_prices(session, [m.id for m in items])
    if user.manages:
        return web.json_response(
            {"items": [_model_json(m, prices.get(m.id, {})) for m in items], "jobs": await _jobs_list(session, acc)}
        )
    job = await repo.get_job(session, user.account_id, user.job_id)
    return web.json_response({"items": [_worker_model_json(m, prices.get(m.id, {}), job) for m in items]})


def _model_name(data: dict) -> str:
    name = str(data.get("name") or "").strip()[:100]
    if not name:
        raise ApiError(400, "Nom bo'sh bo'lmasin")
    return name


async def _job_price_changes(
    session: AsyncSession, owner_id: int, data: dict, currency: str, current: dict[int, ModelPrice]
) -> list[str]:
    """data["prices"] ({job_id: narx}) — modelning kasb narxlari qanday o'zgaradi (_apply_prices dagidek)."""
    prices = data.get("prices")
    if prices is None:
        return []
    if not isinstance(prices, dict):
        raise ApiError(400, "Narxlar noto'g'ri")
    jobs = await _jobs_by_id(session, owner_id)
    changes = []
    for key, value in prices.items():
        job_id = await _job_arg(session, owner_id, key)
        if job_id is None:
            raise ApiError(400, "Kasb noto'g'ri")
        row = current.get(job_id)
        price = _parse_price(value)
        new = (price, currency) if price is not None else None
        changes += _price_change(escape(jobs[job_id].name), (row.price, row.currency) if row else None, new)
    return changes


async def _ask_add_model(session: AsyncSession, manager: User, match: dict, body: dict | None) -> str:
    acc, data = manager.account_id, body or {}
    name = _model_name(data)
    if await repo.get_model_by_name(session, acc, name):
        raise ApiError(400, "Bunday model allaqachon bor")
    currency = await _currency_arg(session, acc, data.get("currency"))
    price = _parse_price(data.get("price"))
    changes = _price_change("umumiy (zagatovka) narxi", None, (price, currency) if price is not None else None)
    changes += await _job_price_changes(session, acc, data, currency, {})
    title = f"🧵 <b>Yangi model</b>: {escape(name)}"
    return _changes(title, changes) if changes else title


@routes.post("/api/models")
@endpoint(owner_only=True, manager=_ask_add_model)
async def add_model(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    data = await _json(request)
    currencies = await repo.enabled_currencies(session, user.id)
    model = ItemModel(owner_id=user.id, name="", currency=currencies[0])
    data.setdefault("name", "")
    await _apply_model(session, model, data)
    session.add(model)
    await session.flush()
    await _apply_prices(session, model, data)
    return web.json_response(_model_json(model, await _model_prices(session, model)))


async def _ask_update_model(session: AsyncSession, manager: User, match: dict, body: dict | None) -> str:
    acc, data = manager.account_id, body or {}
    model = await repo.get_model(session, acc, _int_id(match))
    if model is None:
        raise ApiError(404, "Model topilmadi")
    changes = []
    if "name" in data and (name := _model_name(data)) != model.name:
        existing = await repo.get_model_by_name(session, acc, name)
        if existing and existing.id != model.id:
            raise ApiError(400, "Bunday model allaqachon bor")
        changes.append(f"nomi → <b>{escape(name)}</b>")
    currency = model.currency
    if "currency" in data:
        currency = await _currency_arg(session, acc, data["currency"])
    old = (model.price, model.currency) if model.price is not None else None
    if "price" in data:
        price = _parse_price(data["price"])
        changes += _price_change("umumiy (zagatovka) narxi", old, (price, currency) if price is not None else None)
    elif old and currency != model.currency:
        changes += _price_change("umumiy (zagatovka) narxi", old, (model.price, currency))
    changes += await _job_price_changes(session, acc, data, currency, await _model_prices(session, model))
    if "is_active" in data and bool(data["is_active"]) != model.is_active:
        changes.append("✅ qayta faol bo'ladi" if data["is_active"] else "⛔ o'chiriladi — yangi yozuvlarda taklif qilinmaydi")
    return _changes(f"🧵 <b>Model {escape(model.name)}</b> (bir dona narxi)", changes)


@routes.patch(r"/api/models/{id:\d+}")
@endpoint(owner_only=True, manager=_ask_update_model)
async def update_model(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    model = await repo.get_model(session, user.id, int(request.match_info["id"]))
    if model is None:
        raise ApiError(404, "Model topilmadi")
    data = await _json(request)
    await _apply_model(session, model, data)
    await session.flush()
    await _apply_prices(session, model, data)
    return web.json_response(_model_json(model, await _model_prices(session, model)))


# --- Shaxsiy hamyon: xarajat / daromad --------------------------------------

def _personal_totals(rows: list[repo.PersonalStatRow]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, Decimal]] = {
        PersonalKind.EXPENSE: defaultdict(Decimal),
        PersonalKind.INCOME: defaultdict(Decimal),
    }
    for r in rows:
        out.setdefault(r.kind, defaultdict(Decimal))[r.currency] += r.amount
    return {kind: _money(v) for kind, v in out.items()}


def _personal_series(rows: list[repo.PersonalStatRow], start: date, end: date, bucket: str) -> list[dict]:
    def key(d: date) -> str:
        return d.strftime("%Y-%m") if bucket == "month" else d.isoformat()

    points: dict[str, dict] = {}
    d = start
    while d <= end:
        k = key(d)
        points.setdefault(k, {"key": k, "expense": defaultdict(float), "income": defaultdict(float)})
        d = _shift_month(d, 1) if bucket == "month" else d + timedelta(days=1)
    for r in rows:
        p = points.get(key(r.entry_date))
        if p is not None and r.kind in (PersonalKind.EXPENSE, PersonalKind.INCOME):
            p[r.kind][r.currency] += float(r.amount)
    return [{"key": p["key"], "expense": dict(p["expense"]), "income": dict(p["income"])} for p in points.values()]


def _by_category(rows: list[repo.PersonalStatRow], kind: str) -> list[dict]:
    groups: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for r in rows:
        if r.kind == kind:
            groups[r.category][r.currency] += r.amount
    items = [{"name": name, "amounts": _money(v)} for name, v in groups.items()]
    items.sort(key=lambda x: max(x["amounts"].values(), default=0), reverse=True)
    return items


@routes.get("/api/personal")
@endpoint(owner_only=True)
async def personal_summary(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    period = request.query.get("period", "month")
    if period not in PERIODS:
        raise ApiError(400, "Noto'g'ri davr")
    t = today()
    b = _period_bounds(period, t)
    chart_start, chart_end, bucket = b["chart"]
    lo = min(b["start"], b["prev"][0], chart_start)
    hi = max(b["end"], chart_end)
    rows = await repo.personal_stat_rows(session, user.id, lo, hi)
    cur = [r for r in rows if b["start"] <= r.entry_date <= b["end"]]
    prev = [r for r in rows if b["prev"][0] <= r.entry_date <= b["prev"][1]]
    return web.json_response(
        {
            "period": period,
            "start": b["start"].isoformat(),
            "end": b["end"].isoformat(),
            "today": t.isoformat(),
            "totals": _personal_totals(cur),
            "prev": _personal_totals(prev),
            "series": {
                "bucket": bucket,
                "points": _personal_series(
                    [r for r in rows if chart_start <= r.entry_date <= chart_end], chart_start, chart_end, bucket
                ),
            },
            "by_category": _by_category(cur, PersonalKind.EXPENSE),
            "income_by_category": _by_category(cur, PersonalKind.INCOME),
        }
    )


def _personal_json(e: PersonalEntry) -> dict:
    return {
        "id": e.id,
        "date": e.entry_date.isoformat(),
        "kind": e.kind,
        "category": e.category,
        "amount": float(e.amount),
        "currency": e.currency,
        "note": e.note,
        "raw_text": e.raw_text,
    }


def _paging(request: web.Request) -> tuple[int, int]:
    try:
        limit = min(max(int(request.query.get("limit", 30)), 1), 100)
        offset = max(int(request.query.get("offset", 0)), 0)
    except ValueError:
        raise ApiError(400, "Noto'g'ri so'rov")
    return limit, offset


@routes.get("/api/personal/entries")
@endpoint(owner_only=True)
async def personal_entries(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    limit, offset = _paging(request)
    rows = await repo.list_personal_entries(session, user.id, limit=limit + 1, offset=offset)
    return web.json_response({"items": [_personal_json(e) for e in rows[:limit]], "has_more": len(rows) > limit})


@routes.delete(r"/api/personal/{id:\d+}")
@endpoint(owner_only=True)
async def delete_personal(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    entry = await repo.get_personal_entry(session, user.id, int(request.match_info["id"]))
    if entry is None:
        raise ApiError(404, "Yozuv topilmadi")
    await repo.soft_delete(session, entry)
    return web.json_response({"ok": True})


# --- Qarzlar ----------------------------------------------------------------

def _debt_json(e: DebtEntry) -> dict:
    return {
        "id": e.id,
        "date": e.entry_date.isoformat(),
        "person": e.person,
        "key": e.person_key,
        "direction": e.direction,
        "stated": e.stated,
        "amount": float(e.amount),
        "currency": e.currency,
        "note": e.note,
        "raw_text": e.raw_text,
    }


def _debt_balance_json(b: repo.DebtBalance) -> dict:
    return {
        "type": b.source,
        "key": b.key,
        "id": b.ref_id,
        "name": b.person,
        "balance": _money({c: v for c, v in b.balance.items() if v}),
        "last_date": b.last_date.isoformat() if b.last_date else None,
        "count": b.count,
        "materials": b.materials,
        "settled": b.settled,
    }


@routes.get("/api/debts")
@endpoint(owner_only=True)
async def debts(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """Umumiy qarzlar: odamlar, ta'minotchilar (qarzga olingan xom ashyo bilan), ishchilar va mijozlar — har biri
    o'z bo'limida (groups — bo'lim jami), owed_to_me / i_owe — hammasining jami."""
    balances = await repo.debt_overview(session, user.id)
    owed_to_me, i_owe = repo.debt_totals(balances)
    return web.json_response(
        {
            "people": [_debt_balance_json(b) for b in balances],
            "groups": [
                {"type": source, "owed_to_me": _money(owed), "i_owe": _money(owe)}
                for source, (owed, owe) in repo.debt_groups(balances).items()
            ],
            "owed_to_me": _money(owed_to_me),
            "i_owe": _money(i_owe),
        }
    )


@routes.get("/api/debts/entries")
@endpoint(owner_only=True)
async def debt_entries(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """Qarz yozuvlari va qarzga olingan xom ashyo — sana bo'yicha birga (yangisi birinchi)."""
    limit, offset = _paging(request)
    key = request.query.get("key") or None
    need = offset + limit + 1
    items: list[tuple[date, float, dict]] = []
    for e in await repo.list_debt_entries(session, user.id, key=key, limit=need):
        items.append((e.entry_date, e.created_at.timestamp(), {"type": "debt", **_debt_json(e)}))
    for e in await repo.list_credit_materials(session, user.id, key=key, limit=need):
        items.append((e.entry_date, e.created_at.timestamp(), {"type": "material", **_material_json(e)}))
    items.sort(key=lambda x: (x[0], x[1]), reverse=True)
    page = [x[2] for x in items[offset : offset + limit]]
    return web.json_response({"items": page, "has_more": len(items) > offset + limit})


@routes.delete(r"/api/debts/{id:\d+}")
@endpoint(owner_only=True)
async def delete_debt(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    entry = await repo.get_debt_entry(session, user.id, int(request.match_info["id"]))
    if entry is None:
        raise ApiError(404, "Yozuv topilmadi")
    await repo.soft_delete(session, entry)
    return web.json_response({"ok": True})


# --- Mijozlar: optom savdo --------------------------------------------------

def _client_entry_json(e: ClientEntry) -> dict:
    return {
        "id": e.id,
        "date": e.entry_date.isoformat(),
        "client": {"id": e.client_id, "name": e.client.name},
        "kind": e.kind,
        "product": e.product,
        "sets": e.sets,
        "pieces": e.pieces,
        "unit_price": _num(e.unit_price),
        "price_unit": e.price_unit,
        "amount": _num(e.amount) or 0,
        "currency": e.currency,
        "note": e.note,
        "raw_text": e.raw_text,
        "by_client": e.by_client,
        "deleted_at": e.deleted_at.isoformat() if e.deleted_at else None,
    }


def _client_json(st: repo.ClientStats, bot_username: str) -> dict:
    c = st.client
    # Havola doimiy: ulangandan keyin ham ko'rinadi — mijoz undan istalgan vaqt qayta kiradi
    link = None
    if c.is_active and c.invite_token:
        link = f"https://t.me/{bot_username}?start=cli_{c.invite_token}"
    return {
        "id": c.id,
        "name": c.name,
        "phone": c.phone,
        "is_active": c.is_active,
        "linked": c.tg_id is not None,
        "invite_link": link,
        "balance": _money({cur: v for cur, v in st.balance.items() if v}),
        "sold": _money(st.sold),
        "paid": _money(st.paid),
        "sets": st.sets,
        "pieces": st.pieces,
        "last_date": st.last_date.isoformat() if st.last_date else None,
        "count": st.count,
    }


def _sum_money(items: list[dict[str, Decimal]]) -> dict[str, float]:
    out: dict[str, Decimal] = defaultdict(Decimal)
    for item in items:
        for currency, value in item.items():
            out[currency] += value
    return _money({c: v for c, v in out.items() if v})


@routes.get("/api/clients")
@endpoint(owner_only=True, manager=VIEW)
async def clients(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    period = request.query.get("period", "month")
    if period not in PERIODS:
        raise ApiError(400, "Noto'g'ri davr")
    t = today()
    b = _period_bounds(period, t)
    stats = await repo.client_stats(session, user.account_id, b["start"], b["end"])
    await _with_tokens(session, [st.client for st in stats])
    username = (await request.app["bot"].me()).username
    return web.json_response(
        {
            "period": period,
            "start": b["start"].isoformat(),
            "end": b["end"].isoformat(),
            "today": t.isoformat(),
            "set_size": await repo.set_size(session, user.account_id),
            "items": [_client_json(st, username) for st in stats],
            "totals": {
                "owed": _sum_money([{c: v for c, v in st.balance.items() if v > 0} for st in stats]),
                "prepaid": _sum_money([{c: -v for c, v in st.balance.items() if v < 0} for st in stats]),
                "sold": _sum_money([st.sold for st in stats]),
                "paid": _sum_money([st.paid for st in stats]),
                "sets": sum(st.sets for st in stats),
                "pieces": sum(st.pieces for st in stats),
            },
        }
    )


def _client_fields(data: dict) -> tuple[str | None, str | None]:
    name = str(data.get("name") or "").strip()[:100] or None
    phone = str(data.get("phone") or "").strip()[:30] or None
    return name, phone


async def _ask_add_client(session: AsyncSession, manager: User, match: dict, body: dict | None) -> str:
    name, phone = _client_fields(body or {})
    if not name:
        raise ApiError(400, "Ismini kiriting")
    if await repo.get_client_by_name(session, manager.account_id, name):
        raise ApiError(400, "Bunday mijoz bor")
    return f"🛍 <b>Yangi mijoz</b>: {escape(name)}" + (f" · 📞 {escape(phone)}" if phone else "")


@routes.post("/api/clients")
@endpoint(owner_only=True, manager=_ask_add_client)
async def add_client(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    name, phone = _client_fields(await _json(request))
    if not name:
        raise ApiError(400, "Ismini kiriting")
    if await repo.get_client_by_name(session, user.id, name):
        raise ApiError(400, "Bunday mijoz bor")
    client = await repo.create_client(session, user.id, name, phone)
    username = (await request.app["bot"].me()).username
    return web.json_response(_client_json(repo.ClientStats(client=client), username))


async def _client_of(session: AsyncSession, owner_id: int, match: dict) -> Client:
    client = await repo.get_client(session, owner_id, _int_id(match))
    if client is None:
        raise ApiError(404, "Mijoz topilmadi")
    return client


async def _ask_update_client(session: AsyncSession, manager: User, match: dict, body: dict | None) -> str:
    acc, data = manager.account_id, body or {}
    client = await _client_of(session, acc, match)
    name, phone = _client_fields(data)
    changes = []
    if "name" in data and name != client.name:
        if not name:
            raise ApiError(400, "Ismini kiriting")
        same = await repo.get_client_by_name(session, acc, name)
        if same is not None and same.id != client.id:
            raise ApiError(400, "Bunday mijoz bor")
        changes.append(f"ismi → <b>{escape(name)}</b>")
    if "phone" in data and phone != client.phone:
        changes.append(f"telefoni → <b>{escape(phone) if phone else '—'}</b>")
    if "is_active" in data and bool(data["is_active"]) != client.is_active:
        changes.append("✅ qayta faol bo'ladi" if data["is_active"] else "⛔ o'chiriladi — xabar bormaydi, botda yoza olmaydi")
    return _changes(f"🛍 <b>Mijoz {escape(client.name)}</b>", changes)


@routes.patch(r"/api/clients/{id:\d+}")
@endpoint(owner_only=True, manager=_ask_update_client)
async def update_client(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    client = await repo.get_client(session, user.id, int(request.match_info["id"]))
    if client is None:
        raise ApiError(404, "Mijoz topilmadi")
    data = await _json(request)
    name, phone = _client_fields(data)
    if "name" in data:
        if not name:
            raise ApiError(400, "Ismini kiriting")
        same = await repo.get_client_by_name(session, user.id, name)
        if same is not None and same.id != client.id:
            raise ApiError(400, "Bunday mijoz bor")
        client.name = name
    if "phone" in data:
        client.phone = phone
    if "is_active" in data:
        client.is_active = bool(data["is_active"])
    await session.flush()
    return web.json_response({"ok": True})


@routes.post(r"/api/clients/{id:\d+}/invite")
@endpoint(owner_only=True, manager=VIEW)
async def reinvite_client(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """Doimiy kirish havolasi: bor bo'lsa — o'sha, almashtirilmaydi (yuborilgan havola eskirmaydi)."""
    client = await repo.get_client(session, user.account_id, int(request.match_info["id"]))
    if client is None:
        raise ApiError(404, "Mijoz topilmadi")
    if not client.is_active:
        raise ApiError(400, "Avval mijozni faollashtiring")
    token = await repo.invite_token(session, client)
    bot_me = await request.app["bot"].me()
    return web.json_response({"invite_link": f"https://t.me/{bot_me.username}?start=cli_{token}"})


@routes.get("/api/clients/entries")
@endpoint(owner_only=True, manager=VIEW)
async def client_entries(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """Oldi-berdi. Bitta mijoznikida (client_id) birinchi sahifada yana: outside — «Qarzlar»da qolib ketgan, mijoz
    hisobiga kirmagan qarzlari (also — shu ismli ishchi yoki ta'minotchi; ish boshqaruvchiga — yo'q: «Qarzlar»
    egasiniki); deleted — o'chirilgan yozuvlari (xato o'chgan bo'lsa qaytarish uchun)."""
    acc = user.account_id
    limit, offset = _paging(request)
    client_id = request.query.get("client_id")
    client_id = int(client_id) if client_id and client_id.isdigit() else None
    rows = await repo.list_client_entries(session, acc, client_id=client_id, limit=limit + 1, offset=offset)
    data = {"items": [_client_entry_json(e) for e in rows[:limit]], "has_more": len(rows) > limit}
    if client_id is not None and offset == 0 and (client := await repo.get_client(session, acc, client_id)):
        if user.is_owner:
            outside = await repo.client_outside_debts(session, acc, client)
            also = await repo.namesakes(session, acc, [b.person for b in outside])
            data["outside"] = [{**_debt_balance_json(b), "also": also.get(b.person, [])} for b in outside]
        deleted = await repo.deleted_client_entries(session, acc, client_id)
        data["deleted"] = [_client_entry_json(e) for e in deleted]
    return web.json_response(data)


@routes.post(r"/api/clients/{id:\d+}/merge-debts")
@endpoint(owner_only=True)
async def merge_into_client(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """«Qarzlar»da shu mijoz ismi bilan turgan qarzlar uning hisobiga o'tadi — keys bo'lsa faqat tanlangan odamlar
    (har biri alohida qo'shiladi, adashmaslik uchun); ulangan mijozga xabar boradi."""
    from bot.handlers.money import merge_client_debts  # aylanma importdan qochish uchun

    client = await repo.get_client(session, user.id, int(request.match_info["id"]))
    if client is None:
        raise ApiError(404, "Mijoz topilmadi")
    keys = (await _json(request)).get("keys") if request.can_read_body else None
    if keys is not None and not (isinstance(keys, list) and all(isinstance(k, str) for k in keys)):
        raise ApiError(400, "Noto'g'ri so'rov")
    merged = await merge_client_debts(session, user, client, keys)
    if merged.notice:
        _notify(request, *merged.notice)
    return web.json_response(
        {"ok": True, "count": len(merged.entries), "added": _money(merged.added), "balance": _money(merged.balance)}
    )


async def _ask_delete_client_entry(session: AsyncSession, manager: User, match: dict, body: dict | None) -> str:
    from bot.handlers.money import client_text  # aylanma importdan qochish uchun

    entry = await repo.get_client_entry(session, manager.account_id, _int_id(match))
    if entry is None:
        raise ApiError(404, "Yozuv topilmadi")
    return f"🗑 <b>Mijoz yozuvini o'chirish</b>\n\n{client_text(entry)}"


@routes.delete(r"/api/clients/entries/{id:\d+}")
@endpoint(owner_only=True, manager=_ask_delete_client_entry)
async def delete_client_entry(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    from bot.handlers.money import client_deleted_message  # aylanma importdan qochish uchun

    entry = await repo.get_client_entry(session, user.id, int(request.match_info["id"]))
    if entry is None:
        raise ApiError(404, "Yozuv topilmadi")
    await repo.soft_delete(session, entry)
    if notice := await client_deleted_message(session, user, entry):
        _notify(request, *notice)
    return web.json_response({"ok": True})


async def _ask_restore_client_entry(session: AsyncSession, manager: User, match: dict, body: dict | None) -> str:
    from bot.handlers.money import client_text

    entry = await session.get(ClientEntry, _int_id(match))
    if entry is None or entry.owner_id != manager.account_id or entry.deleted_at is None:
        raise ApiError(404, "Yozuv topilmadi yoki allaqachon qaytarilgan")
    return f"↩️ <b>O'chirilgan yozuvni qaytarish</b> — mijoz qoldig'iga yana kiradi\n\n{client_text(entry)}"


@routes.post(r"/api/clients/entries/{id:\d+}/restore")
@endpoint(owner_only=True, manager=_ask_restore_client_entry)
async def restore_client_entry(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """O'chirilgan yozuv qaytadi (qoldiqqa yana kiradi); ulangan mijozga xabar boradi."""
    from bot.handlers.money import client_restored_message  # aylanma importdan qochish uchun

    entry = await repo.restore_client_entry(session, user.id, int(request.match_info["id"]))
    if entry is None:
        raise ApiError(404, "Yozuv topilmadi yoki allaqachon qaytarilgan")
    if notice := await client_restored_message(session, user, entry):
        _notify(request, *notice)
    return web.json_response({"ok": True})


# --- Xom ashyo: teri, material, kley... ---------------------------------------

def _material_json(e: MaterialEntry) -> dict:
    return {
        "id": e.id,
        "date": e.entry_date.isoformat(),
        "kind": e.kind,
        "name": e.name,
        "quantity": _num(e.quantity),
        "unit": e.unit,
        "unit_price": _num(e.unit_price),
        "amount": _num(e.amount),
        "currency": e.currency,
        "supplier": e.supplier,
        "supplier_id": e.supplier_id,
        "on_credit": e.on_credit,
        "raw_text": e.raw_text,
    }


def _material_kinds(rows: list[repo.MaterialStatRow]) -> list[dict]:
    """Turlar bo'yicha: summa (valyuta bo'yicha), miqdor (birlik bo'yicha), xaridlar soni."""
    groups: dict[str, dict] = {}
    for r in rows:
        g = groups.setdefault(r.kind, {"amounts": defaultdict(Decimal), "qty": defaultdict(Decimal), "count": 0})
        g["amounts"][r.currency] += r.amount
        if r.quantity:
            g["qty"][r.unit or ""] += r.quantity
        g["count"] += r.count
    items = [
        {
            "name": kind,
            "amounts": _money({c: v for c, v in g["amounts"].items() if v}),
            "qty": {unit: float(q) for unit, q in g["qty"].items()},
            "count": g["count"],
        }
        for kind, g in groups.items()
    ]
    items.sort(key=lambda x: (max(x["amounts"].values(), default=0), x["count"]), reverse=True)
    return items


@routes.get("/api/materials")
@endpoint(owner_only=True, manager=VIEW)
async def materials(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    period = request.query.get("period", "month")
    if period not in PERIODS:
        raise ApiError(400, "Noto'g'ri davr")
    t = today()
    b = _period_bounds(period, t)
    rows = await repo.material_stat_rows(session, user.account_id, b["start"], b["end"])
    total: dict[str, Decimal] = defaultdict(Decimal)
    for r in rows:
        total[r.currency] += r.amount
    # Ta'minotchilar bilan qarz (davrdan qat'i nazar) — umumiy qarzlardagi «Xom ashyo» bo'limi
    debts_ = await repo.supplier_debts(session, user.account_id)
    owed_to_me, i_owe = repo.debt_totals(debts_)
    return web.json_response(
        {
            "period": period,
            "start": b["start"].isoformat(),
            "end": b["end"].isoformat(),
            "today": t.isoformat(),
            "total": _money({c: v for c, v in total.items() if v}),
            "count": sum(r.count for r in rows),
            "by_kind": _material_kinds(rows),
            "debts": {
                "people": [_debt_balance_json(d) for d in debts_],
                "owed_to_me": _money(owed_to_me),
                "i_owe": _money(i_owe),
            },
        }
    )


@routes.get("/api/materials/entries")
@endpoint(owner_only=True, manager=VIEW)
async def material_entries(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    limit, offset = _paging(request)
    supplier_id = request.query.get("supplier_id")
    rows = await repo.list_material_entries(
        session, user.account_id, supplier_id=int(supplier_id) if supplier_id and supplier_id.isdigit() else None,
        limit=limit + 1, offset=offset,
    )
    return web.json_response({"items": [_material_json(e) for e in rows[:limit]], "has_more": len(rows) > limit})


async def _material_of(session: AsyncSession, owner_id: int, match: dict) -> MaterialEntry:
    entry = await repo.get_material_entry(session, owner_id, _int_id(match))
    if entry is None:
        raise ApiError(404, "Yozuv topilmadi")
    return entry


async def _ask_delete_material(session: AsyncSession, manager: User, match: dict, body: dict | None) -> str:
    from bot.handlers.money import material_text  # aylanma importdan qochish uchun

    entry = await _material_of(session, manager.account_id, match)
    return f"🗑 <b>Xom ashyo xaridini o'chirish</b>\n\n{material_text(entry)}"


@routes.delete(r"/api/materials/{id:\d+}")
@endpoint(owner_only=True, manager=_ask_delete_material)
async def delete_material(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    from bot.handlers.money import supplier_deleted_message  # aylanma importdan qochish uchun

    entry = await repo.get_material_entry(session, user.id, int(request.match_info["id"]))
    if entry is None:
        raise ApiError(404, "Yozuv topilmadi")
    await repo.soft_delete(session, entry)
    if notice := await supplier_deleted_message(session, user, entry):
        _notify(request, *notice)
    return web.json_response({"ok": True})


async def _ask_material_credit(session: AsyncSession, manager: User, match: dict, body: dict | None) -> str:
    from bot.handlers.money import material_text

    entry = await _material_of(session, manager.account_id, match)
    on_credit = bool((body or {}).get("on_credit"))
    if on_credit and not repo.material_credit_ok(entry):
        raise ApiError(400, "Qarzga qo'shish uchun kimdan olingani va narxi yozilgan bo'lishi kerak")
    if on_credit == entry.on_credit:
        raise ApiError(400, "O'zgarish yo'q")
    who = escape(entry.supplier or "")
    title = (
        f"💳 <b>Qarzga qo'shish</b> — {who}ga qarz bo'lib «Qarzlar»ga qo'shiladi"
        if on_credit
        else f"💵 <b>To'langan deb belgilash</b> — {who} bilan qarzdan chiqariladi"
    )
    return f"{title}\n\n{material_text(entry)}"


@routes.post(r"/api/materials/{id:\d+}/credit")
@endpoint(owner_only=True, manager=_ask_material_credit)
async def material_credit(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """{"on_credit": true} — xarid ta'minotchiga qarz (umumiy qarzlarga qo'shiladi), false — to'langan xarid."""
    entry = await repo.get_material_entry(session, user.id, int(request.match_info["id"]))
    if entry is None:
        raise ApiError(404, "Yozuv topilmadi")
    on_credit = bool((await _json(request)).get("on_credit"))
    if on_credit and not repo.material_credit_ok(entry):
        raise ApiError(400, "Qarzga qo'shish uchun kimdan olingani va narxi yozilgan bo'lishi kerak")
    entry.on_credit = on_credit
    await session.flush()
    return web.json_response(_material_json(entry))


# --- Ta'minotchilar: xom ashyo kimdan olinadi ---------------------------------

async def _supplier_json(
    request: web.Request, sp: Supplier, stats: dict[int, repo.SupplierStats], debts: dict[str, dict[str, Decimal]]
) -> dict:
    """debt — u bilan qarz (umumiy «Qarzlar»dagi qatori): musbat — u qarzdor, manfiy — siz unga qarzdorsiz."""
    # Havola doimiy: ulangandan keyin ham ko'rinadi — ta'minotchi undan istalgan vaqt qayta kiradi
    link = None
    if sp.is_active and sp.invite_token:
        bot_me = await request.app["bot"].me()
        link = f"https://t.me/{bot_me.username}?start=sup_{sp.invite_token}"
    st = stats.get(sp.id) or repo.SupplierStats()
    return {
        "id": sp.id,
        "name": sp.name,
        "is_active": sp.is_active,
        "linked": sp.tg_id is not None,
        "invite_link": link,
        "month": _money(st.month),
        "month_count": st.month_count,
        "total": _money(st.total),
        "count": st.count,
        "debt": _money(debts.get(repo.person_key(sp.name), {})),
    }


async def _supplier_stats(session: AsyncSession, owner_id: int) -> dict[int, repo.SupplierStats]:
    return await repo.supplier_stats(session, owner_id, today().replace(day=1))


async def _one_supplier_json(request: web.Request, session: AsyncSession, supplier: Supplier) -> dict:
    stats = await _supplier_stats(session, supplier.owner_id)
    return await _supplier_json(request, supplier, stats, await repo.supplier_debt_balances(session, supplier.owner_id))


async def _supplier_of(session: AsyncSession, owner_id: int, match: dict) -> Supplier:
    supplier = await repo.get_supplier(session, owner_id, _int_id(match))
    if supplier is None:
        raise ApiError(404, "Ta'minotchi topilmadi")
    return supplier


async def _get_supplier(request: web.Request, session: AsyncSession, user: User) -> Supplier:
    return await _supplier_of(session, user.account_id, request.match_info)


@routes.get("/api/suppliers")
@endpoint(owner_only=True, manager=VIEW)
async def suppliers(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    acc = user.account_id
    stats = await _supplier_stats(session, acc)
    debts = await repo.supplier_debt_balances(session, acc)
    suppliers_ = await _with_tokens(session, await repo.list_suppliers(session, acc, False))
    items = [await _supplier_json(request, sp, stats, debts) for sp in suppliers_]
    items.sort(key=lambda sp: (not sp["is_active"], -sp["month_count"], -sp["count"], sp["name"].lower()))
    return web.json_response({"items": items})


def _supplier_name(data: dict) -> str:
    name = str(data.get("name") or "").strip()[:100]
    if not name:
        raise ApiError(400, "Nomini kiriting")
    return name


async def _ask_add_supplier(session: AsyncSession, manager: User, match: dict, body: dict | None) -> str:
    name = _supplier_name(body or {})
    if await repo.get_supplier_by_name(session, manager.account_id, name):
        raise ApiError(400, "Bunday ta'minotchi bor")
    return f"🏪 <b>Yangi ta'minotchi</b>: {escape(name)}"


@routes.post("/api/suppliers")
@endpoint(owner_only=True, manager=_ask_add_supplier)
async def add_supplier(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    name = _supplier_name(await _json(request))
    if await repo.get_supplier_by_name(session, user.id, name):
        raise ApiError(400, "Bunday ta'minotchi bor")
    supplier = await repo.create_supplier(session, user.id, name)
    return web.json_response(await _one_supplier_json(request, session, supplier))


async def _ask_toggle_supplier(session: AsyncSession, manager: User, match: dict, body: dict | None) -> str:
    supplier = await _supplier_of(session, manager.account_id, match)
    body["is_active"] = _target_active(body, supplier.is_active, "Ta'minotchi")
    if not body["is_active"]:
        return f"⛔ <b>Ta'minotchini o'chirish</b>: {escape(supplier.name)} — undan olinganda xabar bormaydi"
    return f"✅ <b>Ta'minotchini qayta faollashtirish</b>: {escape(supplier.name)}"


@routes.post(r"/api/suppliers/{id:\d+}/toggle")
@endpoint(owner_only=True, manager=_ask_toggle_supplier)
async def toggle_supplier(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    supplier = await _get_supplier(request, session, user)
    data = await _json(request) if request.can_read_body else None
    supplier.is_active = _target_active(data, supplier.is_active, "Ta'minotchi")
    await _with_tokens(session, [supplier])
    await session.flush()
    return web.json_response(await _one_supplier_json(request, session, supplier))


@routes.post(r"/api/suppliers/{id:\d+}/invite")
@endpoint(owner_only=True, manager=VIEW)
async def reinvite_supplier(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    supplier = await _get_supplier(request, session, user)
    if not supplier.is_active:
        raise ApiError(400, "Avval ta'minotchini faollashtiring")
    # Havola doimiy: bor bo'lsa — o'sha, almashtirilmaydi (yuborilgan havola eskirmaydi)
    await repo.invite_token(session, supplier)
    return web.json_response(await _one_supplier_json(request, session, supplier))


# --- Ishchi bilan hisob-kitob: avans, ish haqi, qaytarilgan pul, qoldiq ------------

def _pay_json(e: WorkerPayment, viewer: User) -> dict:
    return {
        "id": e.id,
        "date": e.entry_date.isoformat(),
        "worker": {"id": e.worker_id, "name": e.worker.name},
        "kind": e.kind,
        "amount": abs(float(e.amount)),
        # Hisob holatida: ishchi egasiga qarzdor (qoldiq manfiy)
        "worker_owes": e.amount < 0,
        "currency": e.currency,
        "note": e.note,
        "status": e.status,
        "raw_text": e.raw_text,
        "can_delete": viewer.is_owner or e.created_by == viewer.id,
    }


def _balance_json(b: repo.WorkerBalance | None) -> dict:
    b = b or repo.WorkerBalance(worker_id=0)
    return {
        "amounts": _money(b.balance),
        "since": b.since.isoformat() if b.since else None,
        "start": _money(b.start),
        "earned": _money(b.earned),
        "paid": _money(b.paid),
        "returned": _money(b.returned),
        # Pul yoki hisob holati yozilganmi (yozilmagan bo'lsa qoldiq — hamma ishlari summasi)
        "tracked": bool(b.since or b.paid or b.returned),
    }


def _ledger_json(r: repo.LedgerRow, viewer: User) -> dict:
    # Ish yozuvlari «Yozuvlar» bo'limida tahrirlanadi va o'chiriladi
    base = {**_entry_json(r.entry), "can_delete": False} if r.kind == "work" else _pay_json(r.entry, viewer)
    return {
        **base,
        "type": "work" if r.kind == "work" else "pay",
        "kind": r.kind,
        "counted": r.counted,
        "delta": _money(r.delta),
        "balance": _money(r.balance),
    }


async def _ledger_worker(request: web.Request, session: AsyncSession, user: User, value) -> User:
    """Egasi — tanlangan ishchi, ishchi — o'zi."""
    if not user.is_owner:
        return user
    try:
        worker_id = int(value)
    except (TypeError, ValueError):
        raise ApiError(400, "Ishchini tanlang")
    worker = await repo.get_worker(session, user.id, worker_id)
    if worker is None:
        raise ApiError(404, "Ishchi topilmadi")
    return worker


@routes.get("/api/ledger")
@endpoint()
async def ledger(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    worker = await _ledger_worker(request, session, user, request.query.get("worker_id"))
    limit, offset = _paging(request)
    rows, more = await repo.worker_ledger(session, user.account_id, worker.id, limit=limit, offset=offset)
    balance = await repo.worker_balance(session, user.account_id, worker.id)
    waiting = await repo.pending_worker_pays(session, user.account_id, worker.id)
    return web.json_response(
        {
            "worker": {"id": worker.id, "name": worker.name},
            "balance": _balance_json(balance),
            "items": [_ledger_json(r, user) for r in rows],
            "has_more": more,
            "pending": await repo.count_pending_payments(session, user.account_id, worker.id),
            # Tasdiq kutayotgan pul («oldim», «qaytardim») tasdiqlansa qancha qoladi
            "after_pending": _money(repo.with_pending(balance.balance, waiting)) if waiting else None,
        }
    )


@routes.post("/api/payments")
@endpoint()
async def add_payment(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """Tez yozish: {worker_id (egasida), kind: pay|return|balance, amount, worker_owes, whole}.
    Javobda pending — yozuv egasining tasdig'ini kutadimi (ishchi yozganda)."""
    data = await _json(request)
    worker = await _ledger_worker(request, session, user, data.get("worker_id"))
    kind = data.get("kind")
    if kind not in (PayKind.PAY, PayKind.RETURN, PayKind.BALANCE):
        raise ApiError(400, "Noto'g'ri tur")
    if kind == PayKind.BALANCE and not user.is_owner:
        raise ApiError(403, "Hisob holatini boshlig'ingiz yozadi")
    amount = _parse_price(data.get("amount"))
    currency = (await repo.enabled_currencies(session, user.account_id))[0]
    whole = kind == PayKind.PAY and bool(data.get("whole"))
    if kind == PayKind.BALANCE:
        amount = amount or Decimal(0)
    elif not whole and not amount:
        raise ApiError(400, "Summani yozing")
    draft = {
        "section": "work",
        "entry_id": None,
        "date": today().isoformat(),
        "raw_text": "📱 Ilovada yozildi",
        "created": datetime.now(timezone.utc).isoformat(),
        "lines": [],
        "pays": [
            {
                "worker_id": worker.id,
                "worker_name": worker.name,
                "worker_q": worker.name,
                "kind": kind,
                "amount": None if whole else drafts.dec_str(amount),
                "whole": whole,
                "worker_owes": kind == PayKind.BALANCE and bool(data.get("worker_owes")),
                "currency": currency,
                "note": None,
                "date": None,
            }
        ],
    }
    await drafts.resolve(session, draft, user)
    if draft["pays"][0]["amount"] is None:
        raise ApiError(400, "Qoldiq yo'q — summani yozing")
    try:
        saved = await intake.save(session, request.app["bot"], user, draft)
    except intake.IntakeError as e:
        raise ApiError(400, e.message)
    # Ish boshqaruvchi yozgani saqlanmaydi — egasining tasdig'iga boradi (queued)
    pending = saved.queued or not drafts.pay_counted(draft, draft["pays"][0])
    return web.json_response({"saved": _saved_html(saved), "pending": pending, "queued": saved.queued})


async def _payment_for(request: web.Request, session: AsyncSession, user: User) -> WorkerPayment:
    entry = await repo.get_payment(session, user.account_id, int(request.match_info["id"]))
    if entry is None or not (user.is_owner or entry.worker_id == user.id):
        raise ApiError(404, "Yozuv topilmadi")
    return entry


@routes.post(r"/api/payments/{id:\d+}/{action:approve|reject}")
@endpoint(owner_only=True)
async def review_payment(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    from bot.handlers.entries import pay_summary, pay_text

    entry = await _payment_for(request, session, user)
    approve = request.match_info["action"] == "approve"
    await repo.review_payment(session, entry, user, approve)
    worker = entry.worker
    if worker.tg_id and worker.id != user.id:
        verdict = "✅ tasdiqlandi" if approve else "🚫 rad etildi"
        text = f"{pay_text(entry, worker)}\n\n{await pay_summary(session, [entry], worker)}"
        _notify(request, worker.tg_id, f"Yozuvingiz {verdict}:\n\n{text}")
    return web.json_response(_pay_json(entry, user))


async def _ask_delete_payment(session: AsyncSession, manager: User, match: dict, body: dict | None) -> str:
    from bot.handlers.entries import pay_text

    entry = await repo.get_payment(session, manager.account_id, _int_id(match))
    # Boshqaruvchi faqat o'zi yozgan o'z pul yozuvini (oddiy ishchidek)
    if entry is None or entry.worker_id != manager.id:
        raise ApiError(404, "Yozuv topilmadi")
    if entry.created_by != manager.id:
        raise ApiError(403, "Bu yozuvni boshlig'ingiz kiritgan — faqat u o'chira oladi")
    owner = await session.get(User, manager.owner_id)
    return f"🗑 <b>Pul yozuvini o'chirish</b>\n\n{pay_text(entry, owner)}"


@routes.delete(r"/api/payments/{id:\d+}")
@endpoint(manager=_ask_delete_payment)
async def delete_payment(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    from bot.handlers.entries import pay_text

    entry = await _payment_for(request, session, user)
    if not user.is_owner and entry.created_by != user.id:
        raise ApiError(403, "Bu yozuvni boshlig'ingiz kiritgan — faqat u o'chira oladi")
    await repo.soft_delete(session, entry)
    if not user.is_owner and entry.status == EntryStatus.APPROVED:
        owner = await session.get(User, user.owner_id)
        if owner and owner.tg_id:
            _notify(request, owner.tg_id, f"🗑 {escape(user.name)} pul yozuvini o'chirdi:\n\n<s>{pay_text(entry, owner)}</s>")
    return web.json_response({"ok": True})


# --- Hisobni 0 qilish: mijozlar, ishchilar, xom ashyo, xarajatlar — har biri alohida ------

RESET_TABLES = {
    "clients": (ClientEntry,),
    "workers": (WorkEntry, WorkerPayment),
    "materials": (MaterialEntry,),
    "personal": (PersonalEntry,),
}


@routes.post(r"/api/reset/{section:clients|workers|materials|personal}")
@endpoint(owner_only=True)
async def reset_section(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """Ilova ikki marta so'ragach: bo'limning hamma yozuvlari o'chadi, odamlar (mijoz, ishchi, ta'minotchi) qoladi.
    Mijozlarda — «Qarzlar»da ularning ismi bilan qolib ketgan qarzlari ham (mijozlar bilan hisob hamma joyda 0)."""
    section = request.match_info["section"]
    count = 0
    if section == "clients":
        for client in await repo.list_clients(session, user.id, active_only=False):
            for b in await repo.client_outside_debts(session, user.id, client):
                count += await repo.reset_person(session, user.id, b.key)
    count += await repo.reset_entries(session, user.id, RESET_TABLES[section])
    return web.json_response({"ok": True, "count": count})


@routes.post(r"/api/workers/{id:\d+}/reset")
@endpoint(owner_only=True)
async def reset_worker(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """Ishchi oynasidagi tugma: shu ishchining hamma ish va pul yozuvlari o'chadi, qoldig'i 0 — umumiy «Qarzlar»dan
    ham ayriladi. Ishchining o'zi qoladi. removed — o'chgan qoldiq (musbat — egasining qarzi edi, manfiy — avans)."""
    worker = await _get_worker(request, session, user)
    removed = await repo.worker_balance(session, user.id, worker.id)
    count = await repo.reset_entries(session, user.id, RESET_TABLES["workers"], worker_id=worker.id)
    return web.json_response(
        {
            "ok": True,
            "count": count,
            "removed": _money(removed.balance),
            "worker": await _one_worker_json(request, session, worker),
        }
    )


@routes.post(r"/api/clients/{id:\d+}/reset")
@endpoint(owner_only=True)
async def reset_client(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """Mijoz oynasidagi tugma: u bilan hisob hamma joyda 0 — mijozning hamma oldi-berdisi va «Qarzlar»da uning ismi
    bilan qolib ketgan qarzlari («⚠️ «Qarzlar»da ham bor») o'chadi, umumiy «Qarzlar»dan ham ayriladi. Mijoz
    yozuvlari «🗑 O'chirilganlar»da turadi (bittalab qaytarsa bo'ladi). Ulangan mijozga xabar boradi.
    removed — o'chgan qoldiq (musbat — mijoz qarzi edi, manfiy — oldindan to'lagani yoki siz unga qarzdor edingiz);
    outside — «Qarzlar»dan nechta yozuv o'chdi."""
    from bot.handlers.money import client_reset_message  # aylanma importdan qochish uchun

    client = await repo.get_client(session, user.id, int(request.match_info["id"]))
    if client is None:
        raise ApiError(404, "Mijoz topilmadi")
    removed = [await repo.client_balance(session, user.id, client.id)]
    removed += [b.balance for b in await repo.client_outside_debts(session, user.id, client)]
    count, outside = await repo.reset_client_everywhere(session, user.id, client)
    if count and (notice := client_reset_message(user, client)):
        _notify(request, *notice)
    return web.json_response(
        {"ok": True, "count": count + outside, "outside": outside, "removed": _sum_money(removed)}
    )


@routes.post("/api/debts/reset")
@endpoint(owner_only=True)
async def reset_person(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """{key} — «Qarzlar»dagi odam bilan hisob 0: u bilan qarz yozuvlari va undan olingan (ro'yxatga qo'shilmagan) xom
    ashyo o'chadi. Ro'yxatdagi ta'minotchi bo'lsa — undan olingan hamma xom ashyo ham, unga xabar boradi.
    removed — o'chgan qarz (musbat — u qarzdor edi, manfiy — siz unga qarzdor edingiz)."""
    from bot.handlers.money import supplier_reset_message  # aylanma importdan qochish uchun

    key = str((await _json(request)).get("key") or "")
    row = next((b for b in await repo.debt_balances(session, user.id) if b.key == key), None)
    if row is None:
        raise ApiError(404, "Qarz topilmadi")
    supplier = next(
        (s for s in await repo.list_suppliers(session, user.id, active_only=False) if repo.person_key(s.name) == key),
        None,
    )
    count = await repo.reset_person(session, user.id, key, supplier.id if supplier else None)
    if count and supplier and (notice := supplier_reset_message(user, supplier)):
        _notify(request, *notice)
    removed = _money({c: v for c, v in row.balance.items() if v})
    return web.json_response({"ok": True, "count": count, "removed": removed})


@routes.post(r"/api/suppliers/{id:\d+}/reset")
@endpoint(owner_only=True)
async def reset_supplier(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """Ta'minotchi oynasidagi tugma: undan olingan hamma xom ashyo va u bilan qarz yozuvlari o'chadi, qarz 0 —
    umumiy «Qarzlar»dan ham ayriladi. Ta'minotchining o'zi qoladi. removed — o'chgan qarz (manfiy — sizning
    qarzingiz edi, musbat — uning qarzi)."""
    from bot.handlers.money import supplier_reset_message  # aylanma importdan qochish uchun

    supplier = await _get_supplier(request, session, user)
    removed = (await repo.supplier_debt_balances(session, user.id)).get(repo.person_key(supplier.name), {})
    count = await repo.reset_supplier(session, user.id, supplier)
    if count and (notice := supplier_reset_message(user, supplier)):
        _notify(request, *notice)
    return web.json_response(
        {
            "ok": True,
            "count": count,
            "removed": _money(removed),
            "supplier": await _one_supplier_json(request, session, supplier),
        }
    )


# --- Barcha harakatlar: xarajat, daromad, qarz, mijoz, xom ashyo va ish yozuvlari bitta lentada ---

ACTIVITY_KINDS = ("expense", "income", "debt", "client", "material", "work", "pay")
# Ish boshqaruvchiga: egasining pullari (xarajat, daromad, qarz) va ishchilarning puli ko'rinmaydi
MANAGER_ACTIVITY = ("client", "material", "work")
ACTIVITY_MAX = 1000


@routes.get("/api/activity")
@endpoint(owner_only=True, manager=VIEW)
async def activity(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    limit, offset = _paging(request)
    kind = request.query.get("kind") or None
    allowed = ACTIVITY_KINDS if user.is_owner else MANAGER_ACTIVITY
    if kind is not None and kind not in allowed:
        raise ApiError(400, "Noto'g'ri tur")
    if offset > ACTIVITY_MAX:
        return web.json_response({"items": [], "has_more": False})

    def want(*kinds: str) -> bool:
        return any(k in allowed for k in kinds) and (kind is None or kind in kinds)

    # Har jadvaldan kerakli miqdorni olib, sana bo'yicha birlashtiramiz
    acc = user.account_id
    need = offset + limit + 1
    items: list[tuple[date, float, dict]] = []
    if want("expense", "income"):
        for e in await repo.list_personal_entries(session, acc, kind=kind, limit=need):
            items.append((e.entry_date, e.created_at.timestamp(), {"type": e.kind, **_personal_json(e)}))
    if want("debt"):
        for e in await repo.list_debt_entries(session, acc, limit=need):
            items.append((e.entry_date, e.created_at.timestamp(), {"type": "debt", **_debt_json(e)}))
    if want("client"):
        for e in await repo.list_client_entries(session, acc, limit=need):
            items.append((e.entry_date, e.created_at.timestamp(), {"type": "client", **_client_entry_json(e)}))
    if want("material"):
        for e in await repo.list_material_entries(session, acc, limit=need):
            items.append((e.entry_date, e.created_at.timestamp(), {"type": "material", **_material_json(e)}))
    if want("work"):
        for e in await repo.list_work_entries(session, acc, limit=need):
            items.append((e.work_date, e.created_at.timestamp(), {"type": "work", **_entry_json(e)}))
    if want("pay"):
        for e in await repo.list_payments(session, acc, limit=need):
            items.append((e.entry_date, e.created_at.timestamp(), {"type": "pay", **_pay_json(e, user)}))
    items.sort(key=lambda x: (x[0], x[1]), reverse=True)
    page = [x[2] for x in items[offset : offset + limit]]
    return web.json_response({"items": page, "has_more": len(items) > offset + limit})


# --- Ilova ichida yozish: ovoz/matn/rasm -> qoralama -> saqlash ---
# Qoralama bot chatidagidek ishlaydi (bot/intake.py), faqat server xotirasida saqlanadi: hammasi tushunilsa —
# darhol saqlanadi, yetishmagani bo'lsa — so'raladi.

_drafts: dict[int, dict] = {}

AUDIO_EXT = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/aac": "aac", "audio/mpeg": "mp3", "audio/wav": "wav"}
MAX_AUDIO_BYTES = 8 * 1024**2
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
# Claude rasmni base64 holida 5 MB gacha qabul qiladi (ilova kattasini o'zi kichraytirib yuboradi)
MAX_IMAGE_BYTES = 3_750_000


def _current_draft(user: User) -> dict | None:
    draft = _drafts.get(user.id)
    if draft is not None and drafts.is_stale(draft):
        _drafts.pop(user.id, None)
        return None
    return draft


def _saved_html(saved: intake.Saved) -> str:
    return "\n\n".join([saved.header, *(text for text, _ in saved.cards)])


def _buttons(kb: InlineKeyboardMarkup) -> list[list[dict]]:
    return [[{"text": b.text, "data": b.callback_data} for b in row if b.callback_data] for row in kb.inline_keyboard]


async def _input_state(session: AsyncSession, user: User, **extra) -> web.Response:
    """Ilovaga javob: joriy qoralama (matn + tugmalar) va qo'shimcha xabarlar."""
    data: dict = {"draft": None, **extra}
    draft = _current_draft(user)
    if draft is not None:
        html, kb = await drafts.present(session, draft, user)
        data["draft"] = {"html": html, "buttons": _buttons(kb)}
    return web.json_response(data)


async def _analyze(
    request: web.Request,
    session: AsyncSession,
    user: User,
    text: str,
    heard: bool = False,
    worker: User | None = None,
) -> web.Response:
    text = text.strip()[:2000]
    if not text:
        raise ApiError(400, "Matn bo'sh")
    extra = {"heard": text} if heard else {}
    try:
        draft = await intake.analyze(session, user, text, _current_draft(user), worker)
        if await intake.autosave(session, user, draft):
            saved = await intake.save_auto(session, request.app["bot"], user, draft)
            _drafts.pop(user.id, None)
            return await _input_state(session, user, saved=_saved_html(saved), queued=saved.queued, **extra)
    except intake.IntakeError as e:
        if e.drop:
            _drafts.pop(user.id, None)
        return await _input_state(session, user, notice=e.message, **extra)
    _drafts[user.id] = draft
    return await _input_state(session, user, **extra)


@routes.get("/api/input")
@endpoint()
async def input_state(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    return await _input_state(session, user)


@routes.post("/api/input")
@endpoint()
async def input_text(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """{text, new, worker_id, heard}. new — eski qoralamani tuzatmasdan yangisini boshlash; worker_id — egasi shu
    ishchining hisob-kitob oynasida yozdi (ismi aytilmagan pul va ish shu ishchiga); heard — matn ovozdan
    eshitilgan (/api/input/voice?only=heard), javobda yana ko'rsatiladi."""
    data = await _json(request)
    if data.get("new"):
        _drafts.pop(user.id, None)
    worker = None
    if user.is_owner and data.get("worker_id") is not None:
        worker = await _ledger_worker(request, session, user, data["worker_id"])
    return await _analyze(
        request, session, user, str(data.get("text") or ""), heard=bool(data.get("heard")), worker=worker
    )


@routes.post("/api/input/voice")
@endpoint()
async def input_voice(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """Ovozli xabar. ?only=heard — faqat matnga aylantiriladi: ilova eshitilganini darhol ko'rsatadi va
    tahlilni (Claude) /api/input dan alohida so'raydi. Busiz (eski ilova) — tahlil ham shu so'rovda."""
    ext = AUDIO_EXT.get(request.content_type)
    if ext is None:
        raise ApiError(400, "Ovoz formati qo'llab-quvvatlanmaydi")
    audio = await request.read()
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise ApiError(400, "Ovoz juda uzun yoki bo'sh")
    try:
        text = await stt.transcribe(audio, f"voice.{ext}", request.content_type)
    except stt.SttError as e:
        notice = e.notice or "⚠️ Ovozni matnga aylantirib bo'lmadi. Qayta ayting yoki yozing."
        return await _input_state(session, user, notice=notice)
    if not text:
        return await _input_state(session, user, notice="🤷 Ovozda gap eshitilmadi. Qayta ayting.")
    if request.query.get("only") == "heard":
        return web.json_response({"heard": text})
    return await _analyze(request, session, user, text, heard=True)


@routes.post("/api/input/image")
@endpoint()
async def input_image(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """Bank skrinshoti, chek yoki daftar rasmi (bir nechta bo'lishi mumkin) — multipart: image, caption."""
    form = await request.post()
    images = []
    for field in form.getall("image", []):
        if not isinstance(field, web.FileField) or field.content_type not in IMAGE_TYPES:
            raise ApiError(400, "Rasm formati qo'llab-quvvatlanmaydi (JPG, PNG yoki WEBP bo'lsin)")
        data = field.file.read()
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ApiError(400, "Rasm juda katta yoki bo'sh")
        images.append((data, field.content_type))
    if not images:
        raise ApiError(400, "Rasm tanlanmagan")
    if len(images) > intake.MAX_IMAGES:
        raise ApiError(400, f"Bir martada {intake.MAX_IMAGES} tagacha rasm yuboring")
    try:
        draft = await intake.analyze_images(session, user, images, str(form.get("caption") or ""))
        if await intake.autosave(session, user, draft):
            saved = await intake.save(session, request.app["bot"], user, draft)
            _drafts.pop(user.id, None)
            return await _input_state(session, user, saved=_saved_html(saved), queued=saved.queued)
    except intake.IntakeError as e:
        return await _input_state(session, user, notice=e.message)
    _drafts[user.id] = draft
    return await _input_state(session, user)


@routes.post("/api/input/action")
@endpoint()
async def input_action(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """Qoralama tugmasi: bot chatidagi callback_data qiymatining o'zi keladi."""
    value = str((await _json(request)).get("data") or "")
    draft = _current_draft(user)
    if value == kbs.DraftCb(action="cancel").pack():
        _drafts.pop(user.id, None)
        return await _input_state(session, user, notice="❌ Bekor qilindi.")
    if draft is None:
        return await _input_state(session, user, notice="Bu qoralama eskirgan. Qaytadan ayting.")

    try:
        prefix = value.split(":", 1)[0]
        # So'ralgan narsaga javob berildi: endi hammasi to'liq bo'lsa — darhol saqlanadi
        answered = False
        if prefix == kbs.ResolveCb.__prefix__:
            answered = await intake.choose(session, user, draft, kbs.ResolveCb.unpack(value))
        elif prefix == kbs.DebtDirCb.__prefix__:
            answered = intake.set_direction(draft, kbs.DebtDirCb.unpack(value))
        elif prefix == kbs.ClientKindCb.__prefix__:
            answered = intake.set_client_kind(draft, kbs.ClientKindCb.unpack(value))
        elif prefix == kbs.PayKindCb.__prefix__:
            if answered := intake.set_pay_kind(draft, kbs.PayKindCb.unpack(value)):
                await drafts.resolve(session, draft, user)
        elif prefix == kbs.DraftCb.__prefix__:
            action = kbs.DraftCb.unpack(value).action
            if action in ("today", "yesterday"):
                if not intake.set_date(draft, action):
                    return await _input_state(session, user, notice="Sana o'zgarmadi.")
            elif action == "asdebt":
                if answered := intake.pays_to_debt(draft, user):
                    await drafts.resolve(session, draft, user)
            elif action == "save":
                saved = await intake.save(session, request.app["bot"], user, draft)
                _drafts.pop(user.id, None)
                return await _input_state(session, user, saved=_saved_html(saved), queued=saved.queued)
        if answered and await intake.autosave(session, user, draft):
            saved = await intake.save_auto(session, request.app["bot"], user, draft)
            _drafts.pop(user.id, None)
            return await _input_state(session, user, saved=_saved_html(saved), queued=saved.queued)
    except (ValueError, TypeError):
        raise ApiError(400, "Noto'g'ri tugma")
    except intake.IntakeError as e:
        if e.drop:
            _drafts.pop(user.id, None)
        return await _input_state(session, user, notice=e.message)
    return await _input_state(session, user)


# --- Ish boshqaruvchining so'rovlari: egasi tasdiqlaydi yoki rad etadi ------------------

def _request_json(req: ManagerRequest, viewer: User) -> dict:
    from bot import approvals  # aylanma importdan qochish uchun

    pending = req.status == RequestStatus.PENDING
    return {
        "id": req.id,
        "manager": {"id": req.manager_id, "name": req.manager.name},
        "summary": req.summary,
        "status": req.status,
        "status_label": approvals.status_label(req, viewer.is_owner),
        "result": req.result,
        "created_at": _iso(req.created_at),
        "reviewed_at": _iso(req.reviewed_at),
        "can_review": pending and viewer.is_owner,
        "can_cancel": pending and viewer.id == req.manager_id,
    }


@routes.get("/api/requests")
@endpoint()
async def requests_list(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """Egasiga — boshqaruvchilarning so'rovlari, boshqaruvchiga — o'ziniki (yangisi birinchi).
    ?status=pending — faqat tasdiq kutayotganlar."""
    if not user.manages:
        raise _forbidden()
    limit, offset = _paging(request)
    status = request.query.get("status") or None
    manager_id = None if user.is_owner else user.id
    rows = await repo.list_requests(
        session, user.account_id, manager_id=manager_id, status=status, limit=limit + 1, offset=offset
    )
    return web.json_response(
        {
            "items": [_request_json(r, user) for r in rows[:limit]],
            "has_more": len(rows) > limit,
            "pending": await repo.count_requests(session, user.account_id, manager_id),
        }
    )


@routes.post(r"/api/requests/{id:\d+}/{action:approve|reject|cancel}")
@endpoint()
async def request_action(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    """Egasi: approve (egasi nomidan bajariladi) yoki reject. Boshqaruvchi: cancel — o'z so'rovini qaytarib oladi.
    detail — tasdiqlanganda natija (ishchi, mijozga xabar borgani)."""
    from bot import approvals

    req = await repo.get_request(session, user.account_id, int(request.match_info["id"]))
    if req is None or not (user.is_owner or req.manager_id == user.id):
        raise ApiError(404, "So'rov topilmadi")
    action = request.match_info["action"]
    if action not in ({"approve", "reject"} if user.is_owner else {"cancel"}):
        raise _forbidden()
    detail = None
    try:
        if action == "cancel":
            await approvals.cancel(session, req)
        else:
            detail = await approvals.review(session, request.app["bot"], user, req, action == "approve")
    except approvals.RequestDone:
        label = approvals.STATUS_LABELS.get(req.status, req.status)
        raise ApiError(409, f"Bu so'rov allaqachon ko'rib chiqilgan: {label}")
    return web.json_response({"request": _request_json(req, user), "detail": detail})
