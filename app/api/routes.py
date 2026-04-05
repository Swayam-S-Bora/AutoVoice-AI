from fastapi import APIRouter, UploadFile
from app.agents.agent import run_agent
from app.services.speech_service import speech_to_text, text_to_speech
from app.services.date_service import preprocess_input
from app.core.logger import access_logger, error_logger

router = APIRouter()


@router.post("/chat")
async def chat(input_text: str, phone: str):
    try:
        access_logger.info(f"/chat | phone={phone} | input={input_text}")

        # Resolve any date expressions in Python before hitting the LLM
        text, resolved_date = preprocess_input(input_text)

        response = run_agent(text, phone, resolved_date=resolved_date)
        audio = text_to_speech(response)

        return {"text": response, "audio": audio}

    except Exception as e:
        error_logger.error(f"/chat error: {str(e)}")
        return {"error": "Internal server error"}


@router.post("/voice")
async def voice(file: UploadFile, phone: str):
    try:
        path = f"temp_{file.filename}"

        with open(path, "wb") as f:
            f.write(await file.read())

        text = speech_to_text(path)

        if not text:
            return {"error": "Could not understand audio"}

        # Resolve any date expressions in Python before hitting the LLM
        text, resolved_date = preprocess_input(text)

        response = run_agent(text, phone, resolved_date=resolved_date)
        audio = text_to_speech(response)

        return {"text": response, "audio": audio}

    except Exception as e:
        error_logger.error(f"/voice error: {str(e)}")
        return {"error": "Internal server error"}
