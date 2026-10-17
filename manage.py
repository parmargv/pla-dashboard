"""Server-side admin commands (for when nobody can sign in).

Usage:
    python manage.py list-users
    python manage.py reset-password <username>
    python manage.py unlock <username>
    python manage.py create-admin <username> --name "Full Name"

Uses the same DATABASE_URL (.env) as the app. Passwords are prompted for and never echoed.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from pla.auth import AuthValidationError, create_user, is_locked, list_users, reset_password, unlock_user
from pla.config import get_settings
from pla.db import init_db, session_scope
from pla.models import Role, User


def _ask_new_password() -> str:
    pw = getpass.getpass("New password: ")
    if pw != getpass.getpass("Confirm new password: "):
        sys.exit("Passwords do not match.")
    return pw


def _find(session, username: str) -> User:
    user = session.scalar(select(User).where(User.username == username.strip().lower()))
    if user is None:
        sys.exit(f"No user named '{username}'. Run: python manage.py list-users")
    return user


def cmd_list(_args, session) -> None:
    users = list_users(session)
    if not users:
        print("No users yet. Open the app to run first-time setup, or use create-admin.")
    for u in users:
        status = "locked" if is_locked(u) else ("active" if u.is_active else "disabled")
        print(f"{u.username:<20} {u.role.value:<7} {status:<9} {u.full_name}")


def cmd_reset(args, session) -> None:
    user = _find(session, args.username)
    reset_password(session, user.id, _ask_new_password())  # also clears any lockout
    if not user.is_active:
        user.is_active = True
        print("Account was disabled; it has been re-activated.")
    print(f"Password reset for '{user.username}'.")


def cmd_unlock(args, session) -> None:
    user = _find(session, args.username)
    unlock_user(session, user.id)
    print(f"'{user.username}' unlocked.")


def cmd_create_admin(args, session) -> None:
    create_user(session, username=args.username, password=_ask_new_password(),
                full_name=args.name, role=Role.ADMIN)
    print(f"Administrator '{args.username.strip().lower()}' created.")


def main() -> None:
    parser = argparse.ArgumentParser(description="PLA dashboard admin commands")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-users", help="List all users").set_defaults(func=cmd_list)
    p = sub.add_parser("reset-password", help="Set a new password for a user")
    p.add_argument("username")
    p.set_defaults(func=cmd_reset)
    p = sub.add_parser("unlock", help="Clear a lockout after too many failed attempts")
    p.add_argument("username")
    p.set_defaults(func=cmd_unlock)
    p = sub.add_parser("create-admin", help="Create an additional administrator")
    p.add_argument("username")
    p.add_argument("--name", default="Administrator")
    p.set_defaults(func=cmd_create_admin)
    args = parser.parse_args()

    url = get_settings().database_url
    init_db(url)
    try:
        with session_scope(url) as session:
            args.func(args, session)
    except AuthValidationError as exc:
        sys.exit(f"Error: {exc}")


if __name__ == "__main__":
    main()
