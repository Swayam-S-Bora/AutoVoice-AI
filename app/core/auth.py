"""
auth.py — lightweight security layer
-------------------------------------
• Phone validation  (E.164-style, digits only, 7-15 chars)
• HMAC session tokens for WebSocket authentication
  - Server issues a token via GET /auth/token?phone=...
  - Client passes it as ?token=... on the WS upgrade URL
  - Tokens expire after TOKEN_TTL_SECONDS (default 10 min)
• Input sanitisation helpers (strip injection scaffolding)
• Audio buffer size cap constant
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from app.core.config import settings

# Phone validation
_PHONE_RE = re.compile(r"^\+?\d{7,15}$")


def validate_phone(phone: str) -> bool:
    """Return True if phone looks like a real number (digits, optional +, 7-15 chars)."""
    return bool(_PHONE_RE.match(phone.strip()))


def normalise_phone(phone: str) -> str:
    """Strip whitespace and return the phone string as-is (keep + prefix if present)."""
    return phone.strip()


# HMAC session tokens
TOKEN_TTL_SECONDS = 600   # 10 minutes
_SEP = "."


def _signing_key() -> bytes:
    """Derive a per-deployment signing key from SUPABASE_KEY (always present)."""
    key = (settings.SUPABASE_KEY or "dev-fallback-secret").encode()
    return hashlib.sha256(key).digest()


def issue_token(phone: str) -> str:
    """
    Return a short-lived HMAC token encoding the phone and issue timestamp.
    Format: <ts>.<phone>.<hex-mac>
    """
    ts = str(int(time.time()))
    payload = f"{ts}{_SEP}{phone}".encode()
    mac = hmac.new(_signing_key(), payload, hashlib.sha256).hexdigest()
    return f"{ts}{_SEP}{phone}{_SEP}{mac}"


def verify_token(token: str) -> str | None:
    """
    Verify an HMAC token. Returns the phone string on success, None on failure.
    Rejects expired or tampered tokens without raising exceptions.
    """
    try:
        parts = token.split(_SEP, 2)
        if len(parts) != 3:
            return None
        ts_str, phone, provided_mac = parts
        ts = int(ts_str)
        if time.time() - ts > TOKEN_TTL_SECONDS:
            return None
        payload = f"{ts_str}{_SEP}{phone}".encode()
        expected_mac = hmac.new(_signing_key(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_mac, provided_mac):
            return None
        return phone
    except Exception:
        return None


# Input sanitisation — strip prompt-injection scaffolding
# These prefixes are only ever inserted by the *server* in trusted tool result
# messages. If they appear in caller speech they are stripped before the
# transcript reaches the LLM history.
_INJECTION_PREFIXES = (
    "SYSTEM:",
    "TOOL RESULT",
    "ACTION:",
    "action:",
    "tool_name:",
    "tool_args:",
)


def sanitise_transcript(text: str) -> str:
    """
    Wrap transcript in a caller-speech envelope and strip known injection tokens.
    The LLM is instructed (in the system prompt) that only the system role may
    use TOOL RESULT / SYSTEM: prefixes — this adds a defence-in-depth layer.
    """
    if not text:
        return text
    # Remove lines that start with known privileged prefixes
    clean_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if any(stripped.upper().startswith(p.upper()) for p in _INJECTION_PREFIXES):
            continue
        clean_lines.append(line)
    return " ".join(clean_lines).strip()


# Audio buffer cap (bytes) - 512 KB is ample for short push-to-talk clips.
MAX_AUDIO_BUFFER_BYTES = 512 * 1024


# Per-connection STT rate limit (in-process, resets on process restart)
# For production use Redis instead.
import asyncio
from collections import defaultdict

_stt_timestamps: dict[str, list[float]] = defaultdict(list)
_stt_lock = asyncio.Lock()

STT_MAX_CALLS = 30        # per window
STT_WINDOW_SECONDS = 60


async def check_stt_rate_limit(phone: str) -> bool:
    """
    Returns True if the call is allowed, False if rate limit exceeded.
    Removes timestamps older than the window before checking.
    """
    async with _stt_lock:
        now = time.time()
        timestamps = _stt_timestamps[phone]
        # Evict old entries
        _stt_timestamps[phone] = [t for t in timestamps if now - t < STT_WINDOW_SECONDS]
        if len(_stt_timestamps[phone]) >= STT_MAX_CALLS:
            return False
        _stt_timestamps[phone].append(now)
        return True
