"""Mini App uchun JSON API. Har bir so'rov Telegram initData bilan tasdiqlanadi."""
import asyncio
import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Awaitable, Callable

from aiogram import Bot
from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db import repo
from bot.db.base import session_factory
from bot.db.models import EntryStatus, ItemModel, Role, User, WorkEntry
from bot.services.utils import today
from bot.web.auth import validate_init_data

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
                return response

        return inner

    return wrap


async def _auth(request: web.Request, session: AsyncSession) -> User:
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    tg_user = validate_init_data(init_data, settings.bot_token) if init_data else None
    if tg_user is None and settings.webapp_dev:
        tg_user = {"id": settings.owner_ids[0], "first_name": "Egasi"}
    if tg_user is None:
        raise ApiError(401, "Ilovani Telegram ichida oching")

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

    chart_start, chart_end, bucket = b["chart"]
    lo = min(b["start"], b["prev"][0], chart_start)
    hi = max(b["end"], chart_end)
    all_rows = await repo.work_stat_rows(session, lo, hi, worker_id)

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
        "pending": await repo.count_pending(session, worker_id),
    }
    if user.is_owner:
        data["by_worker"] = _group(cur, "worker_id")
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
            "currencies": await repo.enabled_currencies(session),
            "require_approval": await repo.require_approval(session),
        }
    )


@routes.patch("/api/settings")
@endpoint(owner_only=True)
async def update_settings(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    data = await _json(request)
    if "require_approval" in data:
        await repo.set_setting(session, "require_approval", "1" if data["require_approval"] else "0")
    if "currencies" in data:
        cur = [c for c in repo.ALL_CURRENCIES if c in (data["currencies"] or [])]
        if not cur:
            raise ApiError(400, "Kamida bitta valyuta yoqilgan bo'lishi kerak")
        await repo.set_setting(session, "currencies", ",".join(cur))
    return web.json_response(
        {
            "currencies": await repo.enabled_currencies(session),
            "require_approval": await repo.require_approval(session),
        }
    )


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
    entry = await repo.get_work_entry(session, int(request.match_info["id"]))
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
    rows = await repo.list_work_entries(session, status=status, worker_id=worker_id, limit=limit + 1, offset=offset)
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
    await repo.soft_delete_work_entry(session, entry)
    if not user.is_owner and entry.status == EntryStatus.APPROVED:
        for owner in await repo.get_owners(session):
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


async def _month_stats(session: AsyncSession) -> dict[int, dict]:
    t = today()
    rows = await repo.work_stat_rows(session, t.replace(day=1), t)
    return {g["id"]: g for g in _group(rows, "worker_id")}


@routes.get("/api/workers")
@endpoint(owner_only=True)
async def workers(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    stats = await _month_stats(session)
    items = [await _worker_json(request, w, stats) for w in await repo.list_workers(session, active_only=False)]
    items.sort(key=lambda w: (not w["is_active"], -w["month_qty"], w["name"].lower()))
    return web.json_response({"items": items})


@routes.post("/api/workers")
@endpoint(owner_only=True)
async def add_worker(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    name = str((await _json(request)).get("name") or "").strip()[:100]
    if not name:
        raise ApiError(400, "Ism bo'sh bo'lmasin")
    worker = await repo.create_worker(session, name)
    return web.json_response(await _worker_json(request, worker, {}))


async def _get_worker(request: web.Request, session: AsyncSession) -> User:
    worker = await session.get(User, int(request.match_info["id"]))
    if worker is None or worker.role != Role.WORKER:
        raise ApiError(404, "Ishchi topilmadi")
    return worker


@routes.post(r"/api/workers/{id:\d+}/toggle")
@endpoint(owner_only=True)
async def toggle_worker(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    worker = await _get_worker(request, session)
    worker.is_active = not worker.is_active
    await session.flush()
    return web.json_response(await _worker_json(request, worker, await _month_stats(session)))


@routes.post(r"/api/workers/{id:\d+}/invite")
@endpoint(owner_only=True)
async def reinvite_worker(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    worker = await _get_worker(request, session)
    if not worker.is_active:
        raise ApiError(400, "Avval ishchini faollashtiring")
    await repo.new_invite_token(session, worker)
    # Yangi havola: avvalgi Telegram bog'lanishi saqlanadi, faqat tg_id bo'lmasa ko'rinadi
    data = await _worker_json(request, worker, await _month_stats(session))
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
        existing = await repo.get_model_by_name(session, name)
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
    items = await repo.list_models(session, active_only=not user.is_owner)
    return web.json_response({"items": [_model_json(m) for m in items]})


@routes.post("/api/models")
@endpoint(owner_only=True)
async def add_model(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    data = await _json(request)
    currencies = await repo.enabled_currencies(session)
    model = ItemModel(name="", currency=currencies[0])
    data.setdefault("name", "")
    await _apply_model(session, model, data)
    session.add(model)
    await session.flush()
    return web.json_response(_model_json(model))


@routes.patch(r"/api/models/{id:\d+}")
@endpoint(owner_only=True)
async def update_model(request: web.Request, session: AsyncSession, user: User) -> web.Response:
    model = await session.get(ItemModel, int(request.match_info["id"]))
    if model is None:
        raise ApiError(404, "Model topilmadi")
    await _apply_model(session, model, await _json(request))
    await session.flush()
    return web.json_response(_model_json(model))
