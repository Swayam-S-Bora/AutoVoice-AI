from supabase import create_client, Client
from app.core.config import settings

# Sync client — used by tools and state manager (called from async context via run_in_executor)
supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
