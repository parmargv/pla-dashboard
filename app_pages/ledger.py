import pandas as pd
import streamlit as st

from pla.config import get_settings
from pla.excel_export import build_pla_workbook
from pla.fiscal import fy_bounds
from pla.models import EntryType
from pla.service import entries_wide, get_entry, get_fund_heads, get_opening_balances
from ui.auth import get_user
from ui.common import db, entry_form, inr, money_column, selected_fy, show_flash

fy = selected_fy()
st.title("📒 Ledger")
st.caption(f"FY {fy} · receipts, the expenditure statement (cleared cheques) and the full cheque register")
show_flash()

with db() as s:
    heads = get_fund_heads(s)
    opening = get_opening_balances(s, fy)
    receipts = entries_wide(s, fy, EntryType.RECEIPT)
    statement = entries_wide(s, fy, EntryType.EXPENSE, cleared=True)
    register = entries_wide(s, fy, EntryType.EXPENSE, cleared=None)
    pending = entries_wide(s, fy, EntryType.EXPENSE, cleared=False)

head_names = [h.name for h in heads]
excel = build_pla_workbook(
    fy, head_names, {h.name: float(opening.get(h.id, 0)) for h in heads}, receipts, statement,
    office_name=get_settings().office_name, pending=pending,
)
st.download_button(
    "⬇️ Download PLA sheet (Excel)", excel, file_name=f"PLA_ACCOUNT_{fy}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

# ---- filters ----
start, end = fy_bounds(fy)
f1, f2, f3 = st.columns([2, 2, 1])
date_range = f1.date_input("Date range", value=(start, end), format="DD/MM/YYYY")
search = f2.text_input("Search challan / cheque no. or remarks")
hide_empty = f3.toggle("Hide unused heads", value=True)


def filtered(df: pd.DataFrame, ref_col: str, date_col: str = "Date") -> pd.DataFrame:
    if df.empty:
        return df
    out = df
    if isinstance(date_range, tuple) and len(date_range) == 2:
        dates = out[date_col]
        out = out[dates.notna() & (dates >= date_range[0]) & (dates <= date_range[1])]
    if search:
        mask = out[ref_col].str.contains(search, case=False, na=False) | out["Remarks"].str.contains(
            search, case=False, na=False
        )
        out = out[mask]
    return out


def show_table(df: pd.DataFrame, entry_type: EntryType, date_col: str = "Date", empty_msg: str = "") -> None:
    ref_col = entry_type.ref_label
    view = filtered(df, ref_col, date_col)
    if view.empty:
        st.info(empty_msg or f"No {entry_type.title.lower()} entries for this selection.")
        return
    cols = [c for c in view.columns if c != "id"]
    if hide_empty:
        cols = [c for c in cols if c not in head_names or view[c].abs().sum() > 0]
    st.dataframe(
        view[cols],
        hide_index=True,
        width="stretch",
        column_config={
            "Date": st.column_config.DateColumn(
                "Cheque date" if entry_type is EntryType.EXPENSE else "Date", format="DD/MM/YYYY"
            ),
            "Cleared On": st.column_config.DateColumn(format="DD/MM/YYYY"),
            **{c: money_column() for c in [*head_names, "TOTAL", "BALANCE", "Cheque Amount"]},
        },
    )
    st.markdown(f"**{len(view)} entries · total {inr(float(view['TOTAL'].sum()))}**")


can_edit = get_user().can_edit
tab_names = ["Receipts (challans)", "Expenditure statement", f"Cheque register ({len(pending)} pending)"]
tab_names += ["✏️ Edit / delete"] if can_edit else []
tab_r, tab_s, tab_reg, *rest = st.tabs(tab_names)

with tab_r:
    show_table(receipts, EntryType.RECEIPT)
with tab_s:
    st.caption("Cleared cheques only, in order of clearance date (date filter applies to the clearance date).")
    show_table(statement, EntryType.EXPENSE, date_col="Cleared On",
               empty_msg="No cleared cheques for this selection.")
with tab_reg:
    st.caption("Every cheque issued in this FY, cleared or pending (date filter applies to the cheque date).")
    show_table(register, EntryType.EXPENSE, empty_msg="No cheques for this selection.")

if rest:
    with rest[0]:
        kind = st.radio("Entry type", [EntryType.RECEIPT, EntryType.EXPENSE],
                        format_func=lambda t: "Receipt (challan)" if t is EntryType.RECEIPT else "Expenditure (cheque)",
                        horizontal=True)
        df = receipts if kind is EntryType.RECEIPT else register
        if df.empty:
            st.info("Nothing to edit yet.")
        else:
            options = df.sort_values(["Date", "id"], ascending=False)

            def label(r) -> str:
                text = f"{r.Date:%d/%m/%Y} · {kind.ref_label} {r[kind.ref_label]} · {inr(r.TOTAL)}"
                return text + (f" · {r.Status}" if kind is EntryType.EXPENSE else "")

            labels = {int(r.id): label(r) for _, r in options.iterrows()}
            entry_id = st.selectbox("Select entry", list(labels), format_func=labels.get)
            with db() as s:
                entry = get_entry(s, entry_id)
            if entry:
                entry_form(kind, key=f"edit_{entry.id}", existing=entry)
