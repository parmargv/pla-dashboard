"""SQLAlchemy ORM models.

The spreadsheet is "wide" (one column per fund head). The database is
normalised instead: an Entry (a challan or a bill) has one EntryLine per fund
head that carries a non-zero amount. That keeps the schema stable when heads
are added and lets every total be a simple SUM/GROUP BY.
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MONEY = Numeric(14, 2)


class Base(DeclarativeBase):
    pass


class EntryType(str, enum.Enum):
    RECEIPT = "RECEIPT"  # money in, identified by challan no.
    EXPENSE = "EXPENSE"  # money out, identified by cheque no.

    @property
    def ref_label(self) -> str:
        return "Challan No" if self is EntryType.RECEIPT else "Cheque No"

    @property
    def title(self) -> str:
        return "Receipt" if self is EntryType.RECEIPT else "Expenditure"


class FundHead(Base):
    __tablename__ = "fund_heads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Entry(Base):
    __tablename__ = "entries"
    __table_args__ = (
        UniqueConstraint("entry_type", "fiscal_year", "ref_no", name="uq_entry_ref_per_fy"),
        Index("ix_entries_fy_type_date", "fiscal_year", "entry_type", "entry_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_type: Mapped[EntryType] = mapped_column(Enum(EntryType, name="entry_type"))
    fiscal_year: Mapped[str] = mapped_column(String(7))
    entry_date: Mapped[date] = mapped_column(Date)
    ref_no: Mapped[str] = mapped_column(String(60))
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Expenditure only: the cheque's face value (must equal the sum of the fund-head
    # amounts) and the date the bank cleared it. Uncleared cheques reserve balance
    # but are not part of the statement until `cleared_on` is set.
    cheque_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    cleared_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    lines: Mapped[list["EntryLine"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def total(self) -> Decimal:
        return sum((line.amount for line in self.lines), Decimal("0.00"))

    @property
    def is_cleared(self) -> bool:
        return self.entry_type is EntryType.RECEIPT or self.cleared_on is not None


class EntryLine(Base):
    __tablename__ = "entry_lines"
    __table_args__ = (
        UniqueConstraint("entry_id", "fund_head_id", name="uq_line_head_per_entry"),
        CheckConstraint("amount > 0", name="ck_line_amount_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id", ondelete="CASCADE"), index=True)
    fund_head_id: Mapped[int] = mapped_column(ForeignKey("fund_heads.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(MONEY)

    entry: Mapped[Entry] = relationship(back_populates="lines")


class OpeningBalance(Base):
    __tablename__ = "opening_balances"
    __table_args__ = (UniqueConstraint("fiscal_year", "fund_head_id", name="uq_opening_per_fy_head"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fiscal_year: Mapped[str] = mapped_column(String(7), index=True)
    fund_head_id: Mapped[int] = mapped_column(ForeignKey("fund_heads.id"))
    amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))


class Role(str, enum.Enum):
    ADMIN = "ADMIN"    # everything + manage users
    EDITOR = "EDITOR"  # add / edit / delete challans, bills, opening balances
    VIEWER = "VIEWER"  # dashboard, ledger and Excel export only

    @property
    def can_edit(self) -> bool:
        return self in (Role.ADMIN, Role.EDITOR)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)  # stored lower-case
    full_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role, name="user_role"), default=Role.EDITOR)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # naive UTC
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # naive UTC
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
