"""ElevenLabs Scribe: ovozli xabarni matnga aylantirish."""
import logging

import aiohttp

from bot.config import settings

log = logging.getLogger(__name__)

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
STT_MODEL = "scribe_v2"


class SttError(Exception):
    pass


async def transcribe(audio: bytes, filename: str = "voice.ogg") -> str:
    form = aiohttp.FormData()
    form.add_field("model_id", STT_MODEL)
    form.add_field("language_code", "uzb")
    form.add_field("tag_audio_events", "false")
    form.add_field("file", audio, filename=filename, content_type="audio/ogg")

    headers = {"xi-api-key": settings.elevenlabs_api_key}
    timeout = aiohttp.ClientTimeout(total=90)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.post(STT_URL, data=form, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.error("ElevenLabs STT xatosi %s: %s", resp.status, body[:500])
                    raise SttError(f"ElevenLabs {resp.status}")
                data = await resp.json()
    except aiohttp.ClientError as e:
        raise SttError(str(e)) from e

    return (data.get("text") or "").strip()
