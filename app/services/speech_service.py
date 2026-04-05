import os
from groq import Groq
from gtts import gTTS
from app.core.config import settings
from app.core.logger import app_logger, error_logger

client = Groq(api_key=settings.GROQ_API_KEY)

RESPONSES_DIR = "responses"
os.makedirs(RESPONSES_DIR, exist_ok=True)


def speech_to_text(file_path: str) -> str | None:
    try:
        with open(file_path, "rb") as audio:
            transcript = client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=audio,
            )
        text = transcript.text.strip()
        app_logger.info(f"[STT] Transcribed: '{text}'")
        return text if text else None
    except Exception as e:
        error_logger.error(f"[STT] Groq STT failed: {str(e)}")
        return None


def text_to_speech(text: str, phone: str = "unknown") -> str:
    """
    Saves TTS audio to responses/<phone>_<timestamp>.mp3
    Returns the file path.
    """
    try:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{phone}_{timestamp}.mp3"
        file_path = os.path.join(RESPONSES_DIR, filename)

        tts = gTTS(text)
        tts.save(file_path)

        app_logger.info(f"[TTS] Saved: {file_path}")
        return file_path
    except Exception as e:
        error_logger.error(f"[TTS] gTTS failed: {str(e)}")
        return ""
