import os
from fastapi import APIRouter, UploadFile
from fastapi.responses import FileResponse
from app.agents.agent import run_agent
from app.services.speech_service import speech_to_text, text_to_speech
from app.services.date_service import preprocess_input
from app.core.logger import access_logger, error_logger

router = APIRouter()


@router.post("/chat")
async def chat(input_text: str, phone: str):
    try:
        access_logger.info(f"/chat | phone={phone} | input={input_text}")

        text, resolved_date = preprocess_input(input_text)
        response = run_agent(text, phone, resolved_date=resolved_date)
        audio_path = text_to_speech(response)

        return FileResponse(
            audio_path,
            media_type="audio/mpeg",
            filename="response.mp3",
            headers={"X-Response-Text": response},  # text also available in response header
        )

    except Exception as e:
        error_logger.error(f"/chat error: {str(e)}")
        return {"error": "Internal server error"}


@router.post("/voice")
async def voice(file: UploadFile, phone: str):
    stt_path = f"temp_{file.filename}"
    try:
        access_logger.info(f"/voice | phone={phone} | file={file.filename}")

        # Save uploaded audio temporarily for STT
        with open(stt_path, "wb") as f:
            f.write(await file.read())

        # Transcribe
        text = speech_to_text(stt_path)

        if not text:
            return {"error": "Could not understand audio"}

        text, resolved_date = preprocess_input(text)
        response = run_agent(text, phone, resolved_date=resolved_date)
        audio_path = text_to_speech(response)

        # Stream the TTS mp3 directly back to the caller
        return FileResponse(
            audio_path,
            media_type="audio/mpeg",
            filename="response.mp3",
            headers={"X-Response-Text": response},  # transcript available in header
        )

    except Exception as e:
        error_logger.error(f"/voice error: {str(e)}")
        return {"error": "Internal server error"}

    finally:
        # Always delete the uploaded STT temp file
        if os.path.exists(stt_path):
            os.remove(stt_path)
            access_logger.info(f"[cleanup] Deleted temp file: {stt_path}")
