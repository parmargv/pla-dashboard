import streamlit as st

from pla.models import EntryType
from ui.common import entry_form, selected_fy, show_flash

st.title("📥 Add receipt")
st.caption(f"Record a challan and split its amount across fund heads · FY {selected_fy()}")
show_flash()
entry_form(EntryType.RECEIPT, key="new_receipt")
