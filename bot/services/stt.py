"""Aisha (VoiceLab): ovozli xabarni matnga aylantirish."""
import asyncio
import logging
import uuid

import aiohttp

from bot.config import settings

log = logging.getLogger(__name__)

# Aisha o'zbek tiliga ixtisoslashgan. Javob darhol kelmasa, navbatga qo'yiladi
# va tayyor bo'lguncha so'rab turiladi.
AISHA_URL = "https://api.voicelab.uz/v1/stt"
AISHA_TRANSCRIPTIONS_URL = "https://api.voicelab.uz/v1/stt/transcriptions"
AISHA_POLL_SECONDS = 1.0
AISHA_MAX_POLLS = 60


class SttError(Exception):
    pass


async def transcribe(audio: bytes, filename: str = "voice.ogg", content_type: str = "audio/ogg") -> str:
    try:
        return await _aisha(audio, filename, content_type)
    except SttError as e:
        log.error("Aisha STT xatosi: %s", e)
        raise


async def _aisha(audio: bytes, filename: str, content_type: str) -> str:
    if not settings.aisha_api_key:
        raise SttError("AISHA_API_KEY sozlanmagan")

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
                    raise SttError(f"{error.get('code') or 'failed'} {error.get('message') or ''}".strip())
                if data.get("transcript") is not None and status in (None, "completed"):
                    return data["transcript"].strip()
                await asyncio.sleep(AISHA_POLL_SECONDS)
                data = await _aisha_json(http.get(f"{AISHA_TRANSCRIPTIONS_URL}/{data['id']}"))
    except (aiohttp.ClientError, TimeoutError, KeyError) as e:
        raise SttError(repr(e)) from e
    raise SttError("javob kutish vaqti tugadi")


async def _aisha_json(request) -> dict:
    async with request as resp:
        if not resp.ok:
            body = await resp.text()
            raise SttError(f"{resp.status}: {body[:500]}")
        return await resp.json()
