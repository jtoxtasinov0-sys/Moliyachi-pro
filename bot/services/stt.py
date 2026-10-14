"""ElevenLabs Scribe: ovozli xabarni matnga aylantirish."""
import logging
import re

import aiohttp

from bot.config import settings

log = logging.getLogger(__name__)

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
STT_MODEL = "scribe_v2"


class SttError(Exception):
    pass


# keyterms cheklovlari (ElevenLabs): har biri 50 belgidan kam, ko'pi bilan 5 so'z, shu belgilarsiz.
# 100 tadan oshsa — har so'rov kamida 20 soniya deb hisoblanadi, shuning uchun 100 dan oshirmaymiz
MAX_KEYTERMS = 100
_BAD_CHARS = re.compile(r"[<>{}\[\]\\]")


def clean_keyterms(terms: list[str]) -> list[str]:
    """Takrorlarsiz, ElevenLabs qabul qiladigan ko'rinishda, ko'pi bilan MAX_KEYTERMS ta."""
    result: list[str] = []
    seen: set[str] = set()
    for term in terms:
        term = " ".join(_BAD_CHARS.sub(" ", term or "").split())
        if not term or len(term) >= 50 or len(term.split()) > 5 or term.lower() in seen:
            continue
        seen.add(term.lower())
        result.append(term)
        if len(result) == MAX_KEYTERMS:
            break
    return result


async def transcribe(
    audio: bytes, filename: str = "voice.ogg", content_type: str = "audio/ogg", keyterms: list[str] = ()
) -> str:
    """keyterms — ovozda uchrashi mumkin bo'lgan ism va so'zlar: model ularni aniqroq eshitadi."""
    form = aiohttp.FormData()
    form.add_field("model_id", STT_MODEL)
    form.add_field("language_code", "uzb")
    form.add_field("tag_audio_events", "false")
    # "ee", "hmm", qayta boshlangan so'zlarsiz — Claude'ga toza matn boradi
    form.add_field("no_verbatim", "true")
    for term in clean_keyterms(list(keyterms)):
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
                    raise SttError(f"ElevenLabs {resp.status}")
                data = await resp.json()
    except aiohttp.ClientError as e:
        raise SttError(str(e)) from e

    return (data.get("text") or "").strip()
