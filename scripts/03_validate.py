"""
HW4 Part D - Referential integrity and validation (raw vs clean).
MIS3060 Business Intelligence with AI

Runs the SAME checks on the raw extract (Wildcat Capital/) and on the clean
tables (data/clean/) so every number has a before/after:

  1. Referential integrity - one reusable function, check_referential_integrity(),
     applied to all 13 relationships (10 ID-based + 3 date joins).
  2. Rule-based checks for every column with a natural business bound
     (valid ranges, allowed code lists, sign rules, row-level consistency).
  3. Reconciliations - values recomputed from other columns, and facts that two
     tables (or two rows) both describe.
  4. IQR outlier flags on numeric columns with no natural bound.
  5. Scorecard - one row per table per stage (raw / clean) with
     % completeness, % key uniqueness, % type validity, % rule pass, % referential match.

What "flagged" means: an IQR flag only says a value is far from the middle 50% of
its column, not that it is wrong - a $40M UHNW account is an outlier and perfectly
valid - so flags are leads to check against business rules and reconciliations,
never a reason to change or delete a value on their own.

Outputs (reports/):
  data_quality_scorecard.csv   one row per table per stage (the required deliverable)
  ri_results.csv               13 relationships x 2 stages
  rule_results.csv             every rule x 2 stages (evaluated, failed, pass %)
  reconciliations.csv          every reconciliation x 2 stages
  iqr_outliers.csv             IQR fences and flag counts x 2 stages

Scorecard definitions (per table):
  % completeness      = filled required cells / required cells. Blanks that are correct
                        by design (security/shares/price on cash transactions, sector on
                        ETFs, expense ratio on non-ETFs) count as complete, not missing.
  % key uniqueness    = rows whose primary key AND business key are both unique / rows.
  % type validity     = non-blank cells that parse as the column's expected type
                        (whole number, decimal, ISO date, True/False) / non-blank typed cells.
  % rule pass         = passed rule evaluations / all rule evaluations for the table.
  % referential match = matched non-blank foreign-key values / non-blank FK values over every
                        relationship where the table is the "many" side (blank if none).

Usage (from the hw04 folder):
    python scripts/03_validate.py

Reuse in Assignment 5:
    from importlib import import_module
    check_referential_integrity = import_module("03_validate").check_referential_integrity
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

HW_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = HW_DIR / "Wildcat Capital"
CLEAN_DIR = HW_DIR / "data" / "clean"
REPORT_DIR = HW_DIR / "reports"

TABLES = ["dim_advisor", "dim_benchmark", "dim_client", "dim_date", "dim_security",
          "fact_fees", "fact_portfolio_snapshot", "fact_transactions"]


# ==========================================================================
# 1. REUSABLE REFERENTIAL-INTEGRITY FUNCTION  (keep for Assignment 5)
# ==========================================================================
def check_referential_integrity(fact_df: pd.DataFrame, fact_key: str,
                                dim_df: pd.DataFrame, dim_key: str) -> dict:
    """Check that every value of fact_df[fact_key] exists in dim_df[dim_key].

    Keys are compared exactly as text, the way a join on text would compare them
    ('45.0' does not match '45'). Blank / null foreign keys are NOT orphans: they are
    counted in null_key_rate and excluded from the match rate.

    Returns
        match_rate          matched non-null rows / non-null rows (1.0 if there are none)
        orphan_rows         number of non-null rows with no matching dimension key
        distinct_orphan_keys number of distinct orphan key values
        null_key_rate       null or blank rows / all rows
        orphan_df           the orphan rows themselves (a copy of fact_df rows), for tracing
        plus: rows, null_key_rows, checked_rows, matched_rows, orphan_key_examples
    """
    fk = fact_df[fact_key]
    fk_text = fk.astype("string").str.strip()
    is_null = fk.isna() | fk_text.isna() | (fk_text == "")
    present = fk_text[~is_null]
    dim_keys = set(dim_df[dim_key].dropna().astype("string").str.strip())
    matched = present.isin(dim_keys)
    orphan_values = present[~matched]
    n = len(fk)
    return {
        "rows": n,
        "null_key_rows": int(is_null.sum()),
        "null_key_rate": float(is_null.mean()) if n else 0.0,
        "checked_rows": int(len(present)),
        "matched_rows": int(matched.sum()),
        "match_rate": float(matched.mean()) if len(present) else 1.0,
        "orphan_rows": int((~matched).sum()),
        "distinct_orphan_keys": int(orphan_values.nunique()),
        "orphan_key_examples": " | ".join(sorted(orphan_values.unique(), key=lambda x: (len(x), x))[:5]),
        "orphan_df": fact_df.loc[orphan_values.index].copy(),
    }


# 13 relationships: 10 ID-based + 3 date joins (child, fk, parent, pk)
RELATIONSHIPS = [
    ("dim_client", "advisor_id", "dim_advisor", "advisor_id"),
    ("dim_security", "benchmark_id", "dim_benchmark", "benchmark_id"),
    ("fact_fees", "client_id", "dim_client", "client_id"),
    ("fact_fees", "advisor_id", "dim_advisor", "advisor_id"),
    ("fact_portfolio_snapshot", "client_id", "dim_client", "client_id"),
    ("fact_portfolio_snapshot", "advisor_id", "dim_advisor", "advisor_id"),
    ("fact_portfolio_snapshot", "benchmark_id", "dim_benchmark", "benchmark_id"),
    ("fact_transactions", "client_id", "dim_client", "client_id"),
    ("fact_transactions", "advisor_id", "dim_advisor", "advisor_id"),
    ("fact_transactions", "security_id", "dim_security", "security_id"),
    ("fact_fees", "fee_date", "dim_date", "date"),
    ("fact_portfolio_snapshot", "snapshot_date", "dim_date", "date"),
    ("fact_transactions", "txn_date", "dim_date", "date"),
]

# ==========================================================================
# Table metadata: keys and expected types
# ==========================================================================
PRIMARY_KEYS = {
    "dim_advisor": "advisor_id", "dim_benchmark": "benchmark_id", "dim_client": "client_id",
    "dim_date": "date_id", "dim_security": "security_id", "fact_fees": "fee_id",
    "fact_portfolio_snapshot": "snapshot_id", "fact_transactions": "txn_id",
}
# Business keys: what should be unique in the real world, independent of the surrogate ID.
BUSINESS_KEYS = {
    "dim_advisor": ["first_name", "last_name", "region"],
    "dim_benchmark": ["name"],
    # a client = same person, address, advisor, start date, money (last_name excluded: the
    # duplicates differ only by one letter in last_name)
    "dim_client": ["first_name", "age", "city", "state", "zip_code", "client_since", "advisor_id", "initial_aum"],
    "dim_date": ["date"],
    "dim_security": ["ticker"],
    "fact_fees": ["client_id", "fee_date", "fee_type"],
    "fact_portfolio_snapshot": ["client_id", "snapshot_date"],
    # a transaction = same event regardless of which client_id it was booked under
    "fact_transactions": ["advisor_id", "security_id", "txn_date", "txn_type", "shares", "price", "amount"],
}
TYPES = {
    "dim_advisor": {"advisor_id": "int", "years_experience": "int", "aum_target_mm": "decimal"},
    "dim_benchmark": {"benchmark_id": "int"},
    "dim_client": {"client_id": "int", "age": "int", "initial_aum": "decimal", "client_since": "date",
                   "advisor_id": "int", "is_active": "bool"},
    "dim_date": {"date_id": "int", "date": "date", "year": "int", "quarter": "int", "month": "int",
                 "week_of_year": "int", "is_month_end": "int", "is_quarter_end": "int",
                 "is_year_end": "int", "is_trading_day": "int"},
    "dim_security": {"security_id": "int", "benchmark_id": "int", "is_etf": "bool", "expense_ratio_bps": "decimal"},
    "fact_fees": {"fee_id": "int", "client_id": "int", "advisor_id": "int", "fee_date": "date",
                  "aum_basis": "decimal", "bps_charged": "decimal", "amount": "decimal"},
    "fact_portfolio_snapshot": {"snapshot_id": "int", "client_id": "int", "advisor_id": "int",
                                "benchmark_id": "int", "snapshot_date": "date", "year": "int", "month": "int",
                                "aum": "decimal", "return_ytd": "decimal", "benchmark_return_ytd": "decimal",
                                "active_alpha_ytd": "decimal", "cash_pct": "decimal"},
    "fact_transactions": {"txn_id": "int", "client_id": "int", "advisor_id": "int", "security_id": "int",
                          "txn_date": "date", "shares": "decimal", "price": "decimal", "amount": "decimal"},
}
TYPE_PATTERNS = {
    "int": re.compile(r"^[+-]?\d+$"),
    "decimal": re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$"),  # 9.9e-05 is a valid decimal
    "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "bool": re.compile(r"^(True|False)$"),
}

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
    "VA", "WA", "WV", "WI", "WY", "DC",
}
ACCOUNT_TYPES = {"Taxable", "IRA", "Roth IRA", "Trust", "529"}
SECURITY_TXNS = {"Buy", "Sell", "Dividend"}
CASH_TXNS = {"Deposit", "Withdrawal", "Advisory Fee"}


# ==========================================================================
# Helpers
# ==========================================================================
def load(stage: str) -> dict[str, pd.DataFrame]:
    """Read the 8 tables of one stage as text (blanks stay "")."""
    out = {}
    for t in TABLES:
        if stage == "raw":
            path = RAW_DIR / f"{t}.csv"
        else:
            path = CLEAN_DIR / f"{t}_clean.csv"
            if not path.exists():                  # also accept "same file name" output
                path = CLEAN_DIR / f"{t}.csv"
        out[t] = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    return out


def num(s: pd.Series) -> pd.Series:
    """Lenient numeric parse used by rules and reconciliations ('9.28%' -> 0.0928).
    Format problems are measured separately by type validity."""
    t = s.astype(str).str.strip()
    pct = t.str.endswith("%")
    v = pd.to_numeric(t.str.replace(r"[%$,]", "", regex=True), errors="coerce")
    return v.where(~pct, v / 100)


def dt(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.str.strip(), format="%Y-%m-%d", errors="coerce")


def structural_blank_mask(t: str, df: pd.DataFrame) -> pd.DataFrame:
    """True where a blank cell is correct by design (not missing data)."""
    m = pd.DataFrame(False, index=df.index, columns=df.columns)
    if t == "fact_transactions":
        cash = df["txn_type"].isin(CASH_TXNS)
        for c in ("security_id", "shares", "price"):
            m[c] = cash
    if t == "dim_security":
        etf = df["is_etf"] == "True"
        m["sector"] = etf
        m["expense_ratio_bps"] = ~etf
    return m


# ==========================================================================
# 2. RULE-BASED CHECKS (natural business bounds)
# Each rule returns (applies_mask, pass_mask). Evaluated only where applies_mask is True.
# ==========================================================================
def rng(col, lo=None, hi=None):
    def f(df):
        v = num(df[col])
        ok = v.notna()
        if lo is not None:
            ok &= v >= lo
        if hi is not None:
            ok &= v <= hi
        return df[col] != "", ok
    return f


def date_rng(col, lo, hi):
    def f(df):
        d = dt(df[col])
        return df[col] != "", d.between(pd.Timestamp(lo), pd.Timestamp(hi))
    return f


def allowed(col, values, blank_ok=False):
    def f(df):
        applies = pd.Series(True, index=df.index) if not blank_ok else df[col] != ""
        return applies, df[col].isin(values)
    return f


def regex(col, pattern):
    def f(df):
        return df[col] != "", df[col].str.match(pattern)
    return f


def flags01(df):
    cols = [c for c in df.columns if c.startswith("flag_")]
    if not cols:
        return pd.Series(False, index=df.index), pd.Series(True, index=df.index)
    ok = pd.Series(True, index=df.index)
    for c in cols:
        ok &= df[c].isin({"0", "1"})
    return pd.Series(True, index=df.index), ok


RULES: dict[str, list[tuple[str, str, str, callable]]] = {
    # (rule_id, column, description, function)
    "dim_advisor": [
        ("ADV1", "years_experience", "0-60 years", rng("years_experience", 0, 60)),
        ("ADV2", "aum_target_mm", "> 0 ($ millions)", rng("aum_target_mm", 0.01)),
        ("ADV3", "region", "in region list", allowed("region", {"Northeast", "Mid-Atlantic", "Southeast",
                                                                "Midwest", "West", "Southwest"})),
        ("ADV4", "certifications", "only CFA/CFP/CIMA or 'None'",
         lambda df: (df["certifications"] != "",
                     df["certifications"].str.split(r",\s*").map(lambda xs: set(xs) <= {"CFA", "CFP", "CIMA", "None"}))),
    ],
    "dim_benchmark": [
        ("BEN1", "asset_class", "in asset-class list", allowed("asset_class", {
            "Equity", "Fixed Income", "Multi-Asset", "International", "Small Cap", "Emerging", "Cash"})),
        ("BEN2", "name", "not blank", lambda df: (pd.Series(True, index=df.index), df["name"] != "")),
    ],
    "dim_client": [
        ("CLI1", "age", "18-110 (adult client)", rng("age", 18, 110)),
        ("CLI2", "state", "USPS two-letter code", allowed("state", US_STATES)),
        ("CLI3", "zip_code", "exactly 5 digits", regex("zip_code", r"^\d{5}$")),
        ("CLI4", "risk_profile", "in risk-profile list (blank fails)", allowed("risk_profile", {
            "Conservative", "Moderately Conservative", "Balanced", "Moderately Aggressive", "Aggressive"})),
        ("CLI5", "client_tier", "Retail / HNW / UHNW", allowed("client_tier", {"Retail", "HNW", "UHNW"})),
        ("CLI6", "primary_account_type", "in account-type list", allowed("primary_account_type", ACCOUNT_TYPES)),
        ("CLI7", "initial_aum", "> 0", rng("initial_aum", 0.01)),
        ("CLI8", "client_since", "1950-01-01 to 2024-12-31", date_rng("client_since", "1950-01-01", "2024-12-31")),
        ("CLI9", "is_active", "True/False", allowed("is_active", {"True", "False"})),
        ("CLI10", "client_tier+initial_aum", "tier matches AUM band (Retail<500k, HNW<5M, UHNW>=5M)",
         lambda df: (pd.Series(True, index=df.index),
                     ((df["client_tier"] == "Retail") & (num(df["initial_aum"]) < 5e5))
                     | ((df["client_tier"] == "HNW") & num(df["initial_aum"]).between(5e5, 5e6, inclusive="left"))
                     | ((df["client_tier"] == "UHNW") & (num(df["initial_aum"]) >= 5e6)))),
        ("CLI11", "flag_*", "flag columns are 0/1", flags01),
    ],
    "dim_date": [
        ("DAT1", "year", "2020-2024", rng("year", 2020, 2024)),
        ("DAT2", "quarter", "1-4", rng("quarter", 1, 4)),
        ("DAT3", "month", "1-12", rng("month", 1, 12)),
        ("DAT4", "week_of_year", "1-53", rng("week_of_year", 1, 53)),
        ("DAT5", "is_*", "flag columns are 0/1",
         lambda df: (pd.Series(True, index=df.index),
                     df[["is_month_end", "is_quarter_end", "is_year_end", "is_trading_day"]].isin({"0", "1"}).all(axis=1))),
        ("DAT6", "year/quarter/month/month_name/day_of_week", "agree with the date",
         lambda df: (pd.Series(True, index=df.index),
                     (dt(df["date"]).dt.year.astype("Int64").astype(str) == df["year"])
                     & (dt(df["date"]).dt.quarter.astype("Int64").astype(str) == df["quarter"])
                     & (dt(df["date"]).dt.month.astype("Int64").astype(str) == df["month"])
                     & (dt(df["date"]).dt.month_name() == df["month_name"])
                     & (dt(df["date"]).dt.day_name() == df["day_of_week"]))),
        ("DAT7", "is_trading_day", "weekends are not trading days",
         lambda df: (dt(df["date"]).dt.dayofweek >= 5, df["is_trading_day"] == "0")),
    ],
    "dim_security": [
        ("SEC1", "asset_class", "in asset-class list", allowed("asset_class", {
            "Equity", "Fixed Income", "Alternative", "Cash Equivalent"})),
        ("SEC2", "is_etf", "True/False", allowed("is_etf", {"True", "False"})),
        ("SEC3", "expense_ratio_bps", "ETF: 0-300 bps", lambda df: (df["is_etf"] == "True",
                                                                   num(df["expense_ratio_bps"]).between(0, 300))),
        ("SEC4", "expense_ratio_bps", "non-ETF: blank", lambda df: (df["is_etf"] == "False",
                                                                   df["expense_ratio_bps"] == "")),
        ("SEC5", "sector", "non-ETF: in sector list", lambda df: (df["is_etf"] == "False", df["sector"].isin({
            "Communication Services", "Consumer Discretionary", "Consumer Staples", "Energy", "Financials",
            "Healthcare", "Industrials", "Materials", "Real Estate", "Technology", "Utilities"}))),
        ("SEC6", "ticker", "2-5 capital letters", regex("ticker", r"^[A-Z]{2,5}$")),
    ],
    "fact_fees": [
        ("FEE1", "fee_type", "in fee-type list", allowed("fee_type", {"Advisory", "Management", "Custodial", "Performance"})),
        ("FEE2", "aum_basis", "> 0", rng("aum_basis", 0.01)),
        ("FEE3", "bps_charged", "0-100 bps (<= 1% a year)", rng("bps_charged", 0, 100)),
        ("FEE4", "amount", "> 0 (sign rule: a charge is positive)", rng("amount", 0.01)),
        ("FEE5", "fee_date", "inside 2020-2024", date_rng("fee_date", "2020-01-01", "2024-12-31")),
        ("FEE6", "fee_date", "monthly fee dated on the 1st", lambda df: (df["fee_date"] != "", dt(df["fee_date"]).dt.day == 1)),
        ("FEE7", "flag_*", "flag columns are 0/1", flags01),
    ],
    "fact_portfolio_snapshot": [
        ("SNP1", "aum", "> 0", rng("aum", 0.01)),
        ("SNP2", "return_ytd", ">= -100% (cannot lose more than everything)", rng("return_ytd", -1)),
        ("SNP3", "return_ytd", "<= +100% YTD (plausibility)", rng("return_ytd", None, 1)),
        ("SNP4", "benchmark_return_ytd", "-100% to +100%", rng("benchmark_return_ytd", -1, 1)),
        ("SNP5", "cash_pct", "0-1 (a share)", rng("cash_pct", 0, 1)),
        ("SNP6", "month", "1-12", rng("month", 1, 12)),
        ("SNP7", "year", "2020-2024", rng("year", 2020, 2024)),
        ("SNP8", "account_type", "in account-type list", allowed("account_type", ACCOUNT_TYPES)),
        ("SNP9", "year+month", "agree with snapshot_date",
         lambda df: (pd.Series(True, index=df.index),
                     (dt(df["snapshot_date"]).dt.year.astype("Int64").astype(str) == df["year"])
                     & (dt(df["snapshot_date"]).dt.month == num(df["month"])))),
        ("SNP10", "snapshot_date", "dated on the 1st", lambda df: (df["snapshot_date"] != "",
                                                                 dt(df["snapshot_date"]).dt.day == 1)),
        ("SNP11", "flag_*", "flag columns are 0/1", flags01),
    ],
    "fact_transactions": [
        ("TXN1", "txn_type", "in transaction-type list", allowed("txn_type", SECURITY_TXNS | CASH_TXNS)),
        ("TXN2", "amount", "> 0 (sign rule: direction comes from txn_type)", rng("amount", 0.01)),
        ("TXN3", "shares", "security txn: > 0 (sign rule)", lambda df: (df["txn_type"].isin(SECURITY_TXNS),
                                                                       num(df["shares"]) > 0)),
        ("TXN4", "price", "security txn: > 0", lambda df: (df["txn_type"].isin(SECURITY_TXNS), num(df["price"]) > 0)),
        ("TXN5", "security_id/shares/price", "cash txn: all blank",
         lambda df: (df["txn_type"].isin(CASH_TXNS),
                     (df["security_id"] == "") & (df["shares"] == "") & (df["price"] == ""))),
        ("TXN6", "security_id", "security txn: present", lambda df: (df["txn_type"].isin(SECURITY_TXNS),
                                                                    df["security_id"] != "")),
        ("TXN7", "txn_date", "inside 2020-2024", date_rng("txn_date", "2020-01-01", "2024-12-31")),
    ],
}


def run_rules(stage: str, T: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for t, rules in RULES.items():
        for rid, col, desc, fn in rules:
            applies, ok = fn(T[t])
            applies = applies.fillna(False).astype(bool)
            ok = ok.fillna(False).astype(bool)
            evaluated = int(applies.sum())
            failed = int((applies & ~ok).sum())
            rows.append({"stage": stage, "table": t, "rule_id": rid, "column": col, "rule": desc,
                         "evaluated": evaluated, "failed": failed,
                         "pass_pct": round(100 * (1 - failed / evaluated), 4) if evaluated else 100.0})
    return pd.DataFrame(rows)


# ==========================================================================
# 3. RECONCILIATIONS
# ==========================================================================
def run_reconciliations(stage: str, T: dict[str, pd.DataFrame]) -> pd.DataFrame:
    out = []

    def add(rid, kind, desc, compared, agree, note=""):
        out.append({"stage": stage, "recon_id": rid, "type": kind, "reconciliation": desc,
                    "rows_compared": int(compared), "rows_agree": int(agree),
                    "agree_pct": round(100 * agree / compared, 4) if compared else 100.0, "note": note})

    c, f, s, x = T["dim_client"], T["fact_fees"], T["fact_portfolio_snapshot"], T["fact_transactions"]

    # R1 two tables, one fact: fee aum_basis = snapshot aum for the same client-month
    m = f.merge(s[["client_id", "snapshot_date", "aum"]], left_on=["client_id", "fee_date"],
                right_on=["client_id", "snapshot_date"], how="inner")
    ok = (num(m["aum_basis"]) - num(m["aum"])).abs() <= 0.01
    add("R1", "cross-table", "fact_fees.aum_basis = fact_portfolio_snapshot.aum (same client, month)",
        len(m), ok.sum(), f"{len(f) - len(m)} fees with no snapshot")

    # R2 fact advisor = advisor on the client dimension (3 facts)
    adv = c.set_index("client_id")["advisor_id"]
    for t in ["fact_fees", "fact_portfolio_snapshot", "fact_transactions"]:
        mapped = T[t]["client_id"].map(adv)
        has = mapped.notna()
        add("R2", "cross-table", f"{t}.advisor_id = dim_client.advisor_id", has.sum(),
            (mapped[has] == T[t].loc[has, "advisor_id"]).sum(), f"{int((~has).sum())} rows with no client")

    # R3 snapshot account type = client's primary account type
    acct = c.set_index("client_id")["primary_account_type"]
    mapped = s["client_id"].map(acct)
    has = mapped.notna()
    add("R3", "cross-table", "fact_portfolio_snapshot.account_type = dim_client.primary_account_type",
        has.sum(), (mapped[has] == s.loc[has, "account_type"]).sum())

    # R4 recomputed: fee amount = aum_basis x bps / 10,000 / 12
    ok = (num(f["aum_basis"]) * num(f["bps_charged"]) / 10_000 / 12 - num(f["amount"])).abs() <= 0.05
    add("R4", "recomputed", "fact_fees.amount = aum_basis x bps_charged / 10,000 / 12", len(f), ok.sum())

    # R5 recomputed: amount = shares x price on security transactions
    sec = x["txn_type"].isin(SECURITY_TXNS)
    ok = (num(x["shares"]) * num(x["price"]) - num(x["amount"])).abs() <= 0.05
    add("R5", "recomputed", "fact_transactions.amount = shares x price (Buy/Sell/Dividend)", sec.sum(), (ok & sec).sum())

    # R6 recomputed: alpha = return - benchmark return (strict: return must be a plain number)
    plain = s["return_ytd"].str.match(TYPE_PATTERNS["decimal"])
    ok = plain & ((pd.to_numeric(s["return_ytd"].where(plain), errors="coerce")
                   - num(s["benchmark_return_ytd"]) - num(s["active_alpha_ytd"])).abs() <= 6e-5)
    add("R6", "recomputed", "active_alpha_ytd = return_ytd - benchmark_return_ytd (return as a plain number)",
        len(s), ok.sum(), "percent strings fail until converted")

    # R7 cross-row: return_ytd = aum / prior-December aum - 1
    q = s[["client_id", "year", "month"]].assign(aum=num(s["aum"]), ret=num(s["return_ytd"]))
    dec = q[q["month"] == "12"].assign(year=lambda d: (d["year"].astype(int) + 1).astype(str))
    dec = dec.set_index(["client_id", "year"])["aum"]
    base = pd.MultiIndex.from_frame(q[["client_id", "year"]]).map(lambda k: dec.get(k, np.nan))
    has = pd.Series(base, index=q.index).notna()
    ok = ((q["aum"] / pd.Series(base, index=q.index) - 1 - q["ret"]).abs() <= 0.001) & has
    add("R7", "cross-row", "return_ytd = aum / prior-December aum - 1", has.sum(), ok.sum(),
        "shows return_ytd is a balance change including deposits, not investment performance")

    # R8 cross-row: one benchmark return per benchmark per date
    g = s.groupby(["benchmark_id", "snapshot_date"])["benchmark_return_ytd"].transform("nunique")
    add("R8", "cross-row", "benchmark_return_ytd identical for all clients on the same benchmark and date",
        len(s), (g == 1).sum())

    # R9 cross-table: month-over-month AUM change vs net cash flow from transactions
    q = s[["client_id", "snapshot_date"]].assign(aum=num(s["aum"])).sort_values(["client_id", "snapshot_date"])
    q["prev_aum"] = q.groupby("client_id")["aum"].shift()
    q["prev_month"] = q.groupby("client_id")["snapshot_date"].shift().str[:7]
    sign = {"Deposit": 1, "Withdrawal": -1, "Advisory Fee": -1}
    cf = x[x["txn_type"].isin(sign)].assign(
        v=lambda d: num(d["amount"]) * d["txn_type"].map(sign), m=lambda d: d["txn_date"].str[:7]
    ).groupby(["client_id", "m"])["v"].sum()
    q = q.dropna(subset=["prev_aum"])
    q["cash_flow"] = pd.MultiIndex.from_frame(q[["client_id", "prev_month"]]).map(lambda k: cf.get(k, 0.0))
    big = (q["aum"] - q["prev_aum"]).abs() > 0.5 * q["prev_aum"]
    explained = big & (q["cash_flow"].abs() >= 0.5 * (q["aum"] - q["prev_aum"]).abs()) \
        & (np.sign(q["cash_flow"]) == np.sign(q["aum"] - q["prev_aum"]))
    corr = (q["aum"] - q["prev_aum"]).corr(q["cash_flow"])
    add("R9", "cross-table", "AUM jumps > 50% explained by that month's net deposits/withdrawals",
        big.sum(), explained.sum(),
        f"correlation of monthly AUM change with net cash flow = {corr:.3f}; transactions do not drive AUM "
        "in this extract (documented limitation)")

    return pd.DataFrame(out)


# ==========================================================================
# 4. IQR OUTLIERS (columns with no natural upper bound)
# ==========================================================================
IQR_COLUMNS = [
    ("dim_client", "initial_aum"),
    ("fact_portfolio_snapshot", "aum"),
    ("fact_portfolio_snapshot", "active_alpha_ytd"),
    ("fact_transactions", "amount"),
    ("fact_fees", "amount"),
]
IQR_SENTENCE = ("Flagged does not mean wrong: an IQR flag only says a value is far from the middle 50% of its "
                "column (a $40M UHNW account is an outlier and perfectly valid), so flags are leads to check "
                "against business rules and reconciliations, never a reason to change or delete data on their own.")


def run_iqr(stage: str, T: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for t, col in IQR_COLUMNS:
        v = num(T[t][col]).dropna()
        q1, q3 = v.quantile([0.25, 0.75])
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        flagged = (v < lo) | (v > hi)
        note = ""
        df = T[t]
        for fcol in ("flag_aum_placeholder", "flag_return_implausible"):
            if fcol in df.columns:
                both = (df.loc[flagged[flagged].index, fcol] == "1").sum()
                note += f"{int(both)} of the IQR flags also have {fcol}=1; "
        rows.append({"stage": stage, "table": t, "column": col, "q1": round(q1, 6), "q3": round(q3, 6),
                     "lower_fence": round(lo, 6), "upper_fence": round(hi, 6), "values": int(len(v)),
                     "flagged": int(flagged.sum()), "flagged_pct": round(100 * flagged.mean(), 3),
                     "min": v.min(), "max": v.max(), "note": note.strip()})
    return pd.DataFrame(rows)


# ==========================================================================
# 5. SCORECARD
# ==========================================================================
def scorecard(stage: str, T: dict[str, pd.DataFrame], ri: pd.DataFrame, rules: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for t in TABLES:
        df = T[t]
        data_cols = [c for c in df.columns if not c.startswith("flag_")]
        d = df[data_cols]

        # completeness: blanks that are correct by design count as complete
        blank = d == ""
        structural = structural_blank_mask(t, d)
        required_cells = d.size - int((blank & structural).sum().sum())
        missing = int((blank & ~structural).sum().sum())
        completeness = 100 * (1 - missing / required_cells)

        # key uniqueness: PK unique AND business key unique
        pk_dup = df[PRIMARY_KEYS[t]].duplicated(keep=False)
        bk_dup = df.duplicated(subset=BUSINESS_KEYS[t], keep=False)
        uniqueness = 100 * (1 - (pk_dup | bk_dup).mean())

        # type validity: non-blank typed cells that parse as the expected type
        checked = valid = 0
        for col, typ in TYPES[t].items():
            vals = d[col][d[col] != ""]
            ok = vals.str.match(TYPE_PATTERNS[typ])
            if typ == "date":
                ok &= dt(vals).notna()
            checked += len(vals)
            valid += int(ok.sum())
        type_validity = 100 * valid / checked if checked else 100.0

        r = rules[rules["table"] == t]
        rule_pass = 100 * (1 - r["failed"].sum() / r["evaluated"].sum()) if r["evaluated"].sum() else 100.0

        rr = ri[ri["child_table"] == t]
        ref = 100 * rr["matched_rows"].sum() / rr["checked_rows"].sum() if rr["checked_rows"].sum() else np.nan

        rows.append({
            "table": t, "stage": stage, "rows": len(df),
            "pct_completeness": round(completeness, 4),
            "pct_key_uniqueness": round(uniqueness, 4),
            "pct_type_validity": round(type_validity, 4),
            "pct_rule_pass": round(rule_pass, 4),
            "pct_referential_match": round(ref, 4) if not np.isnan(ref) else "",
            "missing_required_cells": missing,
            "duplicate_key_rows": int((pk_dup | bk_dup).sum()),
            "type_invalid_cells": checked - valid,
            "rule_failures": int(r["failed"].sum()),
            "orphan_fk_rows": int(rr["orphan_rows"].sum()) if len(rr) else "",
            "fk_relationships": len(rr),
        })
    return pd.DataFrame(rows)


# ==========================================================================
# Main
# ==========================================================================
def validate(stage: str) -> dict[str, pd.DataFrame]:
    T = load(stage)
    ri_rows = []
    for child, fk, parent, pk in RELATIONSHIPS:
        r = check_referential_integrity(T[child], fk, T[parent], pk)
        orphans = r.pop("orphan_df")             # keep the summary table flat
        assert len(orphans) == r["orphan_rows"], f"{child}.{fk}: orphan_df does not match orphan_rows"
        ri_rows.append({"stage": stage, "relationship": f"{child}.{fk} -> {parent}.{pk}",
                        "child_table": child, "kind": "date" if parent == "dim_date" else "id", **r})
    ri = pd.DataFrame(ri_rows)
    rules = run_rules(stage, T)
    recon = run_reconciliations(stage, T)
    iqr = run_iqr(stage, T)
    score = scorecard(stage, T, ri, rules)
    return {"ri": ri, "rules": rules, "recon": recon, "iqr": iqr, "score": score}


def side_by_side(raw: pd.DataFrame, clean: pd.DataFrame, keys: list[str], value: str) -> pd.DataFrame:
    a = raw.set_index(keys)[value].rename("raw")
    b = clean.set_index(keys)[value].rename("clean")
    return pd.concat([a, b], axis=1)


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_colwidth", 80)
    pd.set_option("display.max_rows", 200)

    print("Validating RAW tables ...")
    raw = validate("raw")
    print("Validating CLEAN tables ...")
    clean = validate("clean")

    out = {k: pd.concat([raw[k], clean[k]], ignore_index=True) for k in raw}
    score = out["score"].sort_values(["table", "stage"], key=lambda s: s.map({"raw": 0, "clean": 1})
                                     if s.name == "stage" else s, kind="stable")
    score.to_csv(REPORT_DIR / "data_quality_scorecard.csv", index=False)
    out["ri"].to_csv(REPORT_DIR / "ri_results.csv", index=False)
    out["rules"].to_csv(REPORT_DIR / "rule_results.csv", index=False)
    out["recon"].to_csv(REPORT_DIR / "reconciliations.csv", index=False)
    iqr = out["iqr"].copy()
    iqr["interpretation"] = IQR_SENTENCE
    iqr.to_csv(REPORT_DIR / "iqr_outliers.csv", index=False)

    # ---- console report (before / after)
    print("\n=== 1. Referential integrity: match rate (non-blank keys) ===")
    ri_view = side_by_side(raw["ri"], clean["ri"], ["relationship"], "match_rate")
    ri_view["orphans_raw"] = raw["ri"].set_index("relationship")["orphan_rows"]
    ri_view["orphans_clean"] = clean["ri"].set_index("relationship")["orphan_rows"]
    ri_view["null_key_rate"] = clean["ri"].set_index("relationship")["null_key_rate"].round(4)
    print(ri_view.to_string())

    print("\n=== 2. Rules that fail in either stage (failed rows) ===")
    rv = side_by_side(raw["rules"], clean["rules"], ["table", "rule_id", "rule"], "failed")
    print(rv[(rv["raw"] > 0) | (rv["clean"] > 0)].to_string())
    print(f"  {len(rv)} rules evaluated per stage; {int((rv['raw'] > 0).sum())} fail on raw, "
          f"{int((rv['clean'] > 0).sum())} fail on clean")

    print("\n=== 3. Reconciliations (% agree) ===")
    rc = side_by_side(raw["recon"], clean["recon"], ["recon_id", "reconciliation"], "agree_pct")
    print(rc.to_string())

    print("\n=== 4. IQR outliers (1.5 x IQR) ===")
    print(side_by_side(raw["iqr"], clean["iqr"], ["table", "column"], "flagged").to_string())
    print(f"  {IQR_SENTENCE}")

    print("\n=== 5. Data-quality scorecard ===")
    cols = ["table", "stage", "rows", "pct_completeness", "pct_key_uniqueness", "pct_type_validity",
            "pct_rule_pass", "pct_referential_match"]
    print(score[cols].to_string(index=False))
    print(f"\nSaved reports/data_quality_scorecard.csv ({len(score)} rows), ri_results.csv, rule_results.csv, "
          f"reconciliations.csv, iqr_outliers.csv")

    # sanity: the cleaning must not make anything worse
    s = score.set_index(["table", "stage"])
    for t in TABLES:
        for m in ["pct_completeness", "pct_key_uniqueness", "pct_type_validity", "pct_rule_pass"]:
            assert s.loc[(t, "clean"), m] >= s.loc[(t, "raw"), m] - 1e-9, f"{t} {m} got worse after cleaning"


if __name__ == "__main__":
    main()
