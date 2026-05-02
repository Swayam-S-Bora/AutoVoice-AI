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
        "name":              None,
        "phone":             phone,
        "car_model":         None,
        "service_type":      None,
        "date":              None,
        "time":              None,
        "slot_confirmed":    False,
        "booking_confirmed": False,  # True only after caller confirms the read-back summary
        "booking_intent":    None,   # "new" | "modify"
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
            # Back-fill booking_confirmed for states saved before this field existed
            if "booking_confirmed" not in saved:
                saved["booking_confirmed"] = False
            populated = [k for k, v in saved.items() if v not in (None, False, [])]
            app_logger.info(f"State loaded [***{str(phone)[-4:]}] populated={populated}")
            return saved

        app_logger.info(f"State loaded [***{str(phone)[-4:]}] — no row found, returning empty state")
        return _empty_state(phone)

    except Exception as e:
        error_logger.error(f"load_state error [***{str(phone)[-4:]}]: {str(e)}")
        return _empty_state(phone)


def save_state(phone: str, state: dict):
    """Upsert caller state into Supabase using a safe try-update-then-insert pattern."""
    try:
        serialised = json.dumps(state)

        # Try updating an existing row first
        res = supabase.table("agent_state") \
            .update({"state": serialised, "updated_at": "now()"}) \
            .eq("phone", phone) \
            .execute()

        if res.data:
            # Update hit an existing row — done
            app_logger.info(f"State updated [***{str(phone)[-4:]}] fields={[k for k,v in state.items() if v not in (None, False)]}")
            return

        # No existing row — insert a new one
        supabase.table("agent_state").insert({
            "phone": phone,
            "state": serialised,
        }).execute()

        app_logger.info(f"State inserted [***{str(phone)[-4:]}] fields={[k for k,v in state.items() if v not in (None, False)]}")

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
