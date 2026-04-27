"""
memory_service.py — synchronous (run via executor from async agent)

Adds get/save for last_booking_summary so returning callers can pick
up where they left off without re-stating their entire booking.
The summary is stored in a separate Supabase table: caller_last_booking.
If that table does not exist yet, the functions degrade gracefully.
"""
import json
from app.db.supabase_client import supabase
from app.core.logger import app_logger, error_logger


# Conversation history

def get_recent_conversation(phone: str, limit: int = 5) -> list:
    try:
        res = (
            supabase.table("conversation_logs")
            .select("*")
            .eq("phone", phone)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        messages = []
        for row in reversed(res.data):
            messages.append({"role": row["role"], "content": row["message"]})
        return messages
    except Exception as e:
        error_logger.error(f"Memory Fetch Error: {e}")
        return []


def save_message(phone: str, role: str, message: str) -> None:
    try:
        supabase.table("conversation_logs").insert({
            "phone": phone,
            "role": role,
            "message": message,
        }).execute()
    except Exception as e:
        error_logger.error(f"Memory Save Error: {e}")


# Last booking summary — persists the most recent completed booking per phone
# so returning callers can say "change my booking" and the agent already knows
# the details.
#
# Requires a Supabase table with this schema (add once):
#   CREATE TABLE caller_last_booking (
#     phone        TEXT PRIMARY KEY,
#     summary      JSONB NOT NULL,
#     updated_at   TIMESTAMPTZ DEFAULT now()
#   );

def get_last_booking_summary(phone: str) -> dict | None:
    """Return the most recent completed booking dict for this phone, or None."""
    try:
        res = (
            supabase.table("caller_last_booking")
            .select("summary")
            .eq("phone", phone)
            .limit(1)
            .execute()
        )
        if res.data:
            raw = res.data[0]["summary"]
            return raw if isinstance(raw, dict) else json.loads(raw)
        return None
    except Exception as e:
        # Table may not exist yet — degrade gracefully
        app_logger.info(f"get_last_booking_summary: {e}")
        return None


def save_last_booking_summary(phone: str, summary: dict) -> None:
    """Upsert the completed booking summary for this phone."""
    try:
        supabase.table("caller_last_booking").upsert(
            {"phone": phone, "summary": json.dumps(summary)},
            on_conflict="phone",
        ).execute()
        app_logger.info(f"Last booking summary saved [***{str(phone)[-4:]}]")
    except Exception as e:
        error_logger.error(f"save_last_booking_summary error: {e}")
