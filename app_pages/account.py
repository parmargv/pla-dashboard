import streamlit as st

from pla.auth import AuthValidationError, change_own_password
from ui.auth import get_user
from ui.common import db, flash, show_flash

user = get_user()
st.title("👤 My account")
st.caption(f"{user.full_name} · `{user.username}` · {user.role.value.title()}")
show_flash()

st.subheader("Change password")
with st.form("change_pw", clear_on_submit=True):
    current = st.text_input("Current password", type="password", autocomplete="current-password")
    new = st.text_input("New password", type="password", autocomplete="new-password",
                        help="At least 8 characters, letters and numbers")
    confirm = st.text_input("Confirm new password", type="password", autocomplete="new-password")
    submitted = st.form_submit_button("Update password", type="primary")
if submitted:
    if new != confirm:
        st.error("New passwords do not match.")
    else:
        try:
            with db() as s:
                change_own_password(s, user.id, current, new)
        except AuthValidationError as exc:
            st.error(str(exc))
        else:
            flash("Password updated.")
            st.rerun()
