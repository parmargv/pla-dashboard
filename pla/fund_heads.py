"""Fund heads exactly as they appear as columns in the PLA_ACCOUNT sheet.

They are seeded into the `fund_heads` table on first start. New heads can be
appended here (or inserted directly into the table) without a schema change,
because amounts are stored as one row per (entry, fund head).
"""

DEFAULT_FUND_HEADS: list[str] = [
    "GF",
    "DF",
    "SF",
    "CMD",
    "Q.DEPOSIT",
    "KCG",
    "DEPOSIT HOUSE KEEPING",
    "RTO REM.",
    "GKS",
    "GTU EXAM REM",
    "GTU EXAM FEE",
    "TEB REM",
    "NSS",
    "SCHOLRSHIP",
    "CSR FUND(PGVCL)",
    "AICTE AMRUT SAROVAR YOJANA",
    "NCC/OTHER",
]
