from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional

from app.conversation.flow import handle_voice_call
from app.logger import app_logger
from app.database import supabase
from datetime import datetime

router = APIRouter(prefix="/voice", tags=["voice"])

class VoiceResponse(BaseModel):
    response_text: str
    customer_id: Optional[int] = None
    call_id: Optional[str] = None

@router.post("/call", response_model=VoiceResponse)
async def process_voice_call(
    phone_number: str = Form(...),
    audio: Optional[UploadFile] = File(None),
    text: Optional[str] = Form(None)
):
    """
    Process a voice call
    - Either upload an audio file OR provide text
    """
    app_logger.info(f"Voice call received from: {phone_number}")
    
    # Get or create customer
    customer = supabase.table("customers")\
        .select("*")\
        .eq("phone_number", phone_number)\
        .execute()
    
    customer_id = None
    if customer.data:
        customer_id = customer.data[0]["id"]
        # Update last call time
        supabase.table("customers")\
            .update({"last_call_at": datetime.now().isoformat()})\
            .eq("id", customer_id)\
            .execute()
    else:
        # Create new customer if doesn't exist
        new_customer = supabase.table("customers").insert({
            "phone_number": phone_number,
            "preferred_language": "en",
            "created_at": datetime.now().isoformat()
        }).execute()
        if new_customer.data:
            customer_id = new_customer.data[0]["id"]
            app_logger.info(f"Created new customer with ID: {customer_id}")
    
    # Process the call
    response_text = ""
    if audio:
        app_logger.info(f"Processing audio file: {audio.filename}, Content-Type: {audio.content_type}")
        try:
            audio_bytes = await audio.read()
            app_logger.info(f"Read {len(audio_bytes)} bytes from audio file")
            response_text = await handle_voice_call(phone_number, audio_bytes=audio_bytes)
        except Exception as e:
            app_logger.error(f"Error processing audio: {str(e)}")
            response_text = "Sorry, I had trouble processing your audio. Please try again."
    elif text:
        app_logger.info(f"Processing text input: {text}")
        response_text = await handle_voice_call(phone_number, text=text)
    else:
        raise HTTPException(status_code=400, detail="Either audio or text must be provided")
    
    # Log the call
    call_log = supabase.table("call_logs").insert({
        "customer_id": customer_id,
        "call_time": datetime.now().isoformat(),
        "intent": "voice_call",
        "transcript": text if text else "Audio processed",
        "response_summary": response_text[:100],
        "successful": True
    }).execute()
    
    return VoiceResponse(
        response_text=response_text,
        customer_id=customer_id,
        call_id=str(call_log.data[0]["id"]) if call_log.data else None
    )

@router.get("/test/{phone_number}")
async def test_conversation(phone_number: str, message: str):
    """
    Test endpoint for conversation without voice
    """
    response = await handle_voice_call(phone_number, text=message)
    return {"message": message, "response": response}