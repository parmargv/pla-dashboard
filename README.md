# PLA Account Dashboard

Streamlit + SQLAlchemy app that replaces the `PLA_ACCOUNT.xlsx` sheet: enter each
**challan (receipt)** and **cheque (expenditure)** split across the 17 fund heads,
and the database keeps head-wise **opening → receipt → grand total → expense →
closing (net) balance** up to date for every financial year (April–March).

## Features
- **Dashboard** – KPIs (opening, total receipt, grand total, total expense, net balance),
  head-wise position table, receipt-vs-expense and closing-balance charts, monthly flow
  with running balance, latest entries.
- **Add receipt / Add expenditure** – one grid per entry with an amount per fund head.
  Bills show the available balance per head and are rejected if they would overdraw it.
- **Ledger & edit** – the sheet's wide layout (with the running BALANCE column for bills),
  filters, edit/delete with balance checks, and **Excel export in the original PLA layout**
  (totals and balances as live formulas).
- **Opening balance** – per FY, with one-click carry-forward of last year's closing balance.
- Duplicate challan/cheque numbers blocked per financial year.

## Cheques (expenditure)
- Each expenditure has a **cheque no**, **cheque date**, **cheque amount** and optional
  **clearance date**. The cheque amount must equal the sum of the fund-head amounts, or the
  save is refused with a mismatch error (checked in the UI and again in the service layer).
- A cheque appears in the **expenditure statement** (Total expense, Closing balance, running
  BALANCE, Excel export) only once its clearance date is entered, either on the entry form or
  later on the **Cheque clearance** page (bulk entry, with undo).
- Pending cheques still **reserve** their amount: *Available balance = Closing balance −
  Pending cheques*, and new cheques are checked against the available balance.
- Existing expenditure rows from before this change are migrated automatically as
  cleared on their own date, with cheque amount = their total.
- **Sign-in required** before any page opens; users stored in the database with roles
  (Admin / Editor / Viewer), account lockout, idle timeout, and a Users admin page.

## Sign-in & users
- First start with no users → a one-time **First-time setup** screen creates the admin.
  Alternatively set `ADMIN_USERNAME` / `ADMIN_PASSWORD` so the admin is created automatically.
- **Admin**: everything + Users page (add, change role, disable, reset password, unlock).
  **Editor**: add/edit/delete challans, bills, opening balances. **Viewer**: dashboard,
  ledger and Excel export only.
- Passwords: salted PBKDF2-SHA256 (600k iterations). 5 wrong attempts lock the account
  for 15 minutes. Idle sessions expire after `SESSION_TIMEOUT_MINUTES`.
- Refreshing the browser tab signs you out (Streamlit keeps state per tab).

## Run locally (SQLite)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
streamlit run app.py          # data stored in data/pla.db
pytest -q                     # service + Excel export tests
```

## Run on a server (PostgreSQL)
```bash
cp .env.example .env          # set POSTGRES_PASSWORD / ADMIN_USERNAME / ADMIN_PASSWORD / OFFICE_NAME
docker compose up -d --build  # app on http://<server>:8501
```
Or point an existing PostgreSQL at the app with
`DATABASE_URL=postgresql+psycopg://user:pass@host:5432/pla streamlit run app.py`.
Tables and fund heads are created automatically on first start.

For internet-facing use, put it behind a reverse proxy (nginx/Caddy) with HTTPS so
passwords are never sent in plain text.

## Design
```
app.py               entry point, navigation, DB bootstrap
pla/models.py        FundHead, Entry, EntryLine, OpeningBalance, User
pla/auth.py          password hashing, login + lockout, user management
pla/service.py       business rules: balances, overdraft & duplicate checks, summaries
pla/excel_export.py  export to the PLA sheet layout
ui/common.py         shared widgets (entry grid, FY selector, ₹ formatting)
ui/auth.py           sign-in gate, first-run setup, idle timeout, sign-out
app_pages/           Streamlit pages
```
- Amounts are stored normalised (one row per entry × head, `NUMERIC(14,2)`), so adding a
  fund head is a data change, not a schema change.
- Balance rule per head and FY: `closing = opening + receipts − expenses ≥ 0`. Saving,
  editing or deleting is rejected if it would break this; on PostgreSQL the affected
  fund-head rows are locked (`SELECT … FOR UPDATE`) so two users can't overdraw together.
- Schema is created with `create_all`; adopt Alembic if you plan to evolve it.
