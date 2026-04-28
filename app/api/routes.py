"""
routes.py — WebSocket-first transport layer
-------------------------------------------
• GET  /auth/token      — issue a short-lived HMAC token for a phone number
• POST /chat            — text in, audio stream out  (debugging; requires token)
• WS   /ws/{phone}      — full duplex audio: requires ?token= query param
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.agents.agent import run_agent_stream
from app.services.date_service import preprocess_input
from app.services.speech_service import speech_to_text_sync
from app.core.logger import access_logger, error_logger
from app.core.auth import (
    validate_phone,
    normalise_phone,
    issue_token,
    verify_token,
    sanitise_transcript,
    check_stt_rate_limit,
    MAX_AUDIO_BUFFER_BYTES,
)

router = APIRouter()

# How long (seconds) to wait for another utterance before flushing the buffer
# to the agent.  Keeps multi-sentence pauses merged; long silences still flush.
COALESCE_WINDOW_S: float = 0.6


# GET /auth/token — issue a short-lived token for a validated phone number
@router.get("/auth/token")
async def get_token(phone: str = Query(...)):
    phone = normalise_phone(phone)
    if not validate_phone(phone):
        raise HTTPException(status_code=400, detail="Invalid phone number format.")
    token = issue_token(phone)
    access_logger.info(f"/auth/token | issued token for phone ending {phone[-4:]}")
    return {"token": token, "phone": phone}


# POST /chat — text in, streaming audio out (debugging)
@router.post("/chat")
async def chat(
    input_text: str = Query(..., max_length=1000),
    phone: str = Query(...),
    token: str = Query(...),
):
    phone = normalise_phone(phone)
    if not validate_phone(phone):
        raise HTTPException(status_code=400, detail="Invalid phone number format.")

    verified = verify_token(token)
    if verified != phone:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")

    access_logger.info(f"/chat | phone=***{phone[-4:]} | input={input_text[:80]}")
    safe_input = sanitise_transcript(input_text)
    text, resolved_date = preprocess_input(safe_input)

    async def audio_gen():
        async for chunk in run_agent_stream(text, phone, resolved_date=resolved_date):
            yield chunk

    return StreamingResponse(audio_gen(), media_type="audio/pcm")


# WS /ws/{phone} — persistent WebSocket for full conversation session
#
# Protocol (binary frames):
#   Client → Server : raw audio bytes (WebM/Opus from MediaRecorder)
#                     Signal end-of-utterance with a single b"\x00" frame
#   Server → Client : raw PCM audio chunks (24 kHz, 16-bit, mono, little-endian)
#                     Signal end-of-response with a single b"\xFF\xFE" frame
END_OF_UTTERANCE = b"\x00"
END_OF_RESPONSE  = b"\xFF\xFE"

_WEBM_MAGIC = b"\x1a\x45\xdf\xa3"

from app.core.config import settings as _cfg


def _is_webm_header(chunk: bytes) -> bool:
    return chunk[:4] == _WEBM_MAGIC


@router.websocket("/ws/{phone}")
async def websocket_endpoint(
    websocket: WebSocket,
    phone: str,
    token: str = Query(...),
):
    # Origin check 
    origin = websocket.headers.get("origin", "")
    if origin and origin not in _cfg.ALLOWED_ORIGINS:
        await websocket.close(code=4003)
        access_logger.warning(f"[WS] Rejected foreign origin: {origin}")
        return

    # Phone + token validation 
    phone = normalise_phone(phone)
    if not validate_phone(phone):
        await websocket.close(code=4000)
        return

    verified = verify_token(token)
    if verified != phone:
        await websocket.close(code=4001)
        access_logger.warning(f"[WS] Invalid token for phone ending {phone[-4:]}")
        return

    await websocket.accept()
    access_logger.info(f"[WS] Connected: ***{phone[-4:]}")

    audio_buffer: list[bytes] = []
    audio_buffer_size: int = 0
    webm_header: bytes | None = None

    #  Coalescing buffer — merges rapid consecutive utterances 
    # When the caller pauses mid-sentence and the browser fires END_OF_UTTERANCE
    # prematurely, we accumulate transcripts for COALESCE_WINDOW_S before
    # forwarding to the agent.  This prevents multiple "-- NEW TURN --" per
    # real utterance.
    pending_text: list[str] = []
    pending_date: str | None = None
    coalesce_task: asyncio.Task | None = None

    async def _flush_to_agent():
        """Send the coalesced transcript to the agent and stream audio back."""
        nonlocal pending_text, pending_date, coalesce_task
        combined = " ".join(pending_text).strip()
        date_hint = pending_date
        pending_text = []
        pending_date = None
        coalesce_task = None

        if not combined:
            return

        access_logger.info(f"[WS] ***{phone[-4:]} | flushing coalesced: '{combined[:80]}'")

        # Send agent response text as a text frame for UI captions
        async def _send_agent_text(text: str):
            try:
                await websocket.send_text(f"agent:{text}")
            except Exception:
                pass

        async for audio_chunk in run_agent_stream(
            combined, phone,
            resolved_date=date_hint,
            text_callback=_send_agent_text,
        ):
            await websocket.send_bytes(audio_chunk)
        await websocket.send_bytes(END_OF_RESPONSE)

    async def _schedule_flush():
        """Wait for COALESCE_WINDOW_S then flush; cancelled if another utterance arrives."""
        await asyncio.sleep(COALESCE_WINDOW_S)
        await _flush_to_agent()

    try:
        while True:
            data = await websocket.receive_bytes()

            # End-of-utterance marker 
            if data == END_OF_UTTERANCE:
                if not audio_buffer:
                    continue

                if webm_header and not _is_webm_header(audio_buffer[0]):
                    raw_audio = webm_header + b"".join(audio_buffer)
                    access_logger.info(f"[WS] ***{phone[-4:]} | prepended WebM header")
                else:
                    raw_audio = b"".join(audio_buffer)

                audio_buffer.clear()
                audio_buffer_size = 0
                access_logger.info(f"[WS] ***{phone[-4:]} | audio bytes={len(raw_audio)}")

                # Rate limit
                if not await check_stt_rate_limit(phone):
                    error_logger.warning(f"[WS] ***{phone[-4:]} | STT rate limit exceeded")
                    from app.services.speech_service import text_to_speech_stream
                    async for chunk in text_to_speech_stream("You're speaking too fast. Please wait a moment."):
                        await websocket.send_bytes(chunk)
                    await websocket.send_bytes(END_OF_RESPONSE)
                    continue

                # STT in executor (Groq Whisper is sync)
                loop = asyncio.get_event_loop()
                transcript = await loop.run_in_executor(
                    None, speech_to_text_sync, raw_audio, "audio/webm"
                )

                if not transcript:
                    error_logger.warning(f"[WS] ***{phone[-4:]} | STT returned nothing")
                    # Only send "didn't catch that" if there's nothing already buffered
                    if not pending_text:
                        from app.services.speech_service import text_to_speech_stream
                        async for chunk in text_to_speech_stream("Sorry, I didn't catch that. Could you say it again?"):
                            await websocket.send_bytes(chunk)
                        await websocket.send_bytes(END_OF_RESPONSE)
                    continue

                access_logger.info(f"[WS] ***{phone[-4:]} | STT ok: '{transcript[:60]}'")

                # Send transcript to client immediately for user caption
                try:
                    await websocket.send_text(f"user:{transcript}")
                except Exception:
                    pass

                safe_transcript = sanitise_transcript(transcript)
                text, resolved_date = preprocess_input(safe_transcript)

                # ── FIX: Coalesce — cancel any pending flush and restart window ──
                if coalesce_task and not coalesce_task.done():
                    coalesce_task.cancel()

                pending_text.append(text)
                if resolved_date and not pending_date:
                    pending_date = resolved_date

                # Schedule a new flush after the coalesce window
                coalesce_task = asyncio.ensure_future(_schedule_flush())

            else:
                # Accumulate audio chunk with size cap
                audio_buffer_size += len(data)
                if audio_buffer_size > MAX_AUDIO_BUFFER_BYTES:
                    error_logger.warning(f"[WS] ***{phone[-4:]} | audio buffer overflow — closing")
                    await websocket.close(code=1009)
                    return

                if webm_header is None and _is_webm_header(data):
                    webm_header = data
                    access_logger.info(f"[WS] ***{phone[-4:]} | WebM header captured ({len(data)} bytes)")

                audio_buffer.append(data)

    except WebSocketDisconnect:
        access_logger.info(f"[WS] Disconnected: ***{phone[-4:]}")
        if coalesce_task and not coalesce_task.done():
            coalesce_task.cancel()
    except Exception as e:
        error_logger.error(f"[WS] ***{phone[-4:]} error: {e}", exc_info=True)
        if coalesce_task and not coalesce_task.done():
            coalesce_task.cancel()
        try:
            await websocket.close()
        except Exception:
            pass