"""ElevenLabs Scribe: ovozli xabarni matnga aylantirish."""
import logging

import aiohttp

from bot.config import settings

log = logging.getLogger(__name__)

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
    except aiohttp.ClientError as e:
        raise SttError(str(e)) from e

    return (data.get("text") or "").strip()
