"""
Centralized database client module.
Provides a singleton Supabase client with proper validation.
"""
import os
from supabase import create_client, Client
from functools import lru_cache


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    """
    Get the Supabase client instance (singleton).
    Uses the service role key to bypass RLS for backend operations.
    """
    supabase_url = os.getenv("SUPABASE_URL")
    # Use service role key to bypass RLS (backend is trusted)
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    
    if not supabase_url:
        raise RuntimeError(
            "SUPABASE_URL environment variable is required. "
            "Please set it in your .env file."
        )
    
    if not supabase_key:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY (recommended) or SUPABASE_ANON_KEY environment variable is required. "
            "Please set it in your .env file."
        )
    
    return create_client(supabase_url, supabase_key)


def validate_env_vars():
    """
    Validate all required environment variables at startup.
    Call this during application initialization.
    """
    required_vars = [
        ("SUPABASE_URL", "Supabase project URL"),
    ]
    
    optional_vars = [
        ("ENCRYPTION_SECRET", "API key encryption secret (required for storing user API keys)"),
        ("SUPABASE_SERVICE_ROLE_KEY", "Supabase service role key (recommended for backend, bypasses RLS)"),
    ]
    
    missing = []
    for var, description in required_vars:
        if not os.getenv(var):
            missing.append(f"  - {var}: {description}")
    
    # Check if at least one Supabase key is set
    if not os.getenv("SUPABASE_SERVICE_ROLE_KEY") and not os.getenv("SUPABASE_ANON_KEY"):
        missing.append("  - SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY: Supabase authentication key")
    
    if missing:
        print("WARNING: Missing required environment variables:")
        for item in missing:
            print(item)
        print("Some features may not work correctly.")
    
    # Warn about optional but recommended variables
    for var, description in optional_vars:
        if not os.getenv(var):
            print(f"WARNING: {var} not set - {description}")
