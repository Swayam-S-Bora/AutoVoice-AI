"""
routes.py — WebSocket-first transport layer
-------------------------------------------
• POST /chat        — text in, audio stream out  (kept for debugging)
• WS   /ws/{phone}  — full duplex: binary audio in, binary audio out

WebM header fix
---------------
MediaRecorder produces a *continuous* WebM/Opus stream. Only the very first
chunk contains the EBML container header that Groq Whisper needs to identify
the codec. Chunks from the 2nd utterance onward are mid-stream fragments and
will be rejected with "could not process file" if sent without that header.

Fix: capture the first chunk of the session as `webm_header` and prepend it
to every utterance's audio buffer before passing to STT.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from app.agents.agent import run_agent_stream
from app.services.date_service import preprocess_input
from app.services.speech_service import speech_to_text_sync
from app.core.logger import access_logger, error_logger

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /chat — text in, streaming audio out (useful for curl / debugging)
# ---------------------------------------------------------------------------
@router.post("/chat")
async def chat(input_text: str, phone: str):
    access_logger.info(f"/chat | phone={phone} | input={input_text}")
    text, resolved_date = preprocess_input(input_text)

    async def audio_gen():
        async for chunk in run_agent_stream(text, phone, resolved_date=resolved_date):
            yield chunk

    return StreamingResponse(audio_gen(), media_type="audio/pcm")


# ---------------------------------------------------------------------------
# WS /ws/{phone} — persistent WebSocket for full conversation session
#
# Protocol (binary frames):
#   Client → Server : raw audio bytes (WebM/Opus from MediaRecorder)
#                     Signal end-of-utterance with a single b"\x00" frame
#   Server → Client : raw PCM audio chunks (24 kHz, 16-bit, mono, little-endian)
#                     Signal end-of-response with a single b"\xFF\xFE" frame
# ---------------------------------------------------------------------------
END_OF_UTTERANCE = b"\x00"   # client signals "I stopped speaking"
END_OF_RESPONSE  = b"\xFF\xFE"  # server signals "audio stream complete"

# EBML magic bytes that mark the start of a WebM container header
_WEBM_MAGIC = b"\x1a\x45\xdf\xa3"


def _is_webm_header(chunk: bytes) -> bool:
    """Return True if this chunk starts with the EBML/WebM container header."""
    return chunk[:4] == _WEBM_MAGIC


@router.websocket("/ws/{phone}")
async def websocket_endpoint(websocket: WebSocket, phone: str):
    await websocket.accept()
    access_logger.info(f"[WS] Connected: {phone}")

    audio_buffer: list[bytes] = []
    # The first WebM chunk of the session contains the EBML header.
    # We save it and prepend it to every subsequent utterance so Groq
    # Whisper can always identify the codec — even for mid-session turns.
    webm_header: bytes | None = None

    try:
        while True:
            data = await websocket.receive_bytes()

            # ── End-of-utterance marker ──────────────────────────────
            if data == END_OF_UTTERANCE:
                if not audio_buffer:
                    continue

                # Prepend the saved EBML header if this utterance didn't
                # start from the very beginning of the MediaRecorder stream.
                if webm_header and not _is_webm_header(audio_buffer[0]):
                    raw_audio = webm_header + b"".join(audio_buffer)
                    access_logger.info(f"[WS] {phone} | prepended WebM header")
                else:
                    raw_audio = b"".join(audio_buffer)

                audio_buffer.clear()
                access_logger.info(f"[WS] {phone} | audio bytes={len(raw_audio)}")

                # STT in executor (Groq Whisper is sync)
                loop = asyncio.get_event_loop()
                transcript = await loop.run_in_executor(
                    None, speech_to_text_sync, raw_audio, "audio/webm"
                )

                if not transcript:
                    error_logger.warning(f"[WS] {phone} | STT returned nothing — sending retry prompt")
                    # Speak a retry prompt rather than going silent on the user
                    from app.services.speech_service import text_to_speech_stream
                    async for chunk in text_to_speech_stream("Sorry, I didn't catch that. Could you say it again?"):
                        await websocket.send_bytes(chunk)
                    await websocket.send_bytes(END_OF_RESPONSE)
                    continue

                access_logger.info(f"[WS] {phone} | STT: '{transcript}'")
                text, resolved_date = preprocess_input(transcript)

                # Stream agent audio back over WebSocket
                async for audio_chunk in run_agent_stream(
                    text, phone, resolved_date=resolved_date
                ):
                    await websocket.send_bytes(audio_chunk)

                # Signal end of this response
                await websocket.send_bytes(END_OF_RESPONSE)

            else:
                # ── Accumulate audio chunk ───────────────────────────
                # Capture the EBML header from the very first chunk of
                # the session (it starts with the WebM magic bytes).
                if webm_header is None and _is_webm_header(data):
                    webm_header = data
                    access_logger.info(f"[WS] {phone} | WebM header captured ({len(data)} bytes)")

                audio_buffer.append(data)

    except WebSocketDisconnect:
        access_logger.info(f"[WS] Disconnected: {phone}")
    except Exception as e:
        error_logger.error(f"[WS] {phone} error: {e}", exc_info=True)
        try:
            await websocket.close()
        except Exception:
            pass
