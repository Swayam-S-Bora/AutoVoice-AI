"""
State management for the ReAct agent.
Each caller (phone number) gets their own state persisted in Supabase.

booking_intent: "new" | "modify" | None
  Set on the first turn where the caller expresses their goal so the LLM
  always knows whether to call create_booking or update_booking.
"""
import json
from app.db.supabase_client import supabase
from app.core.logger import app_logger, error_logger


def _empty_state(phone: str = None) -> dict:
    return {
        "name":           None,
        "phone":          phone,
        "car_model":      None,
        "service_type":   None,
        "date":           None,
        "time":           None,
        "slot_confirmed": False,
        "booking_intent": None,   # "new" | "modify"
    }


def load_state(phone: str) -> dict:
    """Load caller state from Supabase. Returns fresh state if none exists."""
    try:
        res = supabase.table("agent_state") \
            .select("state") \
            .eq("phone", phone) \
            .limit(1) \
            .execute()

        if res.data:
            saved = json.loads(res.data[0]["state"])
            # Back-fill booking_intent for states saved before this field existed
            if "booking_intent" not in saved:
                saved["booking_intent"] = None
            return saved

        return _empty_state(phone)

    except Exception as e:
        error_logger.error(f"load_state error [***{str(phone)[-4:]}]: {str(e)}")
        return _empty_state(phone)


def save_state(phone: str, state: dict):
    """Upsert caller state into Supabase."""
    try:
        supabase.table("agent_state").upsert({
            "phone": phone,
            "state": json.dumps(state),
        }, on_conflict="phone").execute()

        app_logger.info(f"State saved [***{str(phone)[-4:]}]")

    except Exception as e:
        error_logger.error(f"save_state error [***{str(phone)[-4:]}]: {str(e)}")


def is_booking_ready(state: dict) -> bool:
    """
    Returns True ONLY when every required field is present
    AND the user has confirmed a specific time slot.
    """
    required = ["name", "phone", "car_model", "service_type", "date", "time"]
    for field in required:
        val = state.get(field)
        if val is None or str(val).strip() == "":
            return False
    if not state.get("slot_confirmed"):
        return False
    return True
