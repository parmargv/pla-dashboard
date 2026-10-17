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


def get_settings() -> Settings:
    return Settings(
        # SQLite for local use; set a PostgreSQL URL on the server, e.g.
        # postgresql+psycopg://user:pass@host:5432/pla
        database_url=os.getenv("DATABASE_URL", "sqlite:///data/pla.db"),
        office_name=os.getenv("OFFICE_NAME", "PLA Account"),
        # Optional: creates this admin on first start if no users exist yet.
        admin_username=os.getenv("ADMIN_USERNAME") or None,
        admin_password=os.getenv("ADMIN_PASSWORD") or None,
        # Sign the user out after this many idle minutes (0 = never).
        session_timeout_minutes=int(os.getenv("SESSION_TIMEOUT_MINUTES", "30")),
    )
