import logging
import os
import re
import json
from logging.handlers import RotatingFileHandler
from app.db.supabase_client import supabase

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# PII scrubber — masks phone numbers in log messages
# ---------------------------------------------------------------------------
_PHONE_RE = re.compile(r"(\+?\d{3})\d+(\d{3})")


def _scrub(msg: str) -> str:
    """Replace middle digits of phone numbers with ***."""
    return _PHONE_RE.sub(r"\1***\2", str(msg))


class _ScrubFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _scrub(str(v)) for k, v in record.args.items()}
            else:
                record.args = tuple(_scrub(str(a)) for a in record.args)
        return True


def setup_logger(name, log_file, level=logging.INFO):
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    file_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, log_file),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(_ScrubFilter())

    import sys, io
    stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace") \
        if hasattr(sys.stdout, "buffer") else sys.stdout
    console_handler = logging.StreamHandler(stream)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(_ScrubFilter())

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


app_logger    = setup_logger("app",    "app.log")
error_logger  = setup_logger("error",  "error.log", logging.ERROR)
agent_logger  = setup_logger("agent",  "agent.log")
access_logger = setup_logger("access", "access.log")


def log_action(action_type, input_data, output_data, status="success"):
    try:
        supabase.table("action_logs").insert({
            "action_type": action_type,
            "input_data":  json.dumps(input_data),
            "output_data": json.dumps(output_data),
            "status":      status,
        }).execute()
    except Exception as e:
        error_logger.error(f"DB Logging Failed: {str(e)}")
