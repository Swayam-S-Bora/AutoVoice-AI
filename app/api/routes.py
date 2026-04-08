import os
from urllib.parse import quote
from fastapi import APIRouter, UploadFile
from fastapi.responses import FileResponse
from app.agents.agent import run_agent
from app.services.speech_service import speech_to_text, text_to_speech
from app.services.date_service import preprocess_input
from app.core.logger import access_logger, error_logger

router = APIRouter()

HEADER_CHAR_REPLACEMENTS = str.maketrans({
    "\u2014": "-",
    "\u2013": "-",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2026": "...",
    "\u00a0": " ",
})


def _build_response_headers(response_text: str) -> dict[str, str]:
    # ASGI servers encode headers as latin-1, so provide a safe fallback
    # plus an exact UTF-8 version that clients can decode if needed.
    normalized_text = response_text.translate(HEADER_CHAR_REPLACEMENTS)
    safe_text = normalized_text.encode("latin-1", errors="replace").decode("latin-1")
    return {
        "X-Response-Text": safe_text,
        "X-Response-Text-UTF8": quote(response_text, safe=""),
        "X-Response-Text-Encoding": "utf-8-percent-encoded",
    }


def _audio_file_response(audio_path: str, response_text: str) -> FileResponse:
    return FileResponse(
        audio_path,
        media_type="audio/mpeg",
        filename=os.path.basename(audio_path),
        headers=_build_response_headers(response_text),
    )


@router.post("/chat")
async def chat(input_text: str, phone: str):
    try:
        access_logger.info(f"/chat | phone={phone} | input={input_text}")

        text, resolved_date = preprocess_input(input_text)
        response = run_agent(text, phone, resolved_date=resolved_date)
        audio_path = text_to_speech(response, phone=phone)
        return _audio_file_response(audio_path, response)

    except Exception as e:
        error_logger.error(f"/chat error: {str(e)}")
        return {"error": "Internal server error"}


@router.post("/voice")
async def voice(file: UploadFile, phone: str):
    stt_path = f"temp_{file.filename}"
    try:
        access_logger.info(f"/voice | phone={phone} | file={file.filename}")

        with open(stt_path, "wb") as f:
            f.write(await file.read())

        text = speech_to_text(stt_path)

        if not text:
            return {"error": "Could not understand audio"}

        text, resolved_date = preprocess_input(text)
        response = run_agent(text, phone, resolved_date=resolved_date)
        audio_path = text_to_speech(response, phone=phone)
        return _audio_file_response(audio_path, response)

    except Exception as e:
        error_logger.error(f"/voice error: {str(e)}")
        return {"error": "Internal server error"}

    finally:
        if os.path.exists(stt_path):
            os.remove(stt_path)
            access_logger.info(f"[cleanup] Deleted temp file: {stt_path}")
