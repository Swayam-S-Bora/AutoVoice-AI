from app.db.supabase_client import supabase
from app.core.logger import app_logger, error_logger


def get_recent_conversation(phone: str, limit=5):
    try:
        res = supabase.table("conversation_logs") \
            .select("*") \
            .eq("phone", phone) \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()

        messages = []

        # Reverse to maintain correct order
        for row in reversed(res.data):
            messages.append({
                "role": row["role"],
                "content": row["message"]
            })

        return messages

    except Exception as e:
        error_logger.error(f"Memory Fetch Error: {str(e)}")
        return []


def save_message(phone: str, role: str, message: str):
    try:
        supabase.table("conversation_logs").insert({
            "phone": phone,
            "role": role,
            "message": message
        }).execute()

    except Exception as e:
        error_logger.error(f"Memory Save Error: {str(e)}")