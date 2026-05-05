import os
import sys
from dotenv import load_dotenv

load_dotenv()


class Settings:
    GROQ_API_KEYS: list[str] = [
        key.strip()
        for key in [
            os.getenv("GROQ_API_KEY", ""),
            os.getenv("GROQ_API_KEY_2", ""),
            os.getenv("GROQ_API_KEY_3", ""),
            os.getenv("GROQ_API_KEY_4", ""),
            os.getenv("GROQ_API_KEY_5", ""),
        ]
        if key.strip()
    ]
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
    DEEPGRAM_API_KEY: str = os.getenv("DEEPGRAM_API_KEY", "")
    ALLOWED_ORIGINS: list[str] = [
        o.strip()
        for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
        if o.strip()
    ]


settings = Settings()


def validate_settings() -> None:
    """
    Validate required secrets at startup.
    Exits immediately with a clear message rather than crashing on first API call.
    """
    if not settings.GROQ_API_KEYS:
        print(
            "[STARTUP ERROR] Missing required environment variable: GROQ_API_KEY\n",
            file=sys.stderr,
        )
        sys.exit(1)

    required = {
        "SUPABASE_URL": settings.SUPABASE_URL,
        "SUPABASE_KEY": settings.SUPABASE_KEY,
        "DEEPGRAM_API_KEY": settings.DEEPGRAM_API_KEY,
    }
    missing = [k for k, v in required.items() if not v or v.startswith("your_")]
    if missing:
        print(
            f"[STARTUP ERROR] Missing required environment variables: {', '.join(missing)}\n"
            f"Copy example.env to .env and fill in real values.",
            file=sys.stderr,
        )
        sys.exit(1)