"""Mini App uchun JSON API. Har bir so'rov Telegram initData bilan tasdiqlanadi."""
import asyncio
import logging
import time
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Awaitable, Callable

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup
from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession

from bot import advisor, drafts, intake
from bot import keyboards as kbs
from bot.config import settings
from bot.db import repo
from bot.db.base import session_factory
from bot.db.models import (
    ClientEntry,
    DebtEntry,
    EntryStatus,
    ItemModel,
    PersonalEntry,
    PersonalKind,
    User,
    WorkEntry,
)
from bot.services import llm, stt
from bot.services.utils import today
from bot.web.auth import (
    WEB_TOKEN_DAYS,
    make_web_token,
    validate_init_data,
    validate_web_token,
    web_token_expiry,
)

log = logging.getLogger(__name__)

routes = web.RouteTableDef()

Handler = Callable[[web.Request, AsyncSession, User], Awaitable[web.StreamResponse]]


class ApiError(web.HTTPException):
    def __init__(self, status: int, message: str):
        self.status_code = status
        super().__init__(text=web.json_response({"error": message}).text, content_type="application/json")


def _forbidden() -> ApiError:
    return ApiError(403, "Ruxsat yo'q")


def endpoint(owner_only: bool = False):
    """Sessiya ochadi, foydalanuvchini aniqlaydi, oxirida commit qiladi."""

    def wrap(fn: Handler):
        async def inner(request: web.Request) -> web.StreamResponse:
            async with session_factory() as session:
                user = await _auth(request, session)
                if owner_only and not user.is_owner:
                    raise _forbidden()
                response = await fn(request, session, user)
                await session.commit()
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


def _notify(request: web.Request, chat_id: int, text: str) -> None:
    """Telegram xabarini fonda yuboradi — ilova javobni kutib qolmasin."""

    async def send() -> None:
        try:
            await request.app["bot"].send_message(chat_id, text)
        except Exception:
            log.warning("Xabar yuborilmadi (chat %s)", chat_id, exc_info=True)

    task = asyncio.create_task(send())
    _background.add(task)
    task.add_done_callback(_background.discard)


def _num(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


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
    worker_id = None if user.is_owner else user.id
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
    if user.is_owner:
        data["by_worker"] = _group(cur, "worker_id")
        data["has_team"] = await repo.has_work(session, acc)
    return web.json_response(data)


# --- Foydalanuvchi va sozlamalar --------------------------------------------

@routes.get("/api/me")
@endpoint()
async def me(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    bot: Bot = request.app["bot"]
    bot_me = await bot.me()
    return web.json_response(
        {
            "id": user.id,
            "name": user.name,
            "role": user.role,
            "is_owner": user.is_owner,
            "bot_username": bot_me.username,
            "currencies": await repo.enabled_currencies(session, user.account_id),
            "require_approval": await repo.require_approval(session, user.account_id),
            "set_size": await repo.set_size(session, user.account_id),
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
            "set_size": await repo.set_size(session, user.id),
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

def _entry_json(e: WorkEntry) -> dict:
    total = e.unit_price * e.quantity if e.unit_price is not None else None
    return {
        "id": e.id,
        "date": e.work_date.isoformat(),
        "worker": {"id": e.worker_id, "name": e.worker.name},
        "model": e.model.name,
        "qty": e.quantity,
        "unit_price": _num(e.unit_price),
        "currency": e.price_currency,
        "total": _num(total),
        "status": e.status,
        "raw_text": e.raw_text,
    }


async def _entry_for(request: web.Request, session: AsyncSession, user: User) -> WorkEntry:
    entry = await repo.get_work_entry(session, user.account_id, int(request.match_info["id"]))
    if entry is None or not (user.is_owner or entry.worker_id == user.id):
        raise ApiError(404, "Yozuv topilmadi")
    return entry


@routes.get("/api/entries")
@endpoint()
async def entries(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    q = request.query
    status = q.get("status") or None
    if status not in (None, EntryStatus.PENDING, EntryStatus.APPROVED, EntryStatus.REJECTED):
        raise ApiError(400, "Noto'g'ri holat")
    worker_id = user.id if not user.is_owner else (int(q["worker_id"]) if q.get("worker_id") else None)
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


@routes.delete(r"/api/entries/{id:\d+}")
@endpoint()
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

async def _worker_json(request: web.Request, w: User, stats: dict[int, dict]) -> dict:
    link = None
    if w.is_active and not w.tg_id and w.invite_token:
        bot_me = await request.app["bot"].me()
        link = f"https://t.me/{bot_me.username}?start=inv_{w.invite_token}"
    s = stats.get(w.id, {"qty": 0, "pay": {}})
    return {
        "id": w.id,
        "name": w.name,
        "is_active": w.is_active,
        "linked": w.tg_id is not None,
        "invite_link": link,
        "month_qty": s["qty"],
        "month_pay": s["pay"],
    }


async def _month_stats(session: AsyncSession, owner_id: int) -> dict[int, dict]:
    t = today()
    rows = await repo.work_stat_rows(session, owner_id, t.replace(day=1), t)
    return {g["id"]: g for g in _group(rows, "worker_id")}


@routes.get("/api/workers")
@endpoint(owner_only=True)
async def workers(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    stats = await _month_stats(session, user.id)
    workers_ = await repo.list_workers(session, user.id, active_only=False)
    items = [await _worker_json(request, w, stats) for w in workers_]
    items.sort(key=lambda w: (not w["is_active"], -w["month_qty"], w["name"].lower()))
    return web.json_response({"items": items})


@routes.post("/api/workers")
@endpoint(owner_only=True)
async def add_worker(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    name = str((await _json(request)).get("name") or "").strip()[:100]
    if not name:
        raise ApiError(400, "Ism bo'sh bo'lmasin")
    worker = await repo.create_worker(session, user.id, name)
    return web.json_response(await _worker_json(request, worker, {}))


async def _get_worker(request: web.Request, session: AsyncSession, user: User) -> User:
    worker = await repo.get_worker(session, user.id, int(request.match_info["id"]))
    if worker is None:
        raise ApiError(404, "Ishchi topilmadi")
    return worker


@routes.post(r"/api/workers/{id:\d+}/toggle")
@endpoint(owner_only=True)
async def toggle_worker(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    worker = await _get_worker(request, session, user)
    worker.is_active = not worker.is_active
    await session.flush()
    return web.json_response(await _worker_json(request, worker, await _month_stats(session, user.id)))


@routes.post(r"/api/workers/{id:\d+}/invite")
@endpoint(owner_only=True)
async def reinvite_worker(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    worker = await _get_worker(request, session, user)
    if not worker.is_active:
        raise ApiError(400, "Avval ishchini faollashtiring")
    await repo.new_invite_token(session, worker)
    # Yangi havola: avvalgi Telegram bog'lanishi saqlanadi, faqat tg_id bo'lmasa ko'rinadi
    data = await _worker_json(request, worker, await _month_stats(session, user.id))
    if data["invite_link"] is None:
        bot_me = await request.app["bot"].me()
        data["invite_link"] = f"https://t.me/{bot_me.username}?start=inv_{worker.invite_token}"
    return web.json_response(data)


# --- Modellar ---------------------------------------------------------------

def _model_json(m: ItemModel) -> dict:
    return {"id": m.id, "name": m.name, "price": _num(m.price), "currency": m.currency, "is_active": m.is_active}


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


async def _apply_model(session: AsyncSession, model: ItemModel, data: dict) -> None:
    if "name" in data:
        name = str(data["name"] or "").strip()[:100]
        if not name:
            raise ApiError(400, "Nom bo'sh bo'lmasin")
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


@routes.get("/api/models")
@endpoint()
async def models(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    items = await repo.list_models(session, user.account_id, active_only=not user.is_owner)
    return web.json_response({"items": [_model_json(m) for m in items]})


@routes.post("/api/models")
@endpoint(owner_only=True)
async def add_model(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    data = await _json(request)
    currencies = await repo.enabled_currencies(session, user.id)
    model = ItemModel(owner_id=user.id, name="", currency=currencies[0])
    data.setdefault("name", "")
    await _apply_model(session, model, data)
    session.add(model)
    await session.flush()
    return web.json_response(_model_json(model))


@routes.patch(r"/api/models/{id:\d+}")
@endpoint(owner_only=True)
async def update_model(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    model = await repo.get_model(session, user.id, int(request.match_info["id"]))
    if model is None:
        raise ApiError(404, "Model topilmadi")
    await _apply_model(session, model, await _json(request))
    await session.flush()
    return web.json_response(_model_json(model))


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


@routes.get("/api/debts")
@endpoint(owner_only=True)
async def debts(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    balances = await repo.debt_balances(session, user.id)
    owed_to_me, i_owe = repo.debt_totals(balances)
    return web.json_response(
        {
            "people": [
                {
                    "key": b.key,
                    "name": b.person,
                    "balance": _money({c: v for c, v in b.balance.items() if v}),
                    "last_date": b.last_date.isoformat() if b.last_date else None,
                    "count": b.count,
                    "settled": b.settled,
                }
                for b in balances
            ],
            "owed_to_me": _money(owed_to_me),
            "i_owe": _money(i_owe),
        }
    )


@routes.get("/api/debts/entries")
@endpoint(owner_only=True)
async def debt_entries(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    limit, offset = _paging(request)
    key = request.query.get("key") or None
    rows = await repo.list_debt_entries(session, user.id, key=key, limit=limit + 1, offset=offset)
    return web.json_response({"items": [_debt_json(e) for e in rows[:limit]], "has_more": len(rows) > limit})


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
    }


def _client_json(st: repo.ClientStats) -> dict:
    c = st.client
    return {
        "id": c.id,
        "name": c.name,
        "phone": c.phone,
        "is_active": c.is_active,
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
@endpoint(owner_only=True)
async def clients(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    period = request.query.get("period", "month")
    if period not in PERIODS:
        raise ApiError(400, "Noto'g'ri davr")
    t = today()
    b = _period_bounds(period, t)
    stats = await repo.client_stats(session, user.id, b["start"], b["end"])
    return web.json_response(
        {
            "period": period,
            "start": b["start"].isoformat(),
            "end": b["end"].isoformat(),
            "today": t.isoformat(),
            "set_size": await repo.set_size(session, user.id),
            "items": [_client_json(st) for st in stats],
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


@routes.post("/api/clients")
@endpoint(owner_only=True)
async def add_client(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    name, phone = _client_fields(await _json(request))
    if not name:
        raise ApiError(400, "Ismini kiriting")
    if await repo.get_client_by_name(session, user.id, name):
        raise ApiError(400, "Bunday mijoz bor")
    client = await repo.create_client(session, user.id, name, phone)
    return web.json_response(_client_json(repo.ClientStats(client=client)))


@routes.patch(r"/api/clients/{id:\d+}")
@endpoint(owner_only=True)
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


@routes.get("/api/clients/entries")
@endpoint(owner_only=True)
async def client_entries(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    limit, offset = _paging(request)
    client_id = request.query.get("client_id")
    rows = await repo.list_client_entries(
        session, user.id, client_id=int(client_id) if client_id and client_id.isdigit() else None,
        limit=limit + 1, offset=offset,
    )
    return web.json_response(
        {"items": [_client_entry_json(e) for e in rows[:limit]], "has_more": len(rows) > limit}
    )


@routes.delete(r"/api/clients/entries/{id:\d+}")
@endpoint(owner_only=True)
async def delete_client_entry(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    entry = await repo.get_client_entry(session, user.id, int(request.match_info["id"]))
    if entry is None:
        raise ApiError(404, "Yozuv topilmadi")
    await repo.soft_delete(session, entry)
    return web.json_response({"ok": True})


# --- Barcha harakatlar: xarajat, daromad, qarz, mijoz va ish yozuvlari bitta lentada ---

ACTIVITY_KINDS = ("expense", "income", "debt", "client", "work")
ACTIVITY_MAX = 1000


@routes.get("/api/activity")
@endpoint(owner_only=True)
async def activity(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    limit, offset = _paging(request)
    kind = request.query.get("kind") or None
    if kind is not None and kind not in ACTIVITY_KINDS:
        raise ApiError(400, "Noto'g'ri tur")
    if offset > ACTIVITY_MAX:
        return web.json_response({"items": [], "has_more": False})

    # Har jadvaldan kerakli miqdorni olib, sana bo'yicha birlashtiramiz
    need = offset + limit + 1
    items: list[tuple[date, float, dict]] = []
    if kind in (None, "expense", "income"):
        for e in await repo.list_personal_entries(session, user.id, kind=kind, limit=need):
            items.append((e.entry_date, e.created_at.timestamp(), {"type": e.kind, **_personal_json(e)}))
    if kind in (None, "debt"):
        for e in await repo.list_debt_entries(session, user.id, limit=need):
            items.append((e.entry_date, e.created_at.timestamp(), {"type": "debt", **_debt_json(e)}))
    if kind in (None, "client"):
        for e in await repo.list_client_entries(session, user.id, limit=need):
            items.append((e.entry_date, e.created_at.timestamp(), {"type": "client", **_client_entry_json(e)}))
    if kind in (None, "work"):
        for e in await repo.list_work_entries(session, user.id, limit=need):
            items.append((e.work_date, e.created_at.timestamp(), {"type": "work", **_entry_json(e)}))
    items.sort(key=lambda x: (x[0], x[1]), reverse=True)
    page = [x[2] for x in items[offset : offset + limit]]
    return web.json_response({"items": page, "has_more": len(items) > offset + limit})


# --- Ilova ichida yozish: ovoz/matn/rasm -> qoralama -> «To'g'rimi?» -> saqlash ---
# Qoralama bot chatidagidek ishlaydi (bot/intake.py), faqat server xotirasida saqlanadi.

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


async def _analyze(session: AsyncSession, user: User, text: str, heard: bool = False) -> web.Response:
    text = text.strip()[:2000]
    if not text:
        raise ApiError(400, "Matn bo'sh")
    extra = {"heard": text} if heard else {}
    try:
        _drafts[user.id] = await intake.analyze(session, user, text, _current_draft(user))
    except intake.IntakeError as e:
        return await _input_state(session, user, notice=e.message, **extra)
    return await _input_state(session, user, **extra)


@routes.get("/api/input")
@endpoint()
async def input_state(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    return await _input_state(session, user)


@routes.post("/api/input")
@endpoint()
async def input_text(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    return await _analyze(session, user, str((await _json(request)).get("text") or ""))


@routes.post("/api/input/voice")
@endpoint()
async def input_voice(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    ext = AUDIO_EXT.get(request.content_type)
    if ext is None:
        raise ApiError(400, "Ovoz formati qo'llab-quvvatlanmaydi")
    audio = await request.read()
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise ApiError(400, "Ovoz juda uzun yoki bo'sh")
    try:
        text = await stt.transcribe(audio, f"voice.{ext}", request.content_type)
    except stt.SttError:
        return await _input_state(session, user, notice="⚠️ Ovozni matnga aylantirib bo'lmadi. Qayta ayting yoki yozing.")
    if not text:
        return await _input_state(session, user, notice="🤷 Ovozda gap eshitilmadi. Qayta ayting.")
    return await _analyze(session, user, text, heard=True)


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
            return await _input_state(session, user, saved="\n\n".join([saved.header, *(text for text, _ in saved.cards)]))
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
        if prefix == kbs.ResolveCb.__prefix__:
            await intake.choose(session, user, draft, kbs.ResolveCb.unpack(value))
        elif prefix == kbs.DebtDirCb.__prefix__:
            intake.set_direction(draft, kbs.DebtDirCb.unpack(value))
        elif prefix == kbs.ClientKindCb.__prefix__:
            intake.set_client_kind(draft, kbs.ClientKindCb.unpack(value))
        elif prefix == kbs.DraftCb.__prefix__:
            action = kbs.DraftCb.unpack(value).action
            if action in ("today", "yesterday"):
                if not intake.set_date(draft, action):
                    return await _input_state(session, user, notice="Sana o'zgarmadi.")
            elif action == "save":
                saved = await intake.save(session, request.app["bot"], user, draft)
                _drafts.pop(user.id, None)
                html = "\n\n".join([saved.header, *(text for text, _ in saved.cards)])
                return await _input_state(session, user, saved=html)
    except (ValueError, TypeError):
        raise ApiError(400, "Noto'g'ri tugma")
    except intake.IntakeError as e:
        if e.drop:
            _drafts.pop(user.id, None)
        return await _input_state(session, user, notice=e.message)
    return await _input_state(session, user)
