from datetime import date, timedelta
from decimal import Decimal

import os

import pytest

from pla.config import normalize_database_url
from pla.db import init_db, session_scope
from pla.excel_export import build_pla_workbook
from pla.models import Entry, EntryType
from pla.service import (
    PLAValidationError,
    carry_forward,
    delete_entry,
    entries_wide,
    get_fund_heads,
    get_head_summary,
    get_opening_balances,
    head_balances,
    pending_cheques,
    save_entry,
    set_clearance,
    set_opening_balances,
)

FY = "2026-27"


def save_cheque(s, *, entry_date, ref_no, amounts, cleared=True, cheque_amount=None, **kw):
    """Cheque whose amount equals its fund-head total, cleared on its own date by default."""
    return save_entry(
        s, entry_type=EntryType.EXPENSE, entry_date=entry_date, ref_no=ref_no, amounts=amounts,
        cheque_amount=sum(amounts.values()) if cheque_amount is None else cheque_amount,
        cleared_on=entry_date if cleared else None, **kw,
    )


@pytest.fixture()
def db(tmp_path):
    # Set PG_TEST_URL=postgresql://user:pass@host/db to run against PostgreSQL (wipes that DB).
    url = normalize_database_url(os.environ.get("PG_TEST_URL") or f"sqlite:///{tmp_path}/test.db")
    if url.startswith("postgres"):
        from pla.db import get_engine
        from pla.models import Base
        Base.metadata.drop_all(get_engine(url))
    init_db(url)
    with session_scope(url) as s:
        heads = {h.name: h.id for h in get_fund_heads(s)}
    return url, heads


def test_seeded_heads_match_sheet(db):
    _, heads = db
    assert len(heads) == 17 and "GF" in heads and "NCC/OTHER" in heads


def test_receipt_expense_and_balances(db):
    url, h = db
    with session_scope(url) as s:
        set_opening_balances(s, FY, {h["GF"]: 1000, h["DF"]: 500})
        save_entry(s, entry_type=EntryType.RECEIPT, entry_date=date(2026, 5, 2), ref_no="CH-1",
                   amounts={h["GF"]: 2000, h["NSS"]: 300})
        save_cheque(s, entry_date=date(2026, 6, 1), ref_no="B-1",
                   amounts={h["GF"]: 2500, h["DF"]: 100})
    with session_scope(url) as s:
        bal = head_balances(s, FY)
        assert bal[h["GF"]] == Decimal("500.00")
        assert bal[h["DF"]] == Decimal("400.00")
        assert bal[h["NSS"]] == Decimal("300.00")
        summary = get_head_summary(s, FY).set_index("Fund Head")
        assert summary.loc["GF", "Grand Total"] == 3000
        assert summary["Closing Balance"].sum() == 1200
        exp = entries_wide(s, FY, EntryType.EXPENSE)
        assert exp.iloc[0]["BALANCE"] == 3800 - 2600


def test_overdraft_blocked(db):
    url, h = db
    with session_scope(url) as s, pytest.raises(PLAValidationError, match="Insufficient"):
        save_cheque(s, entry_date=date(2026, 6, 1), ref_no="B-9",
                   amounts={h["GF"]: 1})


def test_duplicate_challan_rejected_but_allowed_next_fy(db):
    url, h = db
    with session_scope(url) as s:
        save_entry(s, entry_type=EntryType.RECEIPT, entry_date=date(2026, 5, 2), ref_no="7", amounts={h["GF"]: 1})
        with pytest.raises(PLAValidationError, match="already exists"):
            save_entry(s, entry_type=EntryType.RECEIPT, entry_date=date(2026, 9, 2), ref_no="7", amounts={h["GF"]: 1})
        save_entry(s, entry_type=EntryType.RECEIPT, entry_date=date(2027, 4, 2), ref_no="7", amounts={h["GF"]: 1})


def test_edit_and_delete_respect_balances(db):
    url, h = db
    with session_scope(url) as s:
        r = save_entry(s, entry_type=EntryType.RECEIPT, entry_date=date(2026, 5, 2), ref_no="CH-1",
                       amounts={h["GF"]: 1000})
        b = save_cheque(s, entry_date=date(2026, 5, 3), ref_no="B-1",
                       amounts={h["GF"]: 800})
        rid, bid = r.id, b.id
    with session_scope(url) as s:
        # editing the bill up to the full balance is fine (its own 800 is "given back" first)
        save_cheque(s, entry_date=date(2026, 5, 3), ref_no="B-1",
                   amounts={h["GF"]: 1000}, entry_id=bid)
        # reducing the receipt below what was spent is not
        with pytest.raises(PLAValidationError):
            save_entry(s, entry_type=EntryType.RECEIPT, entry_date=date(2026, 5, 2), ref_no="CH-1",
                       amounts={h["GF"]: 10}, entry_id=rid)
        with pytest.raises(PLAValidationError):
            delete_entry(s, rid)
        delete_entry(s, bid)
        delete_entry(s, rid)
        assert head_balances(s, FY)[h["GF"]] == 0


def test_carry_forward(db):
    url, h = db
    with session_scope(url) as s:
        save_entry(s, entry_type=EntryType.RECEIPT, entry_date=date(2026, 5, 2), ref_no="1", amounts={h["SF"]: 750})
        carry_forward(s, FY, "2027-28")
        assert get_opening_balances(s, "2027-28")[h["SF"]] == Decimal("750.00")


def test_excel_export(db, tmp_path):
    url, h = db
    with session_scope(url) as s:
        set_opening_balances(s, FY, {h["GF"]: 1000})
        save_entry(s, entry_type=EntryType.RECEIPT, entry_date=date(2026, 5, 2), ref_no="CH-1", amounts={h["GF"]: 500})
        save_cheque(s, entry_date=date(2026, 6, 2), ref_no="B-1", amounts={h["GF"]: 200})
        save_cheque(s, entry_date=date(2026, 6, 3), ref_no="B-2", amounts={h["GF"]: 300})
        heads = get_fund_heads(s)
        opening = {x.name: float(get_opening_balances(s, FY).get(x.id, 0)) for x in heads}
        data = build_pla_workbook(FY, [x.name for x in heads], opening,
                                  entries_wide(s, FY, EntryType.RECEIPT), entries_wide(s, FY, EntryType.EXPENSE),
                                  pending=entries_wide(s, FY, EntryType.EXPENSE, cleared=False))
    (tmp_path / "out.xlsx").write_bytes(data)
    assert data[:2] == b"PK"


# --------------------------------------------------------------------------- #
# Cheques
# --------------------------------------------------------------------------- #
def test_cheque_amount_must_match_fund_heads(db):
    url, h = db
    with session_scope(url) as s:
        set_opening_balances(s, FY, {h["CMD"]: 10000, h["GKS"]: 10000, h["NSS"]: 10000})
        split = {h["CMD"]: 2300, h["GKS"]: 1000, h["NSS"]: 100}  # = 3400
        with pytest.raises(PLAValidationError, match="mismatch"):
            save_entry(s, entry_type=EntryType.EXPENSE, entry_date=date(2026, 6, 1), ref_no="CHQ-1",
                       amounts=split, cheque_amount=5400)
        with pytest.raises(PLAValidationError, match="Cheque amount is required"):
            save_entry(s, entry_type=EntryType.EXPENSE, entry_date=date(2026, 6, 1), ref_no="CHQ-1", amounts=split)
        e = save_entry(s, entry_type=EntryType.EXPENSE, entry_date=date(2026, 6, 1), ref_no="CHQ-1",
                       amounts=split, cheque_amount=3400)
        assert e.cheque_amount == Decimal("3400.00") and e.cleared_on is None


def test_pending_cheque_not_in_statement_but_reserves_balance(db):
    url, h = db
    with session_scope(url) as s:
        set_opening_balances(s, FY, {h["GF"]: 1000})
        chq = save_cheque(s, entry_date=date(2026, 6, 1), ref_no="CHQ-1", amounts={h["GF"]: 700}, cleared=False)
        cid = chq.id
        summary = get_head_summary(s, FY).set_index("Fund Head")
        assert summary.loc["GF", "Total Expense"] == 0          # not in statement yet
        assert summary.loc["GF", "Closing Balance"] == 1000
        assert summary.loc["GF", "Pending Cheques"] == 700
        assert summary.loc["GF", "Available Balance"] == 300
        assert entries_wide(s, FY, EntryType.EXPENSE).empty      # statement
        assert len(entries_wide(s, FY, EntryType.EXPENSE, cleared=None)) == 1  # register
        assert [e.id for e in pending_cheques(s)] == [cid]
        with pytest.raises(PLAValidationError, match="Insufficient"):  # only 300 left to commit
            save_cheque(s, entry_date=date(2026, 6, 2), ref_no="CHQ-2", amounts={h["GF"]: 400}, cleared=False)

    with session_scope(url) as s:  # clear it later
        with pytest.raises(PLAValidationError, match="before the cheque date"):
            set_clearance(s, cid, date(2026, 5, 1))
        with pytest.raises(PLAValidationError, match="future"):
            set_clearance(s, cid, date.today() + timedelta(days=1))
        set_clearance(s, cid, date(2026, 6, 10))
    with session_scope(url) as s:
        summary = get_head_summary(s, FY).set_index("Fund Head")
        assert summary.loc["GF", "Total Expense"] == 700 and summary.loc["GF", "Closing Balance"] == 300
        st = entries_wide(s, FY, EntryType.EXPENSE)
        assert st.iloc[0]["Cleared On"] == date(2026, 6, 10) and st.iloc[0]["BALANCE"] == 300
        assert pending_cheques(s) == []
        set_clearance(s, cid, None)  # undo
        assert len(pending_cheques(s)) == 1


def test_receipt_cannot_have_clearance(db):
    url, h = db
    with session_scope(url) as s:
        r = save_entry(s, entry_type=EntryType.RECEIPT, entry_date=date(2026, 5, 1), ref_no="CH",
                       amounts={h["GF"]: 10}, cleared_on=date(2026, 5, 2))
        assert r.cleared_on is None and r.cheque_amount is None
        with pytest.raises(PLAValidationError):
            set_clearance(s, r.id, date(2026, 5, 2))


def test_migration_backfills_existing_database(tmp_path):
    """An old database (without cheque columns) is upgraded in place on start-up."""
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE fund_heads (id INTEGER PRIMARY KEY, name VARCHAR(120) UNIQUE, sort_order INTEGER, is_active BOOLEAN);
        CREATE TABLE entries (id INTEGER PRIMARY KEY, entry_type VARCHAR(7), fiscal_year VARCHAR(7), entry_date DATE,
            ref_no VARCHAR(60), remarks TEXT, created_at DATETIME, updated_at DATETIME);
        CREATE TABLE entry_lines (id INTEGER PRIMARY KEY, entry_id INTEGER, fund_head_id INTEGER, amount NUMERIC(14,2));
        INSERT INTO fund_heads VALUES (1, 'GF', 1, 1);
        INSERT INTO entries VALUES (1, 'EXPENSE', '2026-27', '2026-06-01', 'B-1', NULL, NULL, NULL);
        INSERT INTO entry_lines VALUES (1, 1, 1, 250.00);
    """)
    con.commit()
    con.close()
    url = f"sqlite:///{path}"
    init_db(url)
    with session_scope(url) as s:
        e = s.get(Entry, 1)
        assert e.cheque_amount == Decimal("250.00") and e.cleared_on == date(2026, 6, 1)


def test_database_url_normalisation():
    from pla.config import normalize_database_url as n
    neon = "postgresql://u:p@ep-x.aws.neon.tech/neondb?sslmode=require"
    assert n(neon) == "postgresql+psycopg://u:p@ep-x.aws.neon.tech/neondb?sslmode=require"
    assert n("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert n("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert n("sqlite:///data/pla.db") == "sqlite:///data/pla.db"
