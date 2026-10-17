import streamlit as st

from pla.models import EntryType
from ui.common import entry_form, selected_fy, show_flash

st.title("📤 Add expenditure (cheque)")
st.caption(
    f"FY {selected_fy()} · Enter the cheque amount, then split it across fund heads — both totals must match. "
    "A cheque shows in the statement once its clearance date is entered (now or later on **Cheque clearance**)."
)
show_flash()
entry_form(EntryType.EXPENSE, key="new_cheque")
