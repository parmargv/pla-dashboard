import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from pla.models import EntryType
from pla.service import get_head_summary, list_entries, monthly_summary
from ui.common import db, inr, money_column, selected_fy, show_flash

def render() -> None:
    fy = selected_fy()
    st.title("📊 PLA account dashboard")
    st.caption(f"Financial year {fy} · 1 April – 31 March")
    show_flash()

    with db() as s:
        summary = get_head_summary(s, fy)
        monthly = monthly_summary(s, fy)
        all_entries = list_entries(s, fy)
    recent = all_entries[-10:][::-1]

    opening = summary["Opening Balance"].sum()
    receipt = summary["Total Receipt"].sum()
    grand = summary["Grand Total"].sum()
    expense = summary["Total Expense"].sum()
    closing = summary["Closing Balance"].sum()
    pending_amt = summary["Pending Cheques"].sum()
    available = summary["Available Balance"].sum()
    pending_count = sum(1 for e in all_entries if e.entry_type is EntryType.EXPENSE and not e.cleared_on)

    k = st.columns(5)
    k[0].metric("Opening balance", inr(opening))
    k[1].metric("Total receipt", inr(receipt))
    k[2].metric("Grand total", inr(grand))
    k[3].metric("Total expense", inr(expense), help="Cleared cheques only (as per statement)")
    k[4].metric("Net (closing) balance", inr(closing),
                delta=f"{(expense / grand * 100):.1f}% utilised" if grand else None, delta_color="off",
                help="Grand total − cleared cheques")
    k2 = st.columns(5)
    k2[3].metric(f"Pending cheques ({pending_count})", inr(pending_amt),
                 help="Issued but not yet cleared — not in the statement, but already reserved")
    k2[4].metric("Available balance", inr(available), help="Net balance − pending cheques: what can still be spent")
    if pending_count:
        k2[0].info(f"{pending_count} cheque(s) awaiting clearance — update them on **Cheque clearance**.", icon="🧾")

    negative = summary[summary["Available Balance"] < 0]
    if not negative.empty:
        st.error("Negative balance in: " + ", ".join(negative["Fund Head"]))

    st.subheader("Head-wise position")
    table = summary.drop(columns="fund_head_id")
    active = table[(table.drop(columns="Fund Head").abs().sum(axis=1)) > 0]
    show_all = st.toggle("Show all fund heads", value=active.empty)
    view = table if show_all else active
    totals = pd.DataFrame([{"Fund Head": "TOTAL", **view.drop(columns="Fund Head").sum().to_dict()}])
    st.dataframe(
        pd.concat([view, totals], ignore_index=True),
        hide_index=True,
        width="stretch",
        column_config={c: money_column() for c in table.columns if c != "Fund Head"},
    )

    if active.empty:
        st.info("No data for this financial year yet. Start with **Opening balance**, then add challans and cheques.")
        return

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Receipt vs expense by head")
        long = active.melt(id_vars="Fund Head", value_vars=["Total Receipt", "Total Expense"],
                           var_name="Type", value_name="Amount")
        fig = px.bar(long, x="Amount", y="Fund Head", color="Type", barmode="group", orientation="h",
                     color_discrete_map={"Total Receipt": "#2E7D5B", "Total Expense": "#C0504D"})
        fig.update_layout(height=max(320, 40 * len(active)), margin=dict(l=0, r=0, t=10, b=0),
                          legend_title=None, yaxis_title=None, xaxis_title="₹")
        st.plotly_chart(fig, width="stretch")
    with c2:
        st.subheader("Closing balance by head")
        bal = active.sort_values("Closing Balance")
        fig = px.bar(bal, x="Closing Balance", y="Fund Head", orientation="h",
                     color=bal["Closing Balance"] >= 0, color_discrete_map={True: "#1F3A5F", False: "#C0504D"})
        fig.update_layout(height=max(320, 40 * len(active)), margin=dict(l=0, r=0, t=10, b=0),
                          showlegend=False, yaxis_title=None, xaxis_title="₹")
        st.plotly_chart(fig, width="stretch")

    st.subheader("Monthly flow")
    monthly["Net balance"] = opening + (monthly["Receipt"] - monthly["Expense"]).cumsum()
    fig = go.Figure()
    fig.add_bar(x=monthly["Month"], y=monthly["Receipt"], name="Receipt", marker_color="#2E7D5B")
    fig.add_bar(x=monthly["Month"], y=monthly["Expense"], name="Expense", marker_color="#C0504D")
    fig.add_scatter(x=monthly["Month"], y=monthly["Net balance"], name="Running balance",
                    mode="lines+markers", line=dict(color="#1F3A5F", width=3))
    fig.update_layout(barmode="group", height=360, margin=dict(l=0, r=0, t=10, b=0), yaxis_title="₹",
                      legend=dict(orientation="h", y=1.1))
    st.plotly_chart(fig, width="stretch")

    st.subheader("Latest entries")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Date": e.entry_date,
                    "Type": e.entry_type.title,
                    "No.": e.ref_no,
                    "Amount": float(e.total),
                    "Status": "" if e.entry_type is EntryType.RECEIPT else (
                        f"Cleared {e.cleared_on:%d/%m/%Y}" if e.cleared_on else "Pending"),
                    "Remarks": e.remarks or "",
                }
                for e in recent
            ]
        ),
        hide_index=True,
        width="stretch",
        column_config={"Date": st.column_config.DateColumn(format="DD/MM/YYYY"), "Amount": money_column()},
    )


render()
