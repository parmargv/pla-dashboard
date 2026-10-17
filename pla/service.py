"""Business logic for the PLA ledger. Framework-agnostic: every function takes a
SQLAlchemy Session, so it is usable from Streamlit, a CLI, or tests alike.

Accounting identities (per fund head, per financial year), mirroring the sheet:
    GRAND TOTAL      = OPENING BALANCE + TOTAL RECEIPT
    CLOSING BALANCE  = GRAND TOTAL - TOTAL EXPENSE          (cleared cheques only)
    AVAILABLE        = CLOSING BALANCE - PENDING CHEQUES    (what can still be spent)

Expenditure is paid by cheque. A cheque appears in the statement (and in TOTAL
EXPENSE / CLOSING BALANCE) only once its clearance date is entered, but it
reserves its amount immediately so the same money cannot be committed twice.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Mapping

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .fiscal import FY_MONTHS, fiscal_year_of
from .fund_heads import DEFAULT_FUND_HEADS
from .models import Entry, EntryLine, EntryType, FundHead, OpeningBalance

ZERO = Decimal("0.00")


class PLAValidationError(ValueError):
    """Raised for user-correctable problems (shown as-is in the UI)."""


def to_money(value) -> Decimal:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ZERO
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- #
# Fund heads
# --------------------------------------------------------------------------- #
def seed_fund_heads(session: Session) -> None:
    existing = set(session.scalars(select(FundHead.name)))
    for order, name in enumerate(DEFAULT_FUND_HEADS, start=1):
        if name not in existing:
            session.add(FundHead(name=name, sort_order=order, is_active=True))


def get_fund_heads(session: Session, active_only: bool = True) -> list[FundHead]:
    stmt = select(FundHead).order_by(FundHead.sort_order, FundHead.id)
    if active_only:
        stmt = stmt.where(FundHead.is_active.is_(True))
    return list(session.scalars(stmt))


# --------------------------------------------------------------------------- #
# Balances
# --------------------------------------------------------------------------- #
def _sum_by_head(
    session: Session, fy: str, entry_type: EntryType, cleared: bool | None = None
) -> dict[int, Decimal]:
    """cleared=None: all entries; True: cleared cheques only; False: pending cheques only."""
    stmt = (
        select(EntryLine.fund_head_id, func.sum(EntryLine.amount))
        .join(Entry, Entry.id == EntryLine.entry_id)
        .where(Entry.fiscal_year == fy, Entry.entry_type == entry_type)
        .group_by(EntryLine.fund_head_id)
    )
    if cleared is True:
        stmt = stmt.where(Entry.cleared_on.is_not(None))
    elif cleared is False:
        stmt = stmt.where(Entry.cleared_on.is_(None))
    return {head_id: to_money(total) for head_id, total in session.execute(stmt)}


def get_opening_balances(session: Session, fy: str) -> dict[int, Decimal]:
    stmt = select(OpeningBalance.fund_head_id, OpeningBalance.amount).where(OpeningBalance.fiscal_year == fy)
    return {head_id: to_money(amount) for head_id, amount in session.execute(stmt)}


def head_balances(
    session: Session, fy: str, exclude_entry: Entry | None = None, statement: bool = False
) -> dict[int, Decimal]:
    """Balance per fund head for the FY.

    statement=False (default): AVAILABLE balance – every cheque issued is deducted,
    cleared or not. Used for all "can this be spent?" checks.
    statement=True: CLOSING balance as per statement – cleared cheques only.
    `exclude_entry` computes the balance as if that entry did not exist (for edits).
    """
    opening = get_opening_balances(session, fy)
    receipts = _sum_by_head(session, fy, EntryType.RECEIPT)
    expenses = _sum_by_head(session, fy, EntryType.EXPENSE, cleared=True if statement else None)
    balances = {
        h.id: opening.get(h.id, ZERO) + receipts.get(h.id, ZERO) - expenses.get(h.id, ZERO)
        for h in get_fund_heads(session, active_only=False)
    }
    if exclude_entry is not None and exclude_entry.fiscal_year == fy and (
        not statement or exclude_entry.is_cleared
    ):
        sign = -1 if exclude_entry.entry_type is EntryType.RECEIPT else 1
        for line in exclude_entry.lines:
            balances[line.fund_head_id] = balances.get(line.fund_head_id, ZERO) + sign * line.amount
    return balances


def get_head_summary(session: Session, fy: str) -> pd.DataFrame:
    """One row per fund head – the bottom half of the PLA sheet."""
    opening = get_opening_balances(session, fy)
    receipts = _sum_by_head(session, fy, EntryType.RECEIPT)
    cleared = _sum_by_head(session, fy, EntryType.EXPENSE, cleared=True)
    pending = _sum_by_head(session, fy, EntryType.EXPENSE, cleared=False)
    rows = []
    for h in get_fund_heads(session):
        o, r = opening.get(h.id, ZERO), receipts.get(h.id, ZERO)
        e, p = cleared.get(h.id, ZERO), pending.get(h.id, ZERO)
        rows.append(
            {
                "fund_head_id": h.id,
                "Fund Head": h.name,
                "Opening Balance": float(o),
                "Total Receipt": float(r),
                "Grand Total": float(o + r),
                "Total Expense": float(e),
                "Closing Balance": float(o + r - e),
                "Pending Cheques": float(p),
                "Available Balance": float(o + r - e - p),
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Entries (challans / bills)
# --------------------------------------------------------------------------- #
def _signed_effect(entry_type: EntryType, amounts: Mapping[int, Decimal]) -> dict[int, Decimal]:
    sign = 1 if entry_type is EntryType.RECEIPT else -1
    return {head_id: sign * amt for head_id, amt in amounts.items()}


def _assert_no_overdraft(
    session: Session, deltas: dict[str, dict[int, Decimal]], head_names: dict[int, str]
) -> None:
    """`deltas` = {fy: {head_id: change to closing balance}}. Reject changes that
    would push a head's closing balance below zero."""
    problems: list[str] = []
    for fy, per_head in deltas.items():
        current = head_balances(session, fy)
        for head_id, delta in per_head.items():
            if delta >= 0:
                continue
            after = current.get(head_id, ZERO) + delta
            if after < 0:
                problems.append(
                    f"**{head_names.get(head_id, head_id)}** (FY {fy}): available "
                    f"₹{current.get(head_id, ZERO):,.2f}, change ₹{delta:,.2f} → would be ₹{after:,.2f}"
                )
    if problems:
        raise PLAValidationError("Insufficient balance:\n\n- " + "\n- ".join(problems))


def save_entry(
    session: Session,
    *,
    entry_type: EntryType,
    entry_date: date,
    ref_no: str,
    amounts: Mapping[int, object],
    remarks: str | None = None,
    entry_id: int | None = None,
    allow_overdraft: bool = False,
    cheque_amount: object = None,
    cleared_on: date | None = None,
) -> Entry:
    """Create (entry_id=None) or update a challan/cheque and its per-head amounts.

    For expenditure, `cheque_amount` is required and must equal the sum of the
    fund-head amounts; `cleared_on` is optional (can be set later via set_clearance).
    """
    ref_no = (ref_no or "").strip()
    if not ref_no:
        raise PLAValidationError(f"{entry_type.ref_label} is required.")
    if entry_date is None:
        raise PLAValidationError("Date is required.")

    clean = {int(h): to_money(a) for h, a in amounts.items()}
    if any(a < 0 for a in clean.values()):
        raise PLAValidationError("Amounts cannot be negative.")
    clean = {h: a for h, a in clean.items() if a > 0}
    if not clean and entry_type is EntryType.RECEIPT:
        raise PLAValidationError("Enter an amount for at least one fund head.")

    heads = {h.id: h.name for h in get_fund_heads(session, active_only=False)}
    unknown = set(clean) - set(heads)
    if unknown:
        raise PLAValidationError(f"Unknown fund head id(s): {sorted(unknown)}")

    cheque: Decimal | None = None
    if entry_type is EntryType.EXPENSE:
        cheque = to_money(cheque_amount)
        if cheque <= 0:
            raise PLAValidationError("Cheque amount is required.")
        heads_total = sum(clean.values(), ZERO)
        if cheque != heads_total:
            raise PLAValidationError(
                f"Amount mismatch: cheque amount is ₹{cheque:,.2f} but the fund-head amounts add up to "
                f"₹{heads_total:,.2f} (difference ₹{abs(cheque - heads_total):,.2f}). "
                "They must be equal to save."
            )
        if not clean:  # unreachable in practice (mismatch caught above), kept as a guard
            raise PLAValidationError("Enter an amount for at least one fund head.")
        _validate_clearance(entry_date, cleared_on)
    else:
        cleared_on = None

    fy = fiscal_year_of(entry_date)

    entry: Entry | None = None
    if entry_id is not None:
        entry = session.get(Entry, entry_id)
        if entry is None:
            raise PLAValidationError("This entry no longer exists (it may have been deleted).")
        if entry.entry_type is not entry_type:
            raise PLAValidationError("Entry type cannot be changed.")

    dup_stmt = select(Entry.id).where(
        Entry.entry_type == entry_type, Entry.fiscal_year == fy, Entry.ref_no == ref_no
    )
    if entry_id is not None:
        dup_stmt = dup_stmt.where(Entry.id != entry_id)
    if session.scalar(dup_stmt) is not None:
        raise PLAValidationError(f"{entry_type.ref_label} '{ref_no}' already exists in FY {fy}.")

    if not allow_overdraft:
        # Serialise concurrent writes touching the same heads (row locks on
        # PostgreSQL; a no-op on SQLite, which serialises writers anyway).
        touched = set(clean) | ({l.fund_head_id for l in entry.lines} if entry else set())
        session.execute(select(FundHead.id).where(FundHead.id.in_(touched)).with_for_update())

        deltas: dict[str, dict[int, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
        for h, d in _signed_effect(entry_type, clean).items():
            deltas[fy][h] += d
        if entry is not None:
            old = {l.fund_head_id: l.amount for l in entry.lines}
            for h, d in _signed_effect(entry_type, old).items():
                deltas[entry.fiscal_year][h] -= d
        _assert_no_overdraft(session, deltas, heads)

    if entry is None:
        entry = Entry(entry_type=entry_type)
        session.add(entry)

    entry.entry_date = entry_date
    entry.fiscal_year = fy
    entry.ref_no = ref_no
    entry.remarks = (remarks or "").strip() or None
    entry.cheque_amount = cheque
    entry.cleared_on = cleared_on

    # Update lines in place (avoids delete+insert ordering issues with the unique constraint)
    existing_lines = {l.fund_head_id: l for l in entry.lines}
    for head_id, line in existing_lines.items():
        if head_id not in clean:
            entry.lines.remove(line)
    for head_id, amount in clean.items():
        if head_id in existing_lines:
            existing_lines[head_id].amount = amount
        else:
            entry.lines.append(EntryLine(fund_head_id=head_id, amount=amount))

    session.flush()
    return entry


def _validate_clearance(cheque_date: date, cleared_on: date | None) -> None:
    if cleared_on is None:
        return
    if cleared_on < cheque_date:
        raise PLAValidationError(
            f"Clearance date {cleared_on:%d/%m/%Y} cannot be before the cheque date {cheque_date:%d/%m/%Y}."
        )
    if cleared_on > date.today():
        raise PLAValidationError(f"Clearance date {cleared_on:%d/%m/%Y} cannot be in the future.")


def set_clearance(session: Session, entry_id: int, cleared_on: date | None) -> Entry:
    """Enter (or remove, with None) the clearance date of a cheque after it was saved.

    Balances are unaffected for "can it be spent" purposes (the cheque already
    reserved its amount); only the statement and closing balance change.
    """
    entry = session.get(Entry, entry_id)
    if entry is None:
        raise PLAValidationError("This cheque no longer exists.")
    if entry.entry_type is not EntryType.EXPENSE:
        raise PLAValidationError("Only cheques (expenditure) have a clearance date.")
    _validate_clearance(entry.entry_date, cleared_on)
    entry.cleared_on = cleared_on
    session.flush()
    return entry


def pending_cheques(session: Session) -> list[Entry]:
    """All uncleared cheques, across financial years, oldest first."""
    stmt = (
        select(Entry)
        .where(Entry.entry_type == EntryType.EXPENSE, Entry.cleared_on.is_(None))
        .order_by(Entry.entry_date, Entry.id)
    )
    return list(session.scalars(stmt))


def delete_entry(session: Session, entry_id: int, allow_overdraft: bool = False) -> None:
    entry = session.get(Entry, entry_id)
    if entry is None:
        return
    if entry.entry_type is EntryType.RECEIPT and not allow_overdraft:
        heads = {h.id: h.name for h in get_fund_heads(session, active_only=False)}
        deltas = {entry.fiscal_year: {l.fund_head_id: -l.amount for l in entry.lines}}
        _assert_no_overdraft(session, deltas, heads)
    session.delete(entry)
    session.flush()


def get_entry(session: Session, entry_id: int) -> Entry | None:
    return session.get(Entry, entry_id)


def list_entries(session: Session, fy: str, entry_type: EntryType | None = None) -> list[Entry]:
    stmt = select(Entry).where(Entry.fiscal_year == fy).order_by(Entry.entry_date, Entry.id)
    if entry_type is not None:
        stmt = stmt.where(Entry.entry_type == entry_type)
    return list(session.scalars(stmt))


def entries_wide(
    session: Session, fy: str, entry_type: EntryType, cleared: bool | None = True
) -> pd.DataFrame:
    """Entries in the sheet's wide layout: Date | No | <heads…> | TOTAL [| BALANCE].

    For expenditure:
      cleared=True  -> the STATEMENT: cleared cheques only, ordered by clearance
                       date, with a running BALANCE (grand total − cumulative expense).
      cleared=None  -> cheque register: every cheque with its status (no BALANCE).
      cleared=False -> pending cheques only.
    """
    heads = get_fund_heads(session, active_only=False)
    head_cols = [h.name for h in heads]
    name_by_id = {h.id: h.name for h in heads}
    is_expense = entry_type is EntryType.EXPENSE

    entries = list_entries(session, fy, entry_type)
    if is_expense:
        if cleared is True:
            entries = sorted((e for e in entries if e.cleared_on), key=lambda e: (e.cleared_on, e.entry_date, e.id))
        elif cleared is False:
            entries = [e for e in entries if not e.cleared_on]

    rows = []
    for e in entries:
        row = {"id": e.id, "Date": e.entry_date, entry_type.ref_label: e.ref_no}
        if is_expense:
            row["Cheque Amount"] = float(e.cheque_amount if e.cheque_amount is not None else e.total)
            row["Cleared On"] = e.cleared_on
            row["Status"] = "Cleared" if e.cleared_on else "Pending"
        row.update({c: 0.0 for c in head_cols})
        for line in e.lines:
            row[name_by_id[line.fund_head_id]] = float(line.amount)
        row["TOTAL"] = float(e.total)
        row["Remarks"] = e.remarks or ""
        rows.append(row)

    lead = ["id", "Date", entry_type.ref_label] + (["Cheque Amount", "Cleared On", "Status"] if is_expense else [])
    columns = [*lead, *head_cols, "TOTAL", "Remarks"]
    df = pd.DataFrame(rows, columns=columns)
    if is_expense and cleared is True:
        opening = sum(get_opening_balances(session, fy).values(), ZERO)
        receipts = sum(_sum_by_head(session, fy, EntryType.RECEIPT).values(), ZERO)
        grand_total = float(opening + receipts)
        df.insert(len(columns) - 1, "BALANCE", grand_total - df["TOTAL"].cumsum())
    return df


def monthly_summary(session: Session, fy: str) -> pd.DataFrame:
    """Receipts by challan month; expenses = cleared cheques by clearance month."""
    stmt = (
        select(Entry.entry_date, Entry.cleared_on, Entry.entry_type, func.sum(EntryLine.amount))
        .join(EntryLine, EntryLine.entry_id == Entry.id)
        .where(Entry.fiscal_year == fy)
        .group_by(Entry.id, Entry.entry_date, Entry.cleared_on, Entry.entry_type)
    )
    data = defaultdict(lambda: {"Receipt": 0.0, "Expense": 0.0})
    for d, cleared_on, etype, total in session.execute(stmt):
        if etype is EntryType.RECEIPT:
            data[d.strftime("%b")]["Receipt"] += float(total)
        elif cleared_on is not None:
            data[cleared_on.strftime("%b")]["Expense"] += float(total)
    df = pd.DataFrame([{"Month": m, **data[m]} for m in FY_MONTHS])
    return df


# --------------------------------------------------------------------------- #
# Opening balances & financial years
# --------------------------------------------------------------------------- #
def set_opening_balances(session: Session, fy: str, amounts: Mapping[int, object]) -> None:
    existing = {
        ob.fund_head_id: ob
        for ob in session.scalars(select(OpeningBalance).where(OpeningBalance.fiscal_year == fy))
    }
    for head_id, amount in amounts.items():
        value = to_money(amount)
        if head_id in existing:
            existing[head_id].amount = value
        else:
            session.add(OpeningBalance(fiscal_year=fy, fund_head_id=int(head_id), amount=value))
    session.flush()


def carry_forward(session: Session, from_fy: str, to_fy: str) -> dict[int, Decimal]:
    """Set `to_fy` opening balances = `from_fy` AVAILABLE balances.

    Cheques still pending at year end remain recorded (and reserved) in the FY
    they were issued; they are already deducted here, so the money is not counted twice.
    """
    closing = head_balances(session, from_fy)
    set_opening_balances(session, to_fy, closing)
    return closing


def available_fiscal_years(session: Session) -> list[str]:
    years = set(session.scalars(select(Entry.fiscal_year).distinct()))
    years |= set(session.scalars(select(OpeningBalance.fiscal_year).distinct()))
    years.add(fiscal_year_of(date.today()))
    return sorted(years, reverse=True)
