from gtts import gTTS
from app.logger import app_logger
import os
import uuid
import base64
from datetime import datetime

# Create audio output directory
AUDIO_DIR = os.path.join(os.getcwd(), "audio_responses")
if not os.path.exists(AUDIO_DIR):
    os.makedirs(AUDIO_DIR)
    app_logger.info(f"Created audio responses directory: {AUDIO_DIR}")

async def text_to_speech(text: str, language: str = 'en') -> dict:
    """
    Convert text to speech using Google TTS
    Returns: {
        "audio_base64": base64 encoded audio,
        "audio_url": path to audio file (for serving),
        "duration": approximate duration in seconds
    }
    """
    try:
        app_logger.info(f"Converting to speech: '{text[:50]}...'")
        
        # Generate unique filename
        filename = f"response_{uuid.uuid4().hex}.mp3"
        filepath = os.path.join(AUDIO_DIR, filename)
        
        # Create gTTS object and save to file
        tts = gTTS(text=text, lang=language, slow=False)
        tts.save(filepath)
        
        # Read file and convert to base64
        with open(filepath, "rb") as audio_file:
            audio_bytes = audio_file.read()
            audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
        
        # Calculate approximate duration (rough estimate: 15 chars per second)
        duration = len(text) / 15
        
        app_logger.info(f"Audio saved: {filepath}, size: {len(audio_bytes)} bytes, est. duration: {duration:.1f}s")
        
        return {
            "audio_base64": audio_base64,
            "audio_url": f"/audio/{filename}",
            "duration": duration,
            "text": text
        }
    
    except Exception as e:
        app_logger.error(f"TTS error: {str(e)}")
        # Return error response
        return {
            "audio_base64": None,
            "audio_url": None,
            "duration": 0,
            "text": text,
            "error": str(e)
        }

async def text_to_speech_file_only(text: str, language: str = 'en') -> str:
    """
    Convert text to speech and return only the file path
    (useful for saving without base64 encoding)
    """
    try:
        filename = f"response_{uuid.uuid4().hex}.mp3"
        filepath = os.path.join(AUDIO_DIR, filename)
        
        tts = gTTS(text=text, lang=language, slow=False)
        tts.save(filepath)
        
        return filepath
    
    except Exception as e:
        app_logger.error(f"TTS file error: {str(e)}")
        return None

# Optional: Serve audio files via FastAPI (add to main.py later)
def setup_audio_serving(app):
    from fastapi.responses import FileResponse
    from fastapi import FastAPI
    
    @app.get("/audio/{filename}")
    async def get_audio(filename: str):
        filepath = os.path.join(AUDIO_DIR, filename)
        if os.path.exists(filepath):
            return FileResponse(filepath, media_type="audio/mpeg")
        return {"error": "Audio not found"}