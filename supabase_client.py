"""Supabase Client integration module for POS_V2 Cloud Dual-Sync.

Provides fail-safe connection initialization to Supabase PostgreSQL cloud database.
Guarded against missing credentials or offline network state.
"""

import os
import logging
from typing import Optional
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
v2_env_path = os.path.join(HERE, ".env")
if os.path.exists(v2_env_path):
    load_dotenv(v2_env_path)

logger = logging.getLogger(__name__)

_supabase_client = None


def get_supabase_client():
    """Return an initialized Supabase Client, or None if credentials are missing or connection fails."""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip() or os.environ.get("SUPABASE_KEY", "").strip()

    if not supabase_url or not supabase_key:
        logger.debug("Supabase credentials (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY) not set in environment.")
        return None

    try:
        from supabase import create_client, Client
        _supabase_client = create_client(supabase_url, supabase_key)
        logger.info("Supabase client initialized successfully.")
        return _supabase_client
    except Exception as exc:
        logger.warning(f"Failed to initialize Supabase client: {exc}")
        return None


def is_supabase_configured() -> bool:
    """Check if Supabase URL and Key environment variables are configured."""
    return bool(os.environ.get("SUPABASE_URL") and (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")))
