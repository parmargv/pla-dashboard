"""Sign-in flow for Streamlit: session state, idle timeout, login and first-run setup screens."""
from __future__ import annotations

import time

import streamlit as st

from pla.auth import AuthUser, AuthValidationError, authenticate, count_users, create_first_admin, get_active_user
from pla.config import get_settings
from ui.common import db, flash, show_flash

USER_KEY = "auth_user"
ACTIVITY_KEY = "auth_last_activity"


def current_user() -> AuthUser | None:
    """The signed-in user, or None. Enforces idle timeout and re-checks the account
    in the database so a deactivated user is signed out on their next click."""
    user: AuthUser | None = st.session_state.get(USER_KEY)
    if user is None:
        return None

    timeout = get_settings().session_timeout_minutes
    now = time.time()
    last = st.session_state.get(ACTIVITY_KEY, now)
    if timeout and now - last > timeout * 60:
        logout("Your session expired due to inactivity. Please sign in again.", rerun=False)
        return None

    with db() as s:
        fresh = get_active_user(s, user.id)
    if fresh is None:
        logout("Your account is no longer active.", rerun=False)
        return None

    st.session_state[USER_KEY] = fresh  # picks up role / name changes
    st.session_state[ACTIVITY_KEY] = now
    return fresh


def get_user() -> AuthUser:
    """For pages: the user already validated by app.py."""
    return st.session_state[USER_KEY]


def logout(message: str = "You have been signed out.", rerun: bool = True) -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]  # clear everything: forms, selections, FY
    flash(message, "info")
    if rerun:
        st.rerun()


def _sign_in(user: AuthUser) -> None:
    st.session_state[USER_KEY] = user
    st.session_state[ACTIVITY_KEY] = time.time()
    st.rerun()


def _centered():
    _, mid, _ = st.columns([1, 1.3, 1])
    return mid


def login_view() -> None:
    office = get_settings().office_name
    with _centered():
        st.markdown(" ")
        st.title("📒 " + office)
        st.caption("PLA account · sign in to continue")
        show_flash()
        with st.form("login", clear_on_submit=False, border=True):
            username = st.text_input("Username", autocomplete="username")
            password = st.text_input("Password", type="password", autocomplete="current-password")
            submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
        if submitted:
            if not username or not password:
                st.error("Enter username and password.")
                return
            with db() as s:
                result = authenticate(s, username, password)
            if isinstance(result, str):
                st.error(result)
            else:
                _sign_in(result)
        st.caption("Forgot your password? Ask an administrator to reset it.")


def setup_view() -> None:
    """Shown only while no user exists: create the first administrator."""
    with _centered():
        st.markdown(" ")
        st.title("📒 First-time setup")
        st.caption("No users exist yet. Create the administrator account to secure the dashboard.")
        with st.form("setup", border=True):
            full_name = st.text_input("Full name")
            username = st.text_input("Username", help="Lowercase letters, digits, '.', '_' or '-'")
            password = st.text_input("Password", type="password", help="At least 8 characters, letters and numbers")
            confirm = st.text_input("Confirm password", type="password")
            submitted = st.form_submit_button("Create administrator", type="primary", width="stretch")
        if submitted:
            if password != confirm:
                st.error("Passwords do not match.")
                return
            try:
                with db() as s:
                    user = create_first_admin(s, username=username, password=password, full_name=full_name)
            except AuthValidationError as exc:
                st.error(str(exc))
            else:
                _sign_in(user)


def has_users() -> bool:
    with db() as s:
        return count_users(s) > 0
