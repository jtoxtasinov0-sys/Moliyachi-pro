"""Ovozli xabarni matnga aylantirish: Aisha (VoiceLab), ishlamasa — ElevenLabs Scribe."""
import asyncio
import logging
import uuid

import aiohttp

from bot.config import settings

log = logging.getLogger(__name__)

# Aisha (VoiceLab) — o'zbek tiliga ixtisoslashgan. Javob darhol kelmasa, navbatga qo'yiladi
# va tayyor bo'lguncha so'rab turiladi.
AISHA_URL = "https://api.voicelab.uz/v1/stt"
AISHA_TRANSCRIPTIONS_URL = "https://api.voicelab.uz/v1/stt/transcriptions"
AISHA_POLL_SECONDS = 1.0
AISHA_MAX_POLLS = 60

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


async def transcribe(audio: bytes, filename: str = "voice.ogg", content_type: str = "audio/ogg") -> str:
    if settings.stt_provider == "aisha" and settings.aisha_api_key:
        try:
            return await _aisha(audio, filename, content_type)
        except SttError as e:
            log.warning("Aisha STT ishlamadi (%s), ElevenLabs'ga o'tilmoqda", e)
    return await _elevenlabs(audio, filename, content_type)


# --- Aisha (VoiceLab) -------------------------------------------------------

async def _aisha(audio: bytes, filename: str, content_type: str) -> str:
    form = aiohttp.FormData()
    form.add_field("audio", audio, filename=filename, content_type=content_type)
    form.add_field("language", "uz")
    form.add_field("include_speakers", "false")

    headers = {"Authorization": f"Bearer {settings.aisha_api_key}", "Accept": "application/json"}
    timeout = aiohttp.ClientTimeout(total=90)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as http:
            # Bir xil kalit bilan qayta yuborilsa, Aisha ovozni ikkinchi marta hisoblamaydi
            data = await _aisha_json(http.post(AISHA_URL, data=form, headers={"Idempotency-Key": str(uuid.uuid4())}))
            for _ in range(AISHA_MAX_POLLS):
                status = data.get("status")
                if status == "failed":
                    error = data.get("error") or {}
                    raise SttError(f"Aisha: {error.get('code') or 'failed'} {error.get('message') or ''}".strip())
                if data.get("transcript") is not None and status in (None, "completed"):
                    return data["transcript"].strip()
                await asyncio.sleep(AISHA_POLL_SECONDS)
                data = await _aisha_json(http.get(f"{AISHA_TRANSCRIPTIONS_URL}/{data['id']}"))
    except (aiohttp.ClientError, TimeoutError, KeyError) as e:
        raise SttError(f"Aisha: {e!r}") from e
    raise SttError("Aisha: javob kutish vaqti tugadi")


async def _aisha_json(request) -> dict:
    async with request as resp:
        if not resp.ok:
            body = await resp.text()
            log.error("Aisha STT xatosi %s: %s", resp.status, body[:500])
            raise SttError(f"Aisha {resp.status}", resp.status)
        return await resp.json()


# --- ElevenLabs Scribe ------------------------------------------------------

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
                    log.error("ElevenLabs STT xatosi %s: %s", resp.status, body[:500])
                    raise SttError(f"ElevenLabs {resp.status}", resp.status)
                data = await resp.json()
    except (aiohttp.ClientError, TimeoutError) as e:
        raise SttError(str(e)) from e

    return (data.get("text") or "").strip()
