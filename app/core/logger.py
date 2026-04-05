import logging
import os
import json
from logging.handlers import RotatingFileHandler
from app.db.supabase_client import supabase

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)


def setup_logger(name, log_file, level=logging.INFO):
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Prevent duplicate handlers
    if logger.handlers:
        return logger

    # File handler (rotating)
    file_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, log_file),
        maxBytes=5 * 1024 * 1024,
        backupCount=3
    )

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    file_handler.setFormatter(formatter)

    # Console handler (for dev) — force UTF-8 to avoid Windows cp1252 crashes
    import sys, io
    stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace") \
        if hasattr(sys.stdout, "buffer") else sys.stdout
    console_handler = logging.StreamHandler(stream)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


# Loggers
app_logger = setup_logger("app", "app.log")
error_logger = setup_logger("error", "error.log", logging.ERROR)
agent_logger = setup_logger("agent", "agent.log")
access_logger = setup_logger("access", "access.log")


# DB Logging
def log_action(action_type, input_data, output_data, status="success"):
    try:
        supabase.table("action_logs").insert({
            "action_type": action_type,
            "input_data": json.dumps(input_data),
            "output_data": json.dumps(output_data),
            "status": status
        }).execute()

    except Exception as e:
        error_logger.error(f"DB Logging Failed: {str(e)}")