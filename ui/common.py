"""Shared Streamlit helpers used by every page."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pandas as pd
import streamlit as st
from sqlalchemy.exc import IntegrityError

from pla.config import get_settings
from pla.db import session_scope
from pla.fiscal import fiscal_year_of
from pla.models import Entry, EntryType
from pla.service import (
    PLAValidationError,
    available_fiscal_years,
    delete_entry,
    get_fund_heads,
    head_balances,
    save_entry,
)

FLASH_KEY = "_flash"


@contextmanager
def db():
    with session_scope(get_settings().database_url) as session:
        yield session


# --------------------------------------------------------------------------- #
# Financial year selection (shared across pages)
# --------------------------------------------------------------------------- #
def sidebar_fy_selector() -> None:
    with db() as s:
        years = available_fiscal_years(s)
    current = st.session_state.get("fy", fiscal_year_of(date.today()))
    if current not in years:
        years = sorted({*years, current}, reverse=True)
    st.sidebar.selectbox("Financial year", years, index=years.index(current), key="fy")


def selected_fy() -> str:
    return st.session_state.get("fy", fiscal_year_of(date.today()))


# --------------------------------------------------------------------------- #
# Formatting & flash messages
# --------------------------------------------------------------------------- #
def inr(value: float, decimals: int = 2) -> str:
    """Indian digit grouping: 1234567.5 -> '₹12,34,567.50'."""
    neg = value < 0
    whole, _, frac = f"{abs(value):.{decimals}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{'-' if neg else ''}₹{whole}{'.' + frac if frac else ''}"


def flash(message: str, kind: str = "success") -> None:
    st.session_state[FLASH_KEY] = (kind, message)


def show_flash() -> None:
    item = st.session_state.pop(FLASH_KEY, None)
    if item:
        kind, message = item
        getattr(st, kind)(message, icon="✅" if kind == "success" else None)


def money_column(label: str | None = None, **kw):
    return st.column_config.NumberColumn(label, format="accounting", **kw)


# --------------------------------------------------------------------------- #
# Challan / bill entry form (used for both "add" and "edit")
# --------------------------------------------------------------------------- #
def entry_form(entry_type: EntryType, *, key: str, existing: Entry | None = None) -> None:
    is_expense = entry_type is EntryType.EXPENSE
    version = st.session_state.setdefault(f"{key}_ver", 0)
    k = f"{key}_{version}"

    cheque_amount: float | None = None
    cleared_on: date | None = None
    if is_expense:
        c1, c2, c3, c4 = st.columns(4)
        entry_date = c1.date_input(
            "Cheque date", value=existing.entry_date if existing else date.today(), format="DD/MM/YYYY",
            key=f"{k}_date",
        )
        ref_no = c2.text_input("Cheque No", value=existing.ref_no if existing else "", key=f"{k}_ref")
        cheque_amount = c3.number_input(
            "Cheque amount (₹)", min_value=0.0, step=1.0, format="%.2f",
            value=float(existing.cheque_amount or existing.total) if existing else 0.0, key=f"{k}_chq",
        )
        cleared_on = c4.date_input(
            "Clearance date", value=existing.cleared_on if existing else None, format="DD/MM/YYYY",
            max_value=date.today(), key=f"{k}_clr",
            help="Leave blank if the cheque is not cleared yet. You can enter it later on the Cheque clearance page.",
        )
        remarks = st.text_input(
            "Particulars / remarks (optional)", value=(existing.remarks or "") if existing else "", key=f"{k}_rem"
        )
    else:
        c1, c2, c3 = st.columns([1, 1, 2])
        entry_date = c1.date_input(
            "Date", value=existing.entry_date if existing else date.today(), format="DD/MM/YYYY", key=f"{k}_date"
        )
        ref_no = c2.text_input(entry_type.ref_label, value=existing.ref_no if existing else "", key=f"{k}_ref")
        remarks = c3.text_input(
            "Particulars / remarks (optional)", value=(existing.remarks or "") if existing else "", key=f"{k}_rem"
        )

    fy = fiscal_year_of(entry_date)
    if fy != selected_fy():
        st.caption(f"ℹ️ This date falls in FY **{fy}**; the entry will be recorded there.")

    with db() as s:
        heads = get_fund_heads(s)
        balances = head_balances(s, fy, exclude_entry=existing) if is_expense else {}

    existing_amounts = {l.fund_head_id: float(l.amount) for l in existing.lines} if existing else {}
    grid = pd.DataFrame(
        {
            "head_id": [h.id for h in heads],
            "Fund Head": [h.name for h in heads],
            **({"Available": [float(balances.get(h.id, 0)) for h in heads]} if is_expense else {}),
            "Amount": [existing_amounts.get(h.id, 0.0) for h in heads],
        }
    )
    edited = st.data_editor(
        grid,
        key=f"{k}_grid",
        hide_index=True,
        width="stretch",
        height=36 * (len(heads) + 1) + 3,
        disabled=["Fund Head", "Available"],
        column_config={
            "head_id": None,
            "Fund Head": st.column_config.TextColumn(width="large"),
            "Available": money_column("Available balance (₹)"),
            "Amount": st.column_config.NumberColumn("Amount (₹)", min_value=0.0, step=1.0, format="%.2f"),
        },
    )

    amounts = edited.set_index("head_id")["Amount"].fillna(0)
    total = float(amounts.sum())
    if is_expense:
        diff = round((cheque_amount or 0) - total, 2)
        m1, m2, m3 = st.columns(3)
        m1.metric("Cheque amount", inr(cheque_amount or 0))
        m2.metric("Fund heads total", inr(total))
        m3.metric("Difference", inr(diff))
        if (cheque_amount or total) and diff == 0:
            st.success("Cheque amount matches the fund-head total.", icon="✅")
        elif cheque_amount or total:
            st.error(
                f"Mismatch: fund heads are {'short' if diff > 0 else 'over'} by {inr(abs(diff))}. "
                "The cheque cannot be saved until both amounts are equal.",
                icon="❌",
            )
        over = edited[edited["Amount"].fillna(0) > edited["Available"] + 1e-9]
        if not over.empty:
            st.warning("Exceeds available balance: " + ", ".join(over["Fund Head"]), icon="⚠️")
    else:
        st.metric(f"{entry_type.title} total", inr(total))

    b1, b2, _ = st.columns([1, 1, 4])
    label = "Update" if existing else ("Save cheque" if is_expense else "Save challan")
    if b1.button(label, type="primary", key=f"{k}_save", width="stretch"):
        try:
            with db() as s:
                saved = save_entry(
                    s,
                    entry_type=entry_type,
                    entry_date=entry_date,
                    ref_no=ref_no,
                    amounts=amounts.to_dict(),
                    remarks=remarks,
                    entry_id=existing.id if existing else None,
                    cheque_amount=cheque_amount,
                    cleared_on=cleared_on,
                )
                msg = f"{entry_type.ref_label} {saved.ref_no} {'updated' if existing else 'saved'} — {inr(float(saved.total))} (FY {saved.fiscal_year})"
                if is_expense and saved.cleared_on is None:
                    msg += " · pending clearance (not yet in statement)"
        except PLAValidationError as exc:
            st.error(str(exc))
        except IntegrityError:
            st.error(f"{entry_type.ref_label} '{ref_no}' was just saved by someone else. Please refresh.")
        else:
            flash(msg)
            st.session_state[f"{key}_ver"] = version + 1  # reset the form
            st.rerun()

    if existing:
        confirm = b2.toggle("Confirm delete", key=f"{k}_confirm")
        if b2.button("Delete", key=f"{k}_del", disabled=not confirm, width="stretch"):
            try:
                with db() as s:
                    delete_entry(s, existing.id)
            except PLAValidationError as exc:
                st.error(str(exc))
            else:
                flash(f"{entry_type.ref_label} {existing.ref_no} deleted.", "info")
                st.session_state[f"{key}_ver"] = version + 1
                st.rerun()
