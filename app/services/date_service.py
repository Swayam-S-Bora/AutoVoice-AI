"""
date_service.py
Resolves relative date expressions to YYYY-MM-DD strings in Python,
before they ever reach the LLM. The LLM only sees absolute dates.

Strategy:
  1. Try dateparser (handles: "tomorrow", "7 april", "17th", "next week", etc.)
  2. If dateparser returns None -> return None, let LLM resolve using today's date in prompt
"""
import dateparser
import re
from datetime import datetime
from app.core.logger import app_logger


# Patterns that are clearly times, not dates — skip dateparser for these.
# Covers: "5", "5pm", "5 pm", "5:30", "5:30pm", "5.30", "5.30 pm", "5.30 p.m.", "17:30"
_TIME_ONLY_PATTERN = re.compile(
    r"""
    ^\s*
    \d{1,2}                     # hour
    (
        [:.]\d{2}               # :30 or .30
        (\s*(am|pm|a\.m\.|p\.m\.))?   # optional am/pm with optional dots
    |
        \s*(am|pm|a\.m\.|p\.m\.)      # bare am/pm after hour
    )?
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Patterns that contain clear time-selection words alongside a time —
# also skip date resolution for these.
_TIME_PHRASE_PATTERN = re.compile(
    r'\b(at\s+)?\d{1,2}([:.]\d{2})?\s*(am|pm|a\.m\.|p\.m\.)\b',
    re.IGNORECASE,
)

# Short negative/affirmative words that dateparser incorrectly maps to dates
_DATE_BLACKLIST = re.compile(
    r'^\s*(no|yes|nope|yeah|yep|okay|ok|sure|fine|correct|right|wrong|'
    r'nevermind|cancel|stop|done|none|nothing|same|keep|still)\s*[.!?]?\s*$',
    re.IGNORECASE,
)


def resolve_date(text: str) -> str | None:
    """
    Attempt to extract and resolve a date from free-form text.
    Returns YYYY-MM-DD string or None if no date found.

    Skips inputs that look like bare times, time-only phrases, or
    short words that dateparser misreads as dates.
    """
    if not text or not text.strip():
        return None

    stripped = text.strip()

    # Skip blacklisted short words
    if _DATE_BLACKLIST.match(stripped):
        app_logger.info(f"[date_service] Skipping blacklisted input: '{text}'")
        return None

    # Skip bare time expressions
    if _TIME_ONLY_PATTERN.match(stripped):
        app_logger.info(f"[date_service] Skipping time-like input: '{text}'")
        return None

    # If the entire input is just a time phrase (e.g. "5.30 p.m."), skip
    # We detect this by checking if removing the time match leaves nothing meaningful
    time_match = _TIME_PHRASE_PATTERN.search(stripped)
    if time_match:
        remainder = _TIME_PHRASE_PATTERN.sub("", stripped).strip().strip(".,!?")
        if not remainder:
            app_logger.info(f"[date_service] Skipping time-phrase-only input: '{text}'")
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
