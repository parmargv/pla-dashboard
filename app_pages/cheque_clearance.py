from datetime import date

import pandas as pd
import streamlit as st

from pla.fiscal import fy_bounds
from pla.service import PLAValidationError, entries_wide, pending_cheques, set_clearance
from pla.models import EntryType
from ui.common import db, flash, inr, money_column, selected_fy, show_flash


def render() -> None:
    st.title("🧾 Cheque clearance")
    st.caption("Enter the date each cheque was cleared by the bank. Cleared cheques move into the statement.")
    show_flash()

    with db() as s:
        pending = pending_cheques(s)

    st.subheader(f"Pending cheques ({len(pending)})")
    if not pending:
        st.success("No pending cheques — everything is cleared.", icon="✅")
    else:
        st.metric("Total pending amount", inr(float(sum(e.total for e in pending))))
        ver = st.session_state.setdefault("clr_ver", 0)
        grid = pd.DataFrame(
            {
                "id": [e.id for e in pending],
                "Cheque date": [e.entry_date for e in pending],
                "Cheque No": [e.ref_no for e in pending],
                "FY": [e.fiscal_year for e in pending],
                "Amount": [float(e.total) for e in pending],
                "Days pending": [(date.today() - e.entry_date).days for e in pending],
                "Remarks": [e.remarks or "" for e in pending],
                "Clearance date": [None] * len(pending),
            }
        )
        grid["Clearance date"] = pd.to_datetime(grid["Clearance date"]).dt.date
        edited = st.data_editor(
            grid,
            key=f"clr_grid_{ver}",
            hide_index=True,
            width="stretch",
            disabled=[c for c in grid.columns if c != "Clearance date"],
            column_config={
                "id": None,
                "Cheque date": st.column_config.DateColumn(format="DD/MM/YYYY"),
                "Amount": money_column(),
                "Clearance date": st.column_config.DateColumn(
                    "Clearance date ✏️", format="DD/MM/YYYY", max_value=date.today(),
                    help="Double-click to pick the date the bank cleared the cheque",
                ),
            },
        )
        to_save = edited.dropna(subset=["Clearance date"])
        if st.button(f"Save clearance dates ({len(to_save)})", type="primary", disabled=to_save.empty):
            errors, saved = [], 0
            for _, row in to_save.iterrows():
                try:
                    with db() as s:
                        set_clearance(s, int(row["id"]), pd.Timestamp(row["Clearance date"]).date())
                    saved += 1
                except PLAValidationError as exc:
                    errors.append(f"Cheque {row['Cheque No']}: {exc}")
            if errors:
                st.error("\n\n".join(errors))
            if saved:
                flash(f"{saved} cheque(s) marked as cleared.")
                st.session_state["clr_ver"] = ver + 1
                if not errors:
                    st.rerun()

    st.divider()
    fy = selected_fy()
    st.subheader(f"Cleared cheques · FY {fy}")
    with db() as s:
        cleared = entries_wide(s, fy, EntryType.EXPENSE, cleared=True)
    if cleared.empty:
        st.info("No cleared cheques in this financial year.")
        return
    st.dataframe(
        cleared[["Cleared On", "Date", "Cheque No", "TOTAL", "Remarks"]].rename(
            columns={"Date": "Cheque date", "TOTAL": "Amount"}
        ),
        hide_index=True,
        width="stretch",
        column_config={
            "Cleared On": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Cheque date": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Amount": money_column(),
        },
    )
    with st.expander("Entered a wrong clearance date? Undo it"):
        labels = {
            int(r.id): f"Cheque {r['Cheque No']} · cleared {r['Cleared On']:%d/%m/%Y} · {inr(r.TOTAL)}"
            for _, r in cleared.iterrows()
        }
        cid = st.selectbox("Cheque", list(labels), format_func=labels.get)
        if st.button("Mark as not cleared"):
            with db() as s:
                set_clearance(s, cid, None)
            flash("Cheque moved back to pending.", "info")
            st.rerun()


render()
