import re
from typing import Tuple, Dict, Any
from datetime import datetime, timedelta
from app.logger import app_logger

# Simple but effective rule-based intent recognition
# (We'll upgrade to ML later if needed)

class IntentRecognizer:
    def __init__(self):
        # Keywords for different intents
        self.intent_patterns = {
            "book_service": [
                r"book.*service", r"schedule.*service", r"service.*appointment",
                r"maintenance", r"oil change", r"servicing", r"service booking"
            ],
            "book_test_drive": [
                r"test drive", r"test.*drive", r"take.*for.*spin", r"drive.*test"
            ],
            "inquiry_price": [
                r"how much", r"price", r"cost", r"pricing", r"rate", r"quote"
            ],
            "inquiry_specs": [
                r"specifications", r"specs", r"features", r"details about",
                r"tell me about", r"what.*like"
            ],
            "cancel_appointment": [
                r"cancel.*appointment", r"cancel.*booking", r"reschedule"
            ],
            "check_status": [
                r"status.*appointment", r"when.*appointment", r"my booking"
            ],
            "greeting": [
                r"hello", r"hi", r"hey", r"good morning", r"good afternoon"
            ]
        }
        
        # Extract entities (dates, times, etc.)
        self.entity_patterns = {
            "date": r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})|(tomorrow|today|next week|next month)",
            "time": r"(\d{1,2}:\d{2}\s*(am|pm))",
            "vehicle_model": r"(swift|dzire|ertiga|brezza|ciaz|baleno|fortuner|innova|city|amaze|creta|verna)"
        }
    
    def recognize(self, text: str) -> Tuple[str, Dict[str, Any]]:
        """
        Recognize intent and extract entities from text
        Returns: (intent, entities)
        """
        text = text.lower().strip()
        app_logger.info(f"Recognizing intent for: '{text}'")
        
        # Check each intent pattern
        for intent, patterns in self.intent_patterns.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    app_logger.info(f"Matched intent: {intent}")
                    
                    # Extract entities
                    entities = self._extract_entities(text)
                    
                    return intent, entities
        
        # Default intent
        app_logger.info("No intent matched, defaulting to inquiry")
        return "inquiry_general", {}
    
    def _extract_entities(self, text: str) -> Dict[str, Any]:
        """Extract useful information from text"""
        entities = {}
        
        # Extract dates
        for pattern_name, pattern in self.entity_patterns.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                # Clean up matches (might be tuples from regex groups)
                clean_matches = []
                for match in matches:
                    if isinstance(match, tuple):
                        clean_matches.extend([m for m in match if m])
                    else:
                        clean_matches.append(match)
                
                entities[pattern_name] = clean_matches[0] if clean_matches else None
        
        return entities

# Create global instance
intent_recognizer = IntentRecognizer()

async def process_user_input(text: str) -> Dict[str, Any]:
    """
    Main function to process user input
    """
    intent, entities = intent_recognizer.recognize(text)
    
    return {
        "intent": intent,
        "entities": entities,
        "original_text": text,
        "confidence": 0.8,  # For rule-based, we'll add confidence later
        "requires_followup": False
    }