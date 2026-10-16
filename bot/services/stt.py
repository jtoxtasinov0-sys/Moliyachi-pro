"""Ovozli xabarni matnga aylantirish: Aisha (VoiceLab), ishlamasa — ElevenLabs Scribe (kaliti bo'lsa)."""
import asyncio
import logging
import time
import uuid

import aiohttp

from bot.config import settings

log = logging.getLogger(__name__)

# Aisha o'zbek tiliga ixtisoslashgan. Ovoz navbatga qo'yiladi va tayyor bo'lguncha so'rab turiladi:
# dastlabki 5 soniya har yarim soniyada, keyin har soniyada (so'rovlar cheklovidan oshmaslik uchun).
AISHA_URL = "https://api.voicelab.uz/v1/stt"
AISHA_TRANSCRIPTIONS_URL = "https://api.voicelab.uz/v1/stt/transcriptions"
AISHA_FAST_POLLS = 10
AISHA_FAST_POLL_SECONDS = 0.5
AISHA_POLL_SECONDS = 1.0
AISHA_WAIT_SECONDS = 60

# Vaqtinchalik xatolarda (cheklov, server band) so'rov qayta yuboriladi. Ovoz yuborish ham qayta
# yuborilishi mumkin: bir xil Idempotency-Key bilan Aisha ovozni ikkinchi marta hisoblamaydi.
RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRIES = 2
RETRY_MAX_SECONDS = 5.0

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
STT_MODEL = "scribe_v2"

# Scribe'ni shu so'zlarga moyil qiladi: sonlar to'g'ri eshitilsin («sakson besh ming»
# «salkam besh ming» bo'lib qolmasin). Narxi +20%. 100 tadan oshmasin — undan ko'pida
# har bir so'rov kamida 20 soniya deb hisoblanadi.
_UNITS = ["bir", "ikki", "uch", "to'rt", "besh", "olti", "yetti", "sakkiz", "to'qqiz"]
_TENS = ["o'n", "yigirma", "o'ttiz", "qirq", "ellik", "oltmish", "yetmish", "sakson", "to'qson"]
KEYTERMS = [
    *_UNITS,
    *_TENS,
    "yuz", "ming", "million", "milliard", "yarim",
    *(f"{t} ming" for t in _TENS),
    "so'm", "dollar", "von",
]


class SttError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def notice(self) -> str | None:
        """Qayta yuborish yordam bermaydigan holatlarda foydalanuvchiga aniq sabab."""
        if self.status == 402:
            return "⚠️ Ovoz xizmatining balansi tugagan — hozircha matn bilan yozing."
        if self.status in (401, 403):
            return "⚠️ Ovoz xizmati kaliti ishlamayapti — hozircha matn bilan yozing."
        return None


async def transcribe(audio: bytes, filename: str = "voice.ogg", content_type: str = "audio/ogg") -> str:
    error = SttError("AISHA_API_KEY ham, ELEVENLABS_API_KEY ham sozlanmagan")
    if settings.aisha_api_key:
        try:
            return await _aisha(audio, filename, content_type)
        except SttError as e:
            error = e
            log.error("Aisha STT xatosi: %s%s", e, " — ElevenLabs'ga o'tilmoqda" if settings.elevenlabs_api_key else "")
    if settings.elevenlabs_api_key:
        try:
            return await _elevenlabs(audio, filename, content_type)
        except SttError as e:
            error = e
            log.error("ElevenLabs STT xatosi: %s", e)
    elif not settings.aisha_api_key:
        log.error("STT: %s", error)
    raise error


# --- Aisha (VoiceLab) -------------------------------------------------------

async def _aisha(audio: bytes, filename: str, content_type: str) -> str:
    def form() -> aiohttp.FormData:
        # FormData bir marta yuboriladi — qayta urinishda yangisi kerak
        data = aiohttp.FormData()
        data.add_field("audio", audio, filename=filename, content_type=content_type)
        data.add_field("language", "uz")
        data.add_field("include_speakers", "false")
        return data

    headers = {"Authorization": f"Bearer {settings.aisha_api_key}", "Accept": "application/json"}
    idempotency = {"Idempotency-Key": str(uuid.uuid4())}
    timeout = aiohttp.ClientTimeout(total=60)
    started = time.monotonic()
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as http:
            data = await _aisha_json(lambda: http.post(AISHA_URL, data=form(), headers=idempotency))
            uploaded = time.monotonic()
            polls = 0
            while True:
                status = data.get("status")
                if status == "failed":
                    error = data.get("error") or {}
                    raise SttError(f"{error.get('code') or 'failed'} {error.get('message') or ''}".strip())
                if status == "completed" or (status is None and data.get("transcript") is not None):
                    done = time.monotonic()
                    log.info(
                        "Aisha: %.1f s (yuborish %.1f s, kutish %.1f s, %d marta so'raldi)",
                        done - started, uploaded - started, done - uploaded, polls,
                    )
                    return (data.get("transcript") or "").strip()
                if time.monotonic() - uploaded > AISHA_WAIT_SECONDS:
                    raise SttError(f"javob kutish vaqti tugadi (holat: {status})")
                await asyncio.sleep(AISHA_FAST_POLL_SECONDS if polls < AISHA_FAST_POLLS else AISHA_POLL_SECONDS)
                polls += 1
                url = f"{AISHA_TRANSCRIPTIONS_URL}/{data['id']}"
                data = await _aisha_json(lambda: http.get(url))
    except (aiohttp.ClientError, TimeoutError, KeyError, ValueError) as e:
        raise SttError(repr(e)) from e


async def _aisha_json(request) -> dict:
    """request() — yangi so'rov. Cheklov (429) va server xatolarida qisqa kutib qayta yuboriladi."""
    attempt = 0
    while True:
        try:
            async with request() as resp:
                if resp.ok:
                    return await resp.json()
                body = await resp.text()
                error = SttError(f"{resp.status}: {body[:500]}", resp.status)
                if resp.status not in RETRY_STATUSES or attempt == RETRIES:
                    raise error
                retry_after = resp.headers.get("Retry-After")
        except aiohttp.ClientConnectionError as e:
            if attempt == RETRIES:
                raise SttError(repr(e)) from e
            error, retry_after = SttError(repr(e)), None
        delay = _retry_after(retry_after, attempt)
        log.warning("Aisha: %s — %.1f s dan keyin qayta urinilmoqda", error, delay)
        await asyncio.sleep(delay)
        attempt += 1


def _retry_after(header: str | None, attempt: int) -> float:
    try:
        seconds = float(header) if header else 2.0 ** attempt
    except ValueError:
        seconds = 2.0 ** attempt
    return min(max(seconds, 0.5), RETRY_MAX_SECONDS)


# --- ElevenLabs Scribe (zaxira) ---------------------------------------------

async def _elevenlabs(audio: bytes, filename: str, content_type: str) -> str:
    try:
        return await _scribe(audio, filename, content_type, KEYTERMS)
    except SttError as e:
        # Keyterms rad etilsa ham ovoz baribir matnga aylansin
        if e.status not in (400, 422):
            raise
        log.warning("ElevenLabs keyterms bilan so'rovni rad etdi, ularsiz qayta yuborilmoqda")
        return await _scribe(audio, filename, content_type, [])


async def _scribe(audio: bytes, filename: str, content_type: str, keyterms: list[str]) -> str:
    form = aiohttp.FormData()
    form.add_field("model_id", STT_MODEL)
    form.add_field("language_code", "uzb")
    form.add_field("tag_audio_events", "false")
    for term in keyterms:
        form.add_field("keyterms", term)
    form.add_field("file", audio, filename=filename, content_type=content_type)

    headers = {"xi-api-key": settings.elevenlabs_api_key}
    timeout = aiohttp.ClientTimeout(total=90)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.post(STT_URL, data=form, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise SttError(f"{resp.status}: {body[:500]}", resp.status)
                data = await resp.json()
    except (aiohttp.ClientError, TimeoutError) as e:
        raise SttError(repr(e)) from e

    return (data.get("text") or "").strip()
