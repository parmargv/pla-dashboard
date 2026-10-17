"""Indian financial-year helpers (1 April – 31 March)."""
from __future__ import annotations

from datetime import date


def fiscal_year_of(d: date) -> str:
    """date(2026, 10, 13) -> '2026-27'; date(2027, 2, 1) -> '2026-27'."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def fy_start_year(fy: str) -> int:
    return int(fy[:4])


def fy_bounds(fy: str) -> tuple[date, date]:
    start = fy_start_year(fy)
    return date(start, 4, 1), date(start + 1, 3, 31)


def previous_fy(fy: str) -> str:
    return fiscal_year_of(date(fy_start_year(fy) - 1, 4, 1))


def next_fy(fy: str) -> str:
    return fiscal_year_of(date(fy_start_year(fy) + 1, 4, 1))


FY_MONTHS = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]
