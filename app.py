"""PLA Account dashboard — entry point.

Run locally:   streamlit run app.py
Nothing but the sign-in screen is reachable until the user has signed in.
"""
import streamlit as st

st.set_page_config(page_title="PLA Account", page_icon="📒", layout="wide")

from pla.auth import AuthUser  # noqa: E402
from pla.config import get_settings  # noqa: E402
from pla.db import init_db  # noqa: E402
from ui.auth import current_user, has_users, login_view, logout, setup_view  # noqa: E402
from ui.common import sidebar_fy_selector  # noqa: E402


@st.cache_resource(show_spinner=False)
def bootstrap(database_url: str, admin_username: str | None, admin_password: str | None) -> bool:
    init_db(database_url, admin_username, admin_password)  # tables, fund heads, optional first admin
    return True


settings = get_settings()
bootstrap(settings.database_url, settings.admin_username, settings.admin_password)

def run_gate() -> None:
    """Not signed in: only the sign-in (or first-time setup) page exists."""
    gate = (
        st.Page(login_view, title="Sign in", icon="🔐")
        if has_users()
        else st.Page(setup_view, title="Setup", icon="🛠️")
    )
    st.navigation([gate], position="hidden").run()


def run_app(user: AuthUser) -> None:
    """Signed in: pages depend on the user's role."""
    pages = {
        "Overview": [
            st.Page("app_pages/dashboard.py", title="Dashboard", icon="📊", default=True),
            st.Page("app_pages/ledger.py", title="Ledger" + (" & edit" if user.can_edit else ""), icon="📒"),
        ],
    }
    if user.can_edit:
        pages["Entries"] = [
            st.Page("app_pages/receipt_entry.py", title="Add receipt (challan)", icon="📥"),
            st.Page("app_pages/bill_entry.py", title="Add expenditure (cheque)", icon="📤"),
            st.Page("app_pages/cheque_clearance.py", title="Cheque clearance", icon="🧾"),
        ]
        pages["Setup"] = [st.Page("app_pages/opening_balance.py", title="Opening balance", icon="🏦")]
    account_pages = [st.Page("app_pages/account.py", title="My account", icon="👤")]
    if user.is_admin:
        account_pages.insert(0, st.Page("app_pages/users.py", title="Users", icon="👥"))
    pages["Account"] = account_pages

    nav = st.navigation(pages)

    with st.sidebar:
        st.markdown(f"### {settings.office_name}")
        sidebar_fy_selector()
        st.divider()
        st.markdown(f"**{user.full_name}**  \n`{user.username}` · {user.role.value.title()}")
        if st.button("Sign out", icon="🚪", width="stretch"):
            logout()

    nav.run()


# Exactly one branch runs, so no dashboard code can execute without a signed-in user.
current = current_user()
if current is None:
    run_gate()
else:
    run_app(current)
