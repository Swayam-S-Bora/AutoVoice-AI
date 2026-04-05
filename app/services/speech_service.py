from groq import Groq
from gtts import gTTS
import tempfile
from app.core.config import settings
from app.core.logger import app_logger, error_logger

client = Groq(api_key=settings.GROQ_API_KEY)


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


def text_to_speech(text: str) -> str:
    try:
        tts = gTTS(text)
        file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        tts.save(file.name)
        app_logger.info(f"[TTS] Saved audio to {file.name}")
        return file.name
    except Exception as e:
        error_logger.error(f"[TTS] gTTS failed: {str(e)}")
        return ""