"""User accounts and authentication (framework-agnostic, standard library only).

Passwords are stored as salted PBKDF2-HMAC-SHA256 hashes. After MAX_FAILED_ATTEMPTS
wrong passwords an account is locked for LOCK_MINUTES.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Role, User

PBKDF2_ITERATIONS = 600_000
MIN_PASSWORD_LENGTH = 8
MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,50}$")
INVALID_CREDENTIALS = "Invalid username or password."


class AuthValidationError(ValueError):
    """User-correctable problem (shown as-is in the UI)."""


@dataclass(frozen=True)
class AuthUser:
    """Detached snapshot of a signed-in user, safe to keep in st.session_state."""

    id: int
    username: str
    full_name: str
    role: Role

    @property
    def can_edit(self) -> bool:
        return self.role.can_edit

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _snapshot(user: User) -> AuthUser:
    return AuthUser(id=user.id, username=user.username, full_name=user.full_name, role=user.role)


# --------------------------------------------------------------------------- #
# Password hashing
# --------------------------------------------------------------------------- #
def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password(secrets.token_hex(8))


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #
def _clean_username(username: str) -> str:
    username = (username or "").strip().lower()
    if not USERNAME_RE.match(username):
        raise AuthValidationError(
            "Username must be 3–50 characters: lowercase letters, digits, '.', '_' or '-'."
        )
    return username


def _check_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthValidationError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if password.isdigit() or password.isalpha():
        raise AuthValidationError("Password must contain both letters and numbers/symbols.")


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
def authenticate(session: Session, username: str, password: str) -> AuthUser | str:
    """Return the signed-in user, or an error message.

    Returns (rather than raises) on failure so the failed-attempt counter is
    committed by the caller's session scope instead of being rolled back.
    """
    name = (username or "").strip().lower()
    user = session.scalar(select(User).where(User.username == name))

    if user is None or not user.is_active:
        verify_password(password or "", _dummy_hash())  # equalise timing
        return INVALID_CREDENTIALS

    now = _utcnow()
    if user.locked_until and user.locked_until > now:
        minutes = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
        return f"Too many failed attempts. Try again in {minutes} minute(s) or ask an administrator to unlock."

    if not verify_password(password or "", user.password_hash):
        user.failed_attempts = (user.failed_attempts or 0) + 1
        if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
            user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            user.failed_attempts = 0
            return f"Too many failed attempts. Account locked for {LOCK_MINUTES} minutes."
        return INVALID_CREDENTIALS

    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    return _snapshot(user)


def get_active_user(session: Session, user_id: int) -> AuthUser | None:
    """Re-read the user on every page run so deactivation / role changes apply immediately."""
    user = session.get(User, user_id)
    return _snapshot(user) if user and user.is_active else None


# --------------------------------------------------------------------------- #
# User management
# --------------------------------------------------------------------------- #
def count_users(session: Session) -> int:
    return session.scalar(select(func.count(User.id))) or 0


def list_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(User.username)))


def create_user(
    session: Session, *, username: str, password: str, full_name: str, role: Role = Role.EDITOR
) -> AuthUser:
    username = _clean_username(username)
    _check_password(password)
    full_name = (full_name or "").strip()
    if not full_name:
        raise AuthValidationError("Full name is required.")
    if session.scalar(select(User.id).where(User.username == username)) is not None:
        raise AuthValidationError(f"Username '{username}' is already taken.")
    user = User(username=username, full_name=full_name, password_hash=hash_password(password), role=role)
    session.add(user)
    session.flush()
    return _snapshot(user)


def create_first_admin(session: Session, *, username: str, password: str, full_name: str) -> AuthUser:
    """Only allowed while the users table is empty (first-run setup)."""
    if count_users(session) > 0:
        raise AuthValidationError("Setup is already complete. Please sign in.")
    return create_user(session, username=username, password=password, full_name=full_name, role=Role.ADMIN)


def ensure_admin_from_env(session: Session, username: str | None, password: str | None) -> None:
    if username and password and count_users(session) == 0:
        create_user(session, username=username, password=password, full_name="Administrator", role=Role.ADMIN)


def _get(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise AuthValidationError("User not found.")
    return user


def _other_active_admins(session: Session, user_id: int) -> int:
    return session.scalar(
        select(func.count(User.id)).where(User.role == Role.ADMIN, User.is_active.is_(True), User.id != user_id)
    ) or 0


def reset_password(session: Session, user_id: int, new_password: str) -> None:
    _check_password(new_password)
    user = _get(session, user_id)
    user.password_hash = hash_password(new_password)
    user.failed_attempts = 0
    user.locked_until = None


def change_own_password(session: Session, user_id: int, current: str, new_password: str) -> None:
    user = _get(session, user_id)
    if not verify_password(current or "", user.password_hash):
        raise AuthValidationError("Current password is incorrect.")
    if current == new_password:
        raise AuthValidationError("New password must be different from the current one.")
    reset_password(session, user_id, new_password)


def update_user(
    session: Session, user_id: int, *, full_name: str, role: Role, is_active: bool
) -> None:
    user = _get(session, user_id)
    losing_admin = user.role is Role.ADMIN and user.is_active and (role is not Role.ADMIN or not is_active)
    if losing_admin and _other_active_admins(session, user_id) == 0:
        raise AuthValidationError("At least one active administrator is required.")
    if not (full_name or "").strip():
        raise AuthValidationError("Full name is required.")
    user.full_name = full_name.strip()
    user.role = role
    user.is_active = is_active


def unlock_user(session: Session, user_id: int) -> None:
    user = _get(session, user_id)
    user.failed_attempts = 0
    user.locked_until = None


def is_locked(user: User) -> bool:
    return bool(user.locked_until and user.locked_until > _utcnow())
