"""Application settings, read from environment variables (or a local .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass

try:  # optional: load a .env file during local development
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


@dataclass(frozen=True)
class Settings:
    database_url: str
    office_name: str
    admin_username: str | None
    admin_password: str | None
    session_timeout_minutes: int


def normalize_database_url(url: str) -> str:
    """Accept connection strings exactly as hosting providers show them.

    Neon / Supabase / Render / Heroku give `postgres://…` or `postgresql://…`;
    SQLAlchemy needs the driver named, and this project ships psycopg 3.
    """
    url = url.strip()
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def get_settings() -> Settings:
    return Settings(
        # SQLite for local use. On a server / Streamlit Cloud set a PostgreSQL URL,
        # e.g. postgresql://user:pass@host/dbname?sslmode=require
        # (On Streamlit Community Cloud, put DATABASE_URL in the app's Secrets:
        # top-level secrets are exposed as environment variables.)
        database_url=normalize_database_url(os.getenv("DATABASE_URL", "sqlite:///data/pla.db")),
        office_name=os.getenv("OFFICE_NAME", "PLA Account"),
        # Optional: creates this admin on first start if no users exist yet.
        admin_username=os.getenv("ADMIN_USERNAME") or None,
        admin_password=os.getenv("ADMIN_PASSWORD") or None,
        # Sign the user out after this many idle minutes (0 = never).
        session_timeout_minutes=int(os.getenv("SESSION_TIMEOUT_MINUTES", "30")),
    )
