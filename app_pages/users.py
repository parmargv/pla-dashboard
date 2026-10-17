import pandas as pd
import streamlit as st

from pla.auth import (
    AuthValidationError,
    create_user,
    is_locked,
    list_users,
    reset_password,
    unlock_user,
    update_user,
)
from pla.models import Role
from ui.auth import get_user
from ui.common import db, flash, show_flash

def render() -> None:
    me = get_user()
    if not me.is_admin:  # defence in depth; the page is only registered for admins
        st.error("Administrators only.")
        return

    st.title("👥 Users")
    st.caption("Admin: full access + users · Editor: add/edit entries · Viewer: dashboard, ledger, export")
    show_flash()


    def run(action, ok_message: str) -> None:
        try:
            with db() as s:
                action(s)
        except AuthValidationError as exc:
            st.error(str(exc))
        else:
            flash(ok_message)
            st.rerun()


    with db() as s:
        users = list_users(s)
        rows = [
            {
                "id": u.id,
                "Username": u.username,
                "Full name": u.full_name,
                "Role": u.role.value.title(),
                "Status": "Locked" if is_locked(u) else ("Active" if u.is_active else "Disabled"),
                "Last login (UTC)": u.last_login_at,
            }
            for u in users
        ]
    st.dataframe(pd.DataFrame(rows).drop(columns="id"), hide_index=True, width="stretch",
                 column_config={"Last login (UTC)": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")})

    roles = list(Role)
    tab_add, tab_manage = st.tabs(["➕ Add user", "⚙️ Manage user"])

    with tab_add:
        with st.form("add_user", clear_on_submit=True):
            c1, c2 = st.columns(2)
            full_name = c1.text_input("Full name")
            username = c2.text_input("Username", help="Lowercase letters, digits, '.', '_' or '-'")
            c3, c4 = st.columns(2)
            password = c3.text_input("Temporary password", type="password", help="Share it privately; the user can change it under My account")
            role = c4.selectbox("Role", roles, index=roles.index(Role.EDITOR), format_func=lambda r: r.value.title())
            if st.form_submit_button("Create user", type="primary"):
                run(lambda s: create_user(s, username=username, password=password, full_name=full_name, role=role),
                    f"User '{username.strip().lower()}' created.")

    with tab_manage:
        by_id = {u.id: u for u in users}
        uid = st.selectbox("User", list(by_id), format_func=lambda i: f"{by_id[i].full_name} ({by_id[i].username})")
        target = by_id[uid]

        with st.form(f"edit_user_{uid}"):
            c1, c2, c3 = st.columns([2, 1, 1])
            name = c1.text_input("Full name", value=target.full_name)
            new_role = c2.selectbox("Role", roles, index=roles.index(target.role), format_func=lambda r: r.value.title())
            active = c3.toggle("Active", value=target.is_active, disabled=target.id == me.id,
                               help="You cannot disable your own account")
            if st.form_submit_button("Save changes", type="primary"):
                run(lambda s: update_user(s, uid, full_name=name, role=new_role, is_active=active),
                    f"User '{target.username}' updated.")

        with st.form(f"reset_pw_{uid}", clear_on_submit=True):
            new_pw = st.text_input("New password", type="password")
            if st.form_submit_button("Reset password"):
                run(lambda s: reset_password(s, uid, new_pw), f"Password reset for '{target.username}'.")

        if is_locked(target) and st.button("Unlock account"):
            run(lambda s: unlock_user(s, uid), f"'{target.username}' unlocked.")


render()
