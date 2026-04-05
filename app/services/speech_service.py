import openai
from gtts import gTTS
import tempfile

def speech_to_text(file_path):
    try:
        audio = open(file_path, "rb")
        transcript = openai.Audio.transcribe("whisper-1", audio)
        return transcript["text"]
    except:
        return None


def text_to_speech(text):
    tts = gTTS(text)
    file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    tts.save(file.name)
    return file.name