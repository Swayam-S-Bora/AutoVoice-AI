"""
speech_service.py
-----------------
STT  → Groq Whisper (whisper-large-v3-turbo)  [sync, called via executor]
TTS  → Deepgram streaming TTS                  [async generator → audio bytes]

Phrase cache: common short phrases are pre-generated at startup so the
very first audio byte for greetings / fillers is instant.
"""
from __future__ import annotations

import asyncio
import io
import random
import re
from typing import AsyncIterator

import httpx
from groq import Groq

from app.core.config import settings
from app.core.logger import app_logger, error_logger

# Clients
_groq = Groq(api_key=settings.GROQ_API_KEYS[0])

DEEPGRAM_TTS_URL = "https://api.deepgram.com/v1/speak"
DEEPGRAM_HEADERS = {
    "Authorization": f"Token {settings.DEEPGRAM_API_KEY}",
    "Content-Type": "application/json",
}
# Deepgram model + voice — Aura Asteria is natural, low-latency
DEEPGRAM_PARAMS = {
    "model": "aura-asteria-en",
    "encoding": "linear16",
    "sample_rate": "24000",
    "container": "none",   # raw PCM — browser AudioContext handles it directly
}

# Thinking / filler phrases (streamed immediately before a tool call)
THINKING_PHRASES = [
    "Sure, let me check that for you.",
    "One moment please.",
    "Let me look that up for you.",
    "Just a second while I check.",
    "Hold on a minute.",
    "Right, give me just a moment.",
]

# Farewell phrases — spoken at the end of a completed booking (final_booking action).
# Pre-warmed at startup so there is zero TTS latency on the closing line.
FAREWELL_PHRASES = [
    "Thank you for choosing us! We'll see you on the day. Take care!",
    "All done! We look forward to seeing your car. Have a great day!",
    "You're all set! Thanks for booking with us. See you soon!",
    "Perfect, everything is confirmed. Thanks for calling, and see you then!",
]

# Mid-conversation interrupt notice — spoken when a new booking intent is detected
# while a previous conversation is still in progress.
INTERRUPTION_PHRASES = [
    "Sure! Let me start fresh for you.",
    "Of course, starting over now.",
    "No problem, let's begin again.",
]

# Greeting responses — returned instantly via fast-path (no LLM call).
# Pre-warmed at startup so TTFA for "hi / hello" is effectively 0 ms.
GREETING_PHRASES = [
    "Hello! Welcome to our service center. How can I help you today?",
    "Hi there! Thanks for calling. How can I assist you?",
    "Hey! Good to hear from you. What can I do for you today?",
]

# All phrases eligible for caching.
ALL_WARMABLE_PHRASES = THINKING_PHRASES + FAREWELL_PHRASES + INTERRUPTION_PHRASES + GREETING_PHRASES

# Startup warming is intentionally small and sequential to avoid memory spikes
# on 512 MB hosts. Less common phrases are cached lazily when first used.
STARTUP_WARM_PHRASES = THINKING_PHRASES + GREETING_PHRASES


def pick_filler() -> str:
    return random.choice(THINKING_PHRASES)


def pick_farewell() -> str:
    return random.choice(FAREWELL_PHRASES)


def pick_interruption() -> str:
    return random.choice(INTERRUPTION_PHRASES)


def pick_greeting() -> str:
    return random.choice(GREETING_PHRASES)


# Phrase cache — pre-warm common audio blobs so TTFA ≈ 0 for cached phrases
_phrase_cache: dict[str, bytes] = {}


async def _fetch_tts_bytes(client: httpx.AsyncClient, text: str) -> bytes:
    """Blocking Deepgram TTS fetch → bytes (used for cache warming only)."""
    r = await client.post(
        DEEPGRAM_TTS_URL,
        headers=DEEPGRAM_HEADERS,
        params=DEEPGRAM_PARAMS,
        json={"text": text},
    )
    r.raise_for_status()
    return r.content


async def warm_phrase_cache() -> None:
    """Pre-generate the most latency-sensitive phrases without a startup burst."""
    warmed = 0
    async with httpx.AsyncClient(timeout=10) as client:
        for phrase in STARTUP_WARM_PHRASES:
            try:
                result = await _fetch_tts_bytes(client, phrase)
            except Exception as exc:
                error_logger.warning(f"[TTS cache] Failed to warm '{phrase[:40]}': {exc}")
                continue
            _phrase_cache[phrase] = result
            warmed += 1
            await asyncio.sleep(0)
    app_logger.info(f"[TTS cache] Warmed {warmed}/{len(STARTUP_WARM_PHRASES)} startup phrases.")


# STT — Groq Whisper (synchronous, run in executor from async context)

# Whisper hallucinates text on silence or very short clips. These are known
# phantom outputs (non-English fragments, filler words) that should be dropped.
_WHISPER_HALLUCINATIONS = {
    # Silence/noise phantoms
    ".",
    "",
    "...",
    "ugh",
    "hmm.",
    "hmm",
    "um.",
    "um",
    "uh.",
    "uh"
}

# Minimum words after stripping punctuation/noise. Keep this at 1 so genuine
# single-word answers like "Frank" or "tomorrow" are accepted.
_MIN_REAL_WORD_COUNT = 1   # allow single real words like "Frank" or "tomorrow"

# Raised from 4 KB to 6 KB to better reject very short noise bursts.
_MIN_AUDIO_BYTES = 6_000

# Whisper initial prompt — primes the model with Indian automotive context so
# that Indian names, car brands, and accented English transcribe more accurately.
# This does NOT lock content; Whisper still transcribes freely.
_WHISPER_PROMPT = (
    "Automobile service booking in India. "
    "Common names: Rahul, Priya, Amit, Neha, Ravi, Suresh, Ananya, Vijay, Pooja, Arjun, "
    "Sanjay, Deepak, Meena, Karan, Divya, Rohit, Sunita, Ajay, Kavya, Prakash. "
    "Car brands: Tata Nexon, Tata Punch, Tata Altroz, Maruti Swift, Maruti Baleno, "
    "Maruti Brezza, Hyundai Creta, Hyundai i20, Mahindra XUV, Honda City, "
    "Kia Seltos, Toyota Innova, Renault Kwid. "
    "Services: basic service, full service, oil change."
)


def speech_to_text_sync(audio_bytes: bytes, mime_type: str = "audio/webm") -> str | None:
    """
    Transcribe raw audio bytes using Groq Whisper.
    Must be called via asyncio.get_event_loop().run_in_executor() from async code.

    Guards applied before returning:
    - Minimum byte length gate (drop near-silence clips)
    - language="en" to prevent Whisper switching to non-English on ambiguous audio
    - Hallucination filter for known phantom outputs
    - Minimum real-word count after stripping punctuation/noise
    """
    if len(audio_bytes) < _MIN_AUDIO_BYTES:
        app_logger.info(f"[STT] Skipped — audio too short ({len(audio_bytes)} bytes)")
        return None

    try:
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "audio.webm"  # Groq needs a filename with extension
        transcript = _groq.audio.transcriptions.create(
            model="whisper-large-v3-turbo",
            file=audio_file,
            response_format="text",
            language="en",          # pin to English — stops non-Latin hallucinations
            prompt=_WHISPER_PROMPT, # primes model for Indian names/car brands/accents
        )
        text = (transcript.strip() if isinstance(transcript, str) else transcript.text.strip())

        # Drop known Whisper hallucinations on silence/noise (case-insensitive)
        if text.lower() in _WHISPER_HALLUCINATIONS:
            app_logger.info(f"[STT] Hallucination filtered: '{text}'")
            return None

        # Drop anything with non-ASCII characters (language bleed like '��r svoja.')
        try:
            text.encode("ascii")
        except UnicodeEncodeError:
            app_logger.info(f"[STT] Non-ASCII hallucination filtered: '{text}'")
            return None

        real_word_count = len(re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", text))
        if real_word_count < _MIN_REAL_WORD_COUNT:
            app_logger.info(f"[STT] Too few real words filtered: '{text}'")
            return None

        app_logger.info(f"[STT] '{text}'")
        return text or None
    except Exception as e:
        error_logger.error(f"[STT] Groq Whisper failed: {e}")
        return None


# TTS — Deepgram streaming (async generator → yields raw PCM chunks)
async def text_to_speech_stream(text: str) -> AsyncIterator[bytes]:
    """
    Yield raw PCM audio chunks from Deepgram as they arrive.
    Uses HTTP streaming so the first byte arrives as fast as possible.

    If the phrase is in the cache, yields the cached blob immediately.
    """
    if text in _phrase_cache:
        app_logger.info("[TTS] Cache hit")
        yield _phrase_cache[text]
        return

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            async with client.stream(
                "POST",
                DEEPGRAM_TTS_URL,
                headers=DEEPGRAM_HEADERS,
                params=DEEPGRAM_PARAMS,
                json={"text": text},
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes(chunk_size=4096):
                    if chunk:
                        yield chunk
    except Exception as e:
        error_logger.error(f"[TTS] Deepgram stream failed: {e}")
        return
