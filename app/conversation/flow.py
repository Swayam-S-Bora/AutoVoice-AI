from typing import Dict, Any, Optional
from datetime import datetime
from app.ai.intent_recognition import process_user_input
from app.database import supabase
from app.logger import app_logger, call_logger

class ConversationManager:
    def __init__(self):
        self.sessions = {}  # Store conversation state by phone number
        
    def get_session(self, phone_number: str) -> Dict[str, Any]:
        """Get or create a conversation session"""
        if phone_number not in self.sessions:
            self.sessions[phone_number] = {
                "state": "greeting",
                "context": {},
                "history": [],
                "customer_id": None,
                "message_count": 0
            }
            app_logger.info(f"Created new session for {phone_number}")
        return self.sessions[phone_number]
    
    async def process_message(self, phone_number: str, message: str) -> str:
        """Process a message and return response"""
        session = self.get_session(phone_number)
        session["message_count"] += 1
        
        # Add to history
        session["history"].append({
            "role": "user",
            "message": message,
            "timestamp": datetime.now().isoformat()
        })
        
        # Process with AI
        ai_result = await process_user_input(message)
        intent = ai_result["intent"]
        entities = ai_result["entities"]
        
        app_logger.info(f"Session {phone_number}: State={session['state']}, Intent={intent}, Message #{session['message_count']}")
        
        # Handle based on current state and intent
        response = await self._generate_response(session, intent, entities, message)
        
        # Add response to history
        session["history"].append({
            "role": "assistant",
            "message": response,
            "timestamp": datetime.now().isoformat()
        })
        
        # Log the interaction
        call_logger.info(f"CONVERSATION|{phone_number}|Intent:{intent}|User:{message}|AI:{response}")
        
        return response
    
    async def _generate_response(self, session: Dict, intent: str, entities: Dict, original: str) -> str:
        """Generate appropriate response based on intent and state"""
        
        app_logger.info(f"Generating response - State: {session['state']}, Intent: {intent}")
        
        # Handle GREETING state
        if session["state"] == "greeting":
            session["state"] = "main_menu"
            return ("Hello! Welcome to AutoVoice AI. How can I help you today?\n\n"
                   "You can say:\n"
                   "• 'Book a service appointment'\n"
                   "• 'Schedule a test drive'\n"
                   "• 'Check vehicle prices'\n"
                   "• 'Cancel an appointment'")
        
        # MAIN MENU state - process user's choice
        elif session["state"] == "main_menu":
            if "book" in original.lower() and "service" in original.lower():
                session["state"] = "collecting_service_details"
                session["context"]["booking_type"] = "service"
                return "I'd be happy to book a service for you. What date would you prefer? (e.g., 'tomorrow' or 'March 15th')"
            
            elif "test drive" in original.lower() or ("schedule" in original.lower() and "drive" in original.lower()):
                session["state"] = "collecting_test_drive_details"
                session["context"]["booking_type"] = "test_drive"
                return "Great! I can schedule a test drive. Which model are you interested in, and what date works for you?"
            
            elif "price" in original.lower() or "cost" in original.lower() or "how much" in original.lower():
                if "vehicle_model" in entities:
                    model = entities["vehicle_model"]
                    return f"The {model.title()} starts at around $20,000. Would you like to schedule a test drive?"
                else:
                    return "I can help with pricing. Which model are you interested in? We have Swift, Dzire, Brezza, and more."
            
            elif "cancel" in original.lower():
                session["state"] = "cancelling"
                return "I can help you cancel an appointment. Could you please provide your appointment ID or date?"
            
            else:
                return ("I'm not sure I understood. Please choose one of these options:\n\n"
                       "• 'Book a service'\n"
                       "• 'Schedule test drive'\n"
                       "• 'Check prices'\n"
                       "• 'Cancel appointment'")
        
        # COLLECTING SERVICE DETAILS state
        elif session["state"] == "collecting_service_details":
            # Check if they mentioned a date
            if "tomorrow" in original.lower() or "today" in original.lower() or "next" in original.lower() or any(char.isdigit() for char in original):
                session["state"] = "confirm_booking"
                session["context"]["requested_date"] = original
                return f"Great! I'll book your service for {original}. Shall I confirm this appointment?"
            else:
                return "Please tell me what date works for you. For example, 'tomorrow' or 'March 15th'"
        
        # COLLECTING TEST DRIVE DETAILS state
        elif session["state"] == "collecting_test_drive_details":
            # Simple check - in real app, extract model and date
            session["state"] = "confirm_booking"
            session["context"]["requested_date"] = original
            return f"Thanks! I'll schedule your test drive. I have noted: '{original}'. Shall I confirm this?"
        
        # CONFIRM BOOKING state
        elif session["state"] == "confirm_booking" or session["state"] == "cancelling":
            if "yes" in original.lower() or "confirm" in original.lower() or "sure" in original.lower():
                session["state"] = "main_menu"
                
                # Here you would actually save to database
                booking_type = session["context"].get("booking_type", "appointment")
                
                return f"Perfect! Your {booking_type} has been confirmed. Is there anything else I can help you with?"
            else:
                session["state"] = "main_menu"
                return "No problem. Is there something else I can help you with?"
        
        # Default fallback
        else:
            session["state"] = "main_menu"
            return "How can I help you? You can say 'book service', 'test drive', 'prices', or 'cancel'."

# Create global instance
conversation_manager = ConversationManager()

async def handle_voice_call(phone_number: str, audio_bytes: Optional[bytes] = None, text: Optional[str] = None) -> str:
    """
    Main entry point for voice calls
    Either audio_bytes or text should be provided
    """
    if audio_bytes:
        # Convert speech to text
        from app.voice.speech_to_text import transcribe_audio
        text = await transcribe_audio(audio_bytes)
        app_logger.info(f"Transcribed text: '{text}'")
        
        if not text or len(text.strip()) == 0:
            return "I couldn't hear you clearly. Could you please speak again?"
    
    if not text:
        return "I didn't receive any input. How can I help you?"
    
    # Process through conversation manager
    response = await conversation_manager.process_message(phone_number, text)
    
    return response