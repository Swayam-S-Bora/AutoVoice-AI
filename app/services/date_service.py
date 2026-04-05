"""
date_service.py
Resolves relative date expressions to YYYY-MM-DD strings in Python,
before they ever reach the LLM. The LLM only sees absolute dates.

Strategy:
  1. Try dateparser (handles: "tomorrow", "7 april", "17th", "next week", etc.)
  2. If dateparser returns None -> return None, let LLM resolve using today's date in prompt
"""
import dateparser
from datetime import datetime
from app.core.logger import app_logger


import re

# Patterns that are clearly times, not dates — skip dateparser for these
_TIME_ONLY_PATTERN = re.compile(
    r'^\s*\d{1,2}(:\d{2})?\s*(am|pm)?\s*$',
    re.IGNORECASE
)


def resolve_date(text: str) -> str | None:
    """
    Attempt to extract and resolve a date from free-form text.
    Returns YYYY-MM-DD string or None if no date found.

    Skips inputs that look like times (e.g. "12", "12:00", "2pm")
    to avoid dateparser misreading them as dates.
    """
    if not text or not text.strip():
        return None

    # Don't try to parse bare time expressions as dates
    if _TIME_ONLY_PATTERN.match(text.strip()):
        app_logger.info(f"[date_service] Skipping time-like input: '{text}'")
        return None

    result = dateparser.parse(
        text,
        settings={
            "PREFER_DATES_FROM": "future",   # "tomorrow" -> tomorrow, not yesterday
            "RETURN_AS_TIMEZONE_AWARE": False,
            "RELATIVE_BASE": datetime.now(),
        }
    )

    if result:
        resolved = result.strftime("%Y-%m-%d")
        app_logger.info(f"[date_service] '{text}' -> {resolved}")
        return resolved

    app_logger.info(f"[date_service] Could not resolve date from: '{text}' — LLM fallback")
    return None


def preprocess_input(user_input: str) -> tuple[str, str | None]:
    """
    Run before calling the agent. Returns:
      - original user_input (unchanged, so conversation stays natural)
      - resolved_date (YYYY-MM-DD) or None
    """
    resolved = resolve_date(user_input)
    return user_input, resolved
