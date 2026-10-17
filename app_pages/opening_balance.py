import pandas as pd
import streamlit as st

from pla.fiscal import previous_fy
from pla.service import carry_forward, get_fund_heads, get_opening_balances, head_balances, set_opening_balances
from ui.common import db, flash, inr, money_column, selected_fy, show_flash

fy = selected_fy()
prev = previous_fy(fy)
st.title("🏦 Opening balance")
st.caption(f"Opening balance per fund head at 1 April for FY {fy}")
show_flash()

with db() as s:
    heads = get_fund_heads(s)
    opening = get_opening_balances(s, fy)
    prev_closing = head_balances(s, prev)

ver = st.session_state.setdefault("ob_ver", 0)
grid = pd.DataFrame(
    {
        "head_id": [h.id for h in heads],
        "Fund Head": [h.name for h in heads],
        f"Closing FY {prev}": [float(prev_closing.get(h.id, 0)) for h in heads],
        "Opening balance": [float(opening.get(h.id, 0)) for h in heads],
    }
)
edited = st.data_editor(
    grid,
    key=f"ob_grid_{fy}_{ver}",
    hide_index=True,
    width="stretch",
    height=36 * (len(heads) + 1) + 3,
    disabled=["Fund Head", f"Closing FY {prev}"],
    column_config={
        "head_id": None,
        f"Closing FY {prev}": money_column(),
        "Opening balance": st.column_config.NumberColumn("Opening balance (₹)", step=1.0, format="%.2f"),
    },
)
st.metric("Total opening balance", inr(float(edited["Opening balance"].fillna(0).sum())))

c1, c2, _ = st.columns([1, 2, 3])
if c1.button("Save opening balance", type="primary", width="stretch"):
    with db() as s:
        set_opening_balances(s, fy, edited.set_index("head_id")["Opening balance"].fillna(0).to_dict())
    flash(f"Opening balance for FY {fy} saved.")
    st.session_state["ob_ver"] = ver + 1
    st.rerun()

if c2.button(f"Carry forward closing balance of FY {prev}", width="stretch"):
    with db() as s:
        carry_forward(s, prev, fy)
    flash(f"Opening balance for FY {fy} set from FY {prev} closing balance.")
    st.session_state["ob_ver"] = ver + 1
    st.rerun()
