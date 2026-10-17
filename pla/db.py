"""Engine / session management. Works with SQLite (local) and PostgreSQL (server)."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


@lru_cache(maxsize=None)
def get_engine(url: str) -> Engine:
    is_sqlite = url.startswith("sqlite")
    kwargs: dict = {"pool_pre_ping": True}
    if is_sqlite:
        db_path = url.split("///", 1)[-1]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # Streamlit serves each browser session on its own thread
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(pool_size=5, max_overflow=10, pool_recycle=1800)

    engine = create_engine(url, **kwargs)

    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


@lru_cache(maxsize=None)
def _session_factory(url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False)


@contextmanager
def session_scope(url: str) -> Iterator[Session]:
    """One unit of work: commit on success, roll back on any exception."""
    session = _session_factory(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _migrate(engine: Engine) -> None:
    """Add columns introduced after the first release to an existing database.

    Existing expenditure rows were already part of the statement, so they are
    back-filled as cleared on their own date with cheque amount = their total.
    """
    cols = {c["name"] for c in inspect(engine).get_columns("entries")}
    with engine.begin() as conn:
        if "cheque_amount" not in cols:
            conn.execute(text("ALTER TABLE entries ADD COLUMN cheque_amount NUMERIC(14, 2)"))
            conn.execute(text(
                "UPDATE entries SET cheque_amount = (SELECT SUM(l.amount) FROM entry_lines l "
                "WHERE l.entry_id = entries.id) WHERE entry_type = 'EXPENSE'"
            ))
        if "cleared_on" not in cols:
            conn.execute(text("ALTER TABLE entries ADD COLUMN cleared_on DATE"))
            conn.execute(text("UPDATE entries SET cleared_on = entry_date WHERE entry_type = 'EXPENSE'"))


def init_db(url: str, admin_username: str | None = None, admin_password: str | None = None) -> None:
    """Create tables (idempotent), seed the fund heads and, optionally, the first admin."""
    from .auth import ensure_admin_from_env
    from .models import Base
    from .service import seed_fund_heads

    engine = get_engine(url)
    Base.metadata.create_all(engine)  # creates missing tables (e.g. `users`) on an existing DB
    _migrate(engine)                  # adds missing columns to existing tables
    with session_scope(url) as session:
        seed_fund_heads(session)
        ensure_admin_from_env(session, admin_username, admin_password)
