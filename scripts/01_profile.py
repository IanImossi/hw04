"""
HW4 Part A - Audit / profiling of the Wildcat Capital extract.
MIS3060 Business Intelligence with AI

What this script does
---------------------
1. GENERIC PROFILE (works on any CSV): every file is read as text
   (dtype=str, keep_default_na=False) and, for every column, it reports:
   blank count, null-like tokens ("None", "NULL", "NA", ...), distinct count,
   dominant value shapes, the expected type and the share of values that fail
   to parse as that type, whitespace problems, mixed formats, numeric
   min/max/mean, and text length min/max.
   -> reports/profile_raw.csv

2. DATA-QUALITY CHECKS in four groups, each finding tagged ERROR / WARN / INFO:
     1 Column level          - type, allowed values, ranges, key uniqueness
     2 Single-table rules    - duplicates, near-duplicates, row contradictions
     3 Referential integrity - every foreign key points to an existing key
     4 Cross-table rules     - facts that disagree with dimensions or each other
   -> reports/dq_summary_raw.csv

   Severity:
     ERROR = wrong, or will break a join or a calculation
     WARN  = probably wrong; investigate
     INFO  = worth knowing (often a legitimate structure, not a defect)

The generic part needs no configuration. The rule checks for the eight
Wildcat Capital tables are driven by the SCHEMA dictionary below; a table
that is not in SCHEMA still gets the profile plus the generic checks
(type parsing, whitespace, mixed formats, duplicates, near-duplicates,
first-column key uniqueness, and inferred foreign keys named <x>_id).

This script only READS the raw files. It never modifies them.

Usage (from the hw04 folder):
    python scripts/01_profile.py                       # all CSVs in "Wildcat Capital/"
    python scripts/01_profile.py path/to/folder        # all CSVs in another folder
    python scripts/01_profile.py a.csv b.csv           # specific files
    python scripts/01_profile.py --out reports         # output folder (default: reports/)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
HW_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = HW_DIR / "Wildcat Capital"
DEFAULT_OUT_DIR = HW_DIR / "reports"

# The eight files this assignment uses. Anything else in the folder is ignored
# when the default folder is profiled (the brief says to ignore other files).
WILDCAT_TABLES = [
    "dim_advisor", "dim_benchmark", "dim_client", "dim_date", "dim_security",
    "fact_fees", "fact_portfolio_snapshot", "fact_transactions",
]

NULL_TOKENS = {"none", "null", "na", "n/a", "nan", "nat", "nil", "-", "--", "?", "missing", "#n/a"}

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
    "VA", "WA", "WV", "WI", "WY", "DC",
}

# --------------------------------------------------------------------------
# SCHEMA - expected types, keys, allowed values, ranges.
#   types:   int | decimal | date | bool | text   (columns not listed are inferred)
#   pk:      primary key column
#   natural_keys: combinations that should be unique in the business sense
#   allowed: {column: set of allowed values}   (blank handled separately)
#   ranges:  {column: (min, max, severity, reason)}  None = unbounded on that side
#   required: columns where a blank is a defect (ERROR). Blanks elsewhere -> INFO
#   fks:     {column: (parent_table, parent_column)}
# --------------------------------------------------------------------------
SCHEMA: dict[str, dict] = {
    "dim_advisor": {
        "pk": "advisor_id",
        "types": {"advisor_id": "int", "years_experience": "int", "aum_target_mm": "decimal",
                  "first_name": "text", "last_name": "text", "region": "text", "certifications": "text"},
        "required": ["advisor_id", "first_name", "last_name", "region"],
        "allowed": {"region": {"Northeast", "Mid-Atlantic", "Southeast", "Midwest", "West", "Southwest"}},
        "ranges": {"years_experience": (0, 60, "WARN", "career length"),
                   "aum_target_mm": (0.01, None, "ERROR", "AUM target must be positive")},
    },
    "dim_benchmark": {
        "pk": "benchmark_id",
        "types": {"benchmark_id": "int", "name": "text", "asset_class": "text", "ticker_proxy": "text"},
        "required": ["benchmark_id", "name"],
        "natural_keys": [["name"], ["ticker_proxy"]],
    },
    "dim_client": {
        "pk": "client_id",
        "types": {"client_id": "int", "age": "int", "zip_code": "text", "initial_aum": "decimal",
                  "client_since": "date", "advisor_id": "int", "is_active": "bool",
                  "first_name": "text", "last_name": "text", "city": "text", "state": "text",
                  "risk_profile": "text", "client_tier": "text", "primary_account_type": "text"},
        "required": ["client_id", "first_name", "last_name", "state", "client_tier",
                     "primary_account_type", "advisor_id", "risk_profile"],
        "allowed": {
            "state": US_STATES,
            "risk_profile": {"Conservative", "Moderately Conservative", "Balanced",
                             "Moderately Aggressive", "Aggressive"},
            "client_tier": {"Retail", "HNW", "UHNW"},
            "primary_account_type": {"Taxable", "IRA", "Roth IRA", "Trust", "529"},
            "is_active": {"True", "False"},
        },
        "ranges": {"age": (18, 110, "ERROR", "clients must be adults"),
                   "initial_aum": (0.01, None, "ERROR", "AUM must be positive"),
                   "client_since": ("1950-01-01", "2024-12-31", "ERROR", "cannot be in the future of the extract")},
        "patterns": {"zip_code": (r"^\d{5}$", "ERROR", "US ZIP must be exactly 5 digits (leading zeros kept)")},
        "fks": {"advisor_id": ("dim_advisor", "advisor_id")},
    },
    "dim_date": {
        "pk": "date_id",
        "types": {"date_id": "int", "date": "date", "year": "int", "quarter": "int", "month": "int",
                  "week_of_year": "int", "is_month_end": "int", "is_quarter_end": "int",
                  "is_year_end": "int", "is_trading_day": "int", "month_name": "text", "day_of_week": "text"},
        "required": ["date_id", "date"],
        "natural_keys": [["date"]],
        "allowed": {
            "month_name": {"January", "February", "March", "April", "May", "June", "July",
                           "August", "September", "October", "November", "December"},
            "day_of_week": {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"},
            "is_month_end": {"0", "1"}, "is_quarter_end": {"0", "1"},
            "is_year_end": {"0", "1"}, "is_trading_day": {"0", "1"},
        },
        "ranges": {"quarter": (1, 4, "ERROR", "calendar"), "month": (1, 12, "ERROR", "calendar"),
                   "week_of_year": (1, 53, "ERROR", "ISO week"),
                   "year": (2020, 2024, "WARN", "extract covers 2020-2024")},
    },
    "dim_security": {
        "pk": "security_id",
        "types": {"security_id": "int", "benchmark_id": "int", "is_etf": "bool",
                  "expense_ratio_bps": "decimal", "ticker": "text", "name": "text",
                  "asset_class": "text", "sector": "text"},
        "required": ["security_id", "ticker", "name", "asset_class", "benchmark_id", "is_etf"],
        "natural_keys": [["ticker"]],
        "allowed": {"asset_class": {"Equity", "Fixed Income", "Alternative", "Cash Equivalent"},
                    "is_etf": {"True", "False"},
                    "sector": {"Communication Services", "Consumer Discretionary", "Consumer Staples",
                               "Energy", "Financials", "Healthcare", "Industrials", "Materials",
                               "Real Estate", "Technology", "Utilities"}},
        "ranges": {"expense_ratio_bps": (0, 300, "WARN", "typical fund expense ratios are 0-300 bps")},
        "fks": {"benchmark_id": ("dim_benchmark", "benchmark_id")},
    },
    "fact_fees": {
        "pk": "fee_id",
        "types": {"fee_id": "int", "client_id": "int", "advisor_id": "int", "fee_date": "date",
                  "fee_type": "text", "aum_basis": "decimal", "bps_charged": "decimal", "amount": "decimal"},
        "required": ["fee_id", "client_id", "advisor_id", "fee_date", "fee_type", "aum_basis",
                     "bps_charged", "amount"],
        "natural_keys": [["client_id", "fee_date", "fee_type"]],
        "allowed": {"fee_type": {"Advisory", "Management", "Custodial", "Performance"}},
        "ranges": {"aum_basis": (0.01, None, "ERROR", "fee basis must be positive"),
                   "bps_charged": (0, 100, "WARN", "annual advisory fees above 100 bps (1%) are unusual"),
                   "amount": (0.01, None, "ERROR", "a fee charge must be positive"),
                   "fee_date": ("2020-01-01", "2024-12-31", "ERROR", "outside extract period")},
        "fks": {"client_id": ("dim_client", "client_id"), "advisor_id": ("dim_advisor", "advisor_id"),
                "fee_date": ("dim_date", "date")},
    },
    "fact_portfolio_snapshot": {
        "pk": "snapshot_id",
        "types": {"snapshot_id": "int", "client_id": "int", "advisor_id": "int", "benchmark_id": "int",
                  "snapshot_date": "date", "year": "int", "month": "int", "aum": "decimal",
                  "return_ytd": "decimal", "benchmark_return_ytd": "decimal",
                  "active_alpha_ytd": "decimal", "cash_pct": "decimal", "account_type": "text"},
        "required": ["snapshot_id", "client_id", "advisor_id", "benchmark_id", "snapshot_date", "aum"],
        "natural_keys": [["client_id", "snapshot_date"]],
        "allowed": {"account_type": {"Taxable", "IRA", "Roth IRA", "Trust", "529"}},
        "ranges": {"aum": (0.01, None, "ERROR", "AUM must be positive"),
                   "return_ytd": (-1, 1, "WARN", "a YTD return below -100% is impossible; above +100% is implausible"),
                   "benchmark_return_ytd": (-1, 1, "WARN", "benchmark YTD return outside +/-100%"),
                   "active_alpha_ytd": (-1, 1, "WARN", "beating/trailing the benchmark by more than 100 points is implausible"),
                   "cash_pct": (0, 1, "ERROR", "a share must be between 0 and 1"),
                   "month": (1, 12, "ERROR", "calendar"),
                   "year": (2020, 2024, "ERROR", "outside extract period"),
                   "snapshot_date": ("2020-01-01", "2024-12-31", "ERROR", "outside extract period")},
        "fks": {"client_id": ("dim_client", "client_id"), "advisor_id": ("dim_advisor", "advisor_id"),
                "benchmark_id": ("dim_benchmark", "benchmark_id"), "snapshot_date": ("dim_date", "date")},
    },
    "fact_transactions": {
        "pk": "txn_id",
        "types": {"txn_id": "int", "client_id": "int", "advisor_id": "int", "security_id": "int",
                  "txn_date": "date", "txn_type": "text", "shares": "decimal", "price": "decimal",
                  "amount": "decimal"},
        "required": ["txn_id", "client_id", "advisor_id", "txn_date", "txn_type", "amount"],
        "allowed": {"txn_type": {"Buy", "Sell", "Dividend", "Deposit", "Withdrawal", "Advisory Fee"}},
        "ranges": {"price": (0.0001, None, "ERROR", "price must be positive"),
                   "shares": (0.0001, None, "ERROR", "sign rule: shares are a positive quantity; direction comes from txn_type"),
                   "amount": (0.01, None, "ERROR", "sign rule: amount is a positive magnitude"),
                   "txn_date": ("2020-01-01", "2024-12-31", "ERROR", "outside extract period")},
        "fks": {"client_id": ("dim_client", "client_id"), "advisor_id": ("dim_advisor", "advisor_id"),
                "security_id": ("dim_security", "security_id"), "txn_date": ("dim_date", "date")},
    },
}

# --------------------------------------------------------------------------
# Value-level helpers
# --------------------------------------------------------------------------
RE_INT = re.compile(r"^[+-]?\d+$")
RE_INT_AS_FLOAT = re.compile(r"^[+-]?\d+\.0+$")
RE_DEC = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
RE_PCT = re.compile(r"^[+-]?\d+(\.\d+)?\s?%$")
RE_CUR = re.compile(r"^[+-]?\$\s?[\d,]+(\.\d+)?$")
RE_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")
RE_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RE_OTHER_DATE = re.compile(r"^(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}/\d{2}/\d{2}|\d{8})$")
BOOL_WORDS = {"true", "false", "yes", "no", "y", "n", "t", "f"}

# For "mixed formats", plain numbers written with or without decimals are one family.
MIX_FAMILY = {"integer": "number", "integer_as_float": "number", "decimal": "number"}


def value_shape(v: str) -> str:
    """Letters -> A, digits -> 9, everything else kept. '12-B x' -> '99-A A'."""
    if v == "":
        return "<blank>"
    return re.sub(r"\d", "9", re.sub(r"[A-Za-z]", "A", v))


def format_family(v: str) -> str:
    """Classify one raw value into a format family (used for mixed-format detection)."""
    s = v.strip()
    if s == "":
        return "blank"
    if s.lower() in NULL_TOKENS:
        return "null_token"
    if RE_INT.match(s):
        return "integer"
    if RE_INT_AS_FLOAT.match(s):
        return "integer_as_float"
    if RE_DEC.match(s):
        return "decimal"
    if RE_PCT.match(s):
        return "percent"
    if RE_CUR.match(s):
        return "currency"
    if RE_THOUSANDS.match(s):
        return "thousands_sep"
    if RE_ISO_DATE.match(s):
        return "iso_date"
    if RE_OTHER_DATE.match(s):
        return "other_date"
    if s.lower() in BOOL_WORDS:
        return "bool_word"
    return "text"


# Which families count as a clean parse for each expected type.
VALID_FAMILIES = {
    "int": {"integer"},
    "decimal": {"integer", "decimal", "integer_as_float"},  # '426254.0' is a valid decimal
    "date": {"iso_date"},
    "bool": {"bool_word", "integer"},  # 0/1 accepted as boolean
    "text": None,  # anything non-blank is valid text
}


def to_number(s: pd.Series, percent_as_fraction: bool = True) -> pd.Series:
    """Lenient numeric conversion used ONLY for rule checks (not for profiling).
    Strips $ , and whitespace; '9.28%' -> 0.0928 when percent_as_fraction."""
    t = s.astype(str).str.strip()
    is_pct = t.str.endswith("%")
    t = t.str.replace(r"[$,%\s]", "", regex=True)
    out = pd.to_numeric(t, errors="coerce")
    if percent_as_fraction:
        out = out.where(~is_pct, out / 100)
    return out


def to_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.str.strip(), format="%Y-%m-%d", errors="coerce")


def infer_type(col: pd.Series) -> str:
    """Guess the expected type of a column that is not in SCHEMA (>=95% rule)."""
    vals = col[col.str.strip() != ""]
    if vals.empty:
        return "text"
    fam = vals.map(format_family)
    share = fam.value_counts(normalize=True)
    if share.get("integer", 0) >= 0.95:
        return "int"
    if share.get("integer", 0) + share.get("decimal", 0) + share.get("integer_as_float", 0) >= 0.95:
        return "decimal"
    if share.get("iso_date", 0) + share.get("other_date", 0) >= 0.95:
        return "date"
    if share.get("bool_word", 0) >= 0.95:
        return "bool"
    return "text"


# --------------------------------------------------------------------------
# Findings collector
# --------------------------------------------------------------------------
GROUP_NAMES = {1: "1 Column level", 2: "2 Single-table", 3: "3 Referential integrity", 4: "4 Cross-table"}


class Findings:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, table, column, group, check, severity, affected, total, detail, examples=None):
        """Record one finding. affected=0 findings are skipped (only problems/notes are logged)."""
        if affected == 0 and severity != "INFO":
            return
        ex = ""
        if examples is not None:
            ex_list = [str(x) for x in list(examples)[:5]]
            ex = " | ".join(ex_list)
        self.rows.append({
            "table": table,
            "column": column,
            "check_group": GROUP_NAMES[group],
            "check": check,
            "severity": severity,
            "rows_affected": int(affected),
            "table_rows": int(total),
            "pct_affected": round(100 * affected / total, 3) if total else 0.0,
            "detail": detail,
            "examples": ex,
        })

    def frame(self) -> pd.DataFrame:
        cols = ["finding_id", "table", "column", "check_group", "check", "severity",
                "rows_affected", "table_rows", "pct_affected", "detail", "examples"]
        if not self.rows:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(self.rows)
        sev_order = {"ERROR": 0, "WARN": 1, "INFO": 2}
        df = df.sort_values(["check_group", "table", "severity"],
                            key=lambda s: s.map(sev_order) if s.name == "severity" else s,
                            kind="stable").reset_index(drop=True)
        df.insert(0, "finding_id", [f"F{i + 1:03d}" for i in range(len(df))])
        return df[cols]


# --------------------------------------------------------------------------
# 1. GENERIC COLUMN PROFILE (works on any table)
# --------------------------------------------------------------------------
def profile_table(name: str, df: pd.DataFrame, types: dict[str, str]) -> list[dict]:
    rows = []
    n = len(df)
    for col in df.columns:
        s = df[col]
        stripped = s.str.strip()
        blank = stripped == ""
        null_tok = stripped.str.lower().isin(NULL_TOKENS)
        nonblank = s[~blank]

        expected = types.get(col) or infer_type(s)
        fam = s.map(format_family)
        fam_nonblank = fam[~blank & ~null_tok]
        valid = VALID_FAMILIES[expected]
        if valid is None:
            fail_mask = pd.Series(False, index=s.index)
        else:
            fail_mask = ~blank & ~null_tok & ~fam.isin(valid)
        # an ISO-looking date that is not a real calendar date also fails
        if expected == "date":
            bad_cal = fam.eq("iso_date") & to_date(s).isna()
            fail_mask = fail_mask | bad_cal
        n_checked = int((~blank & ~null_tok).sum())

        lead_trail = int((s != stripped).sum())
        double_space = int(s.str.contains(r"\s{2,}", regex=True).sum())

        shapes = s.map(value_shape).value_counts()
        top_shapes = "; ".join(f"{k} ({v / n:.1%})" for k, v in shapes.head(3).items())
        dominant_share = shapes.iloc[0] / n if n else 0

        families_present = sorted(set(fam_nonblank.unique()))
        # mixed formats: more than one format family among non-blank values
        # (integer / decimal / '45.0' count as one "number" family here)
        mix_fam = fam_nonblank.map(lambda f: MIX_FAMILY.get(f, f))
        mixed = mix_fam.nunique() > 1
        minority_fmt = int((mix_fam != mix_fam.mode().iloc[0]).sum()) if mixed else 0
        # values that differ only in case / spacing ("Balanced" vs "balanced ")
        norm = nonblank.str.strip().str.lower().str.replace(r"\s+", " ", regex=True)
        case_variants = int(nonblank.nunique() - norm.nunique())

        num = pd.to_numeric(stripped.where(fam.isin({"integer", "decimal", "integer_as_float"})),
                            errors="coerce")
        lengths = nonblank.str.len()

        rows.append({
            "table": name,
            "column": col,
            "rows": n,
            "expected_type": expected,
            "type_source": "schema" if col in types else "inferred",
            "blank_count": int(blank.sum()),
            "blank_pct": round(100 * blank.mean(), 3) if n else 0,
            "null_token_count": int(null_tok.sum()),
            "null_tokens_seen": ", ".join(sorted(stripped[null_tok].unique())),
            "distinct_count": int(s.nunique()),
            "distinct_pct": round(100 * s.nunique() / n, 3) if n else 0,
            "top_values": "; ".join(f"{k} ({v})" for k, v in s.value_counts().head(3).items()),
            "n_shapes": int(len(shapes)),
            "dominant_shapes": top_shapes,
            "dominant_shape_pct": round(100 * dominant_share, 3),
            "format_families": ", ".join(families_present),
            "mixed_formats": "Y" if mixed else "N",
            "minority_format_count": minority_fmt,
            "case_or_spacing_variants": case_variants,
            "parse_checked": n_checked,
            "parse_fail_count": int(fail_mask.sum()),
            "parse_fail_pct": round(100 * fail_mask.sum() / n_checked, 3) if n_checked else 0,
            "parse_fail_examples": " | ".join(s[fail_mask].unique()[:5]),
            "leading_trailing_ws": lead_trail,
            "internal_double_space": double_space,
            "numeric_min": num.min() if num.notna().any() else "",
            "numeric_max": num.max() if num.notna().any() else "",
            "numeric_mean": round(num.mean(), 6) if num.notna().any() else "",
            "min_length": int(lengths.min()) if len(lengths) else "",
            "max_length": int(lengths.max()) if len(lengths) else "",
        })
    return rows


# --------------------------------------------------------------------------
# GROUP 1 - column level checks
# --------------------------------------------------------------------------
def column_checks(name: str, df: pd.DataFrame, prof: pd.DataFrame, cfg: dict, F: Findings) -> None:
    n = len(df)
    pk = cfg.get("pk") or df.columns[0]
    required = set(cfg.get("required", []))

    for _, p in prof[prof["table"] == name].iterrows():
        col = p["column"]
        s = df[col]
        is_key = col == pk or col.endswith("_id")

        # --- type parsing ('45.0' in an _id column gets its own finding below)
        only_iaf = col.endswith("_id") and set(
            s[~s.map(format_family).isin({"integer", "blank", "null_token"})].map(format_family)) == {"integer_as_float"}
        if p["parse_fail_count"] > 0 and not only_iaf:
            sev = "ERROR" if (is_key or p["expected_type"] in {"int", "decimal", "date"}) else "WARN"
            fam = s.map(format_family)
            bad_fams = fam[~fam.isin((VALID_FAMILIES[p["expected_type"]] or set()) | {"blank", "null_token"})]
            fam_txt = ", ".join(f"{k}={v}" for k, v in bad_fams.value_counts().items())
            F.add(name, col, 1, "type: fails to parse as expected type", sev, p["parse_fail_count"], n,
                  f"expected {p['expected_type']}; failing formats: {fam_txt}", s[s.isin(s[bad_fams.index])].unique())

        # --- mixed formats (only flag when the column is not free text)
        if p["mixed_formats"] == "Y" and p["expected_type"] != "text":
            fam = s.map(format_family).map(lambda f: MIX_FAMILY.get(f, f))
            fam = fam[~fam.isin({"blank", "null_token"})]
            minority = fam != fam.mode().iloc[0]
            F.add(name, col, 1, "format: mixed formats in one column", "WARN", int(minority.sum()), n,
                  f"format families present: {p['format_families']}; minority format rows shown",
                  s[minority[minority].index].unique())
        if p["expected_type"] == "text" and p["distinct_count"] <= 60 and 90 <= p["dominant_shape_pct"] < 100:
            # low-cardinality code column where a minority of values look different
            shapes = s.map(value_shape)
            minority = shapes != shapes.mode().iloc[0]
            if p["column"] not in cfg.get("allowed", {}):  # allowed-value check covers listed columns
                F.add(name, col, 1, "format: minority value shape in a code column", "WARN", int(minority.sum()), n,
                      f"dominant shape covers {p['dominant_shape_pct']}% of rows", s[minority].unique())
        if p["case_or_spacing_variants"] > 0:
            F.add(name, col, 1, "format: same value in different case/spacing", "WARN",
                  p["case_or_spacing_variants"], n, "distinct values that collapse after lower()+strip()")

        # --- whitespace
        if p["leading_trailing_ws"] > 0:
            sev = "ERROR" if is_key else "WARN"
            F.add(name, col, 1, "whitespace: leading/trailing spaces", sev, p["leading_trailing_ws"], n,
                  "breaks exact-match joins and group-bys", s[s != s.str.strip()].map(repr).unique())
        if p["internal_double_space"] > 0:
            F.add(name, col, 1, "whitespace: internal double spaces", "WARN", p["internal_double_space"], n, "")

        # --- completeness
        if p["blank_count"] > 0:
            if col == pk:
                sev, why = "ERROR", "primary key is blank"
            elif col in required:
                sev, why = "ERROR", "required column is blank"
            else:
                sev, why = "INFO", "blank in an optional column - may be legitimate (see group 2 rules)"
            F.add(name, col, 1, "completeness: blank values", sev, p["blank_count"], n, why)
        if p["null_token_count"] > 0:
            F.add(name, col, 1, "completeness: null-like text token", "INFO", p["null_token_count"], n,
                  f"tokens {p['null_tokens_seen']!r} stored as text; decide whether it means 'missing' "
                  f"or is a real value (e.g. 'no certification')")

        # --- integer IDs stored as floats ('45.0') - breaks joins on text keys
        if col.endswith("_id"):
            iaf = s.str.strip().str.match(RE_INT_AS_FLOAT.pattern)
            if iaf.any():
                F.add(name, col, 1, "type: integer key stored as float text ('45.0')", "ERROR", int(iaf.sum()), n,
                      "'45.0' does not equal '45' in a text join; a float key also cannot hold a missing value as int",
                      s[iaf].unique())

    # --- key uniqueness
    if pk in df.columns:
        dup = df[pk].duplicated(keep=False) & (df[pk].str.strip() != "")
        F.add(name, pk, 1, "key: primary key uniqueness", "ERROR" if dup.any() else "INFO", int(dup.sum()), n,
              f"{df[pk].nunique()} distinct of {n} rows" + ("" if dup.any() else " - PK is unique"),
              df.loc[dup, pk].unique())

    # --- allowed values
    for col, allowed in cfg.get("allowed", {}).items():
        if col not in df.columns:
            continue
        s = df[col]
        bad = (s.str.strip() != "") & ~s.isin(allowed)
        if bad.any():
            sev = "WARN" if s[bad].str.strip().isin(allowed).all() else "ERROR"
            F.add(name, col, 1, "allowed values: value not in code list", sev, int(bad.sum()), n,
                  f"allowed = {sorted(allowed)[:12]}{'...' if len(allowed) > 12 else ''}; "
                  f"bad values: {s[bad].value_counts().head(8).to_dict()}", s[bad].unique())

    # --- regex patterns
    for col, (pat, sev, why) in cfg.get("patterns", {}).items():
        if col in df.columns:
            bad = (df[col] != "") & ~df[col].str.match(pat)
            F.add(name, col, 1, "pattern: value does not match expected pattern", sev, int(bad.sum()), n, why,
                  df.loc[bad, col].unique())

    # --- ranges (numeric or ISO date)
    for col, (lo, hi, sev, why) in cfg.get("ranges", {}).items():
        if col not in df.columns:
            continue
        if isinstance(lo, str) or isinstance(hi, str):
            v = to_date(df[col])
            lo_v = pd.Timestamp(lo) if lo else None
            hi_v = pd.Timestamp(hi) if hi else None
        else:
            v = to_number(df[col])
            lo_v, hi_v = lo, hi
        bad = pd.Series(False, index=df.index)
        if lo_v is not None:
            bad |= v < lo_v
        if hi_v is not None:
            bad |= v > hi_v
        F.add(name, col, 1, f"range: outside [{lo}, {hi}]", sev, int(bad.sum()), n,
              f"{why}; observed min={v.min()}, max={v.max()}", df.loc[bad, col].unique())

    # --- placeholder / sentinel values: one exact value far more common than any other
    #     in a high-cardinality numeric column (e.g. aum = 1000.0 used as a floor)
    for col in df.columns:
        if col == pk or col.endswith("_id"):
            continue
        exp = (cfg.get("types", {}).get(col)) or infer_type(df[col])
        if exp not in {"decimal", "int"} or df[col].nunique() < 0.5 * n:
            continue
        vc = df.loc[df[col].str.strip() != "", col].value_counts()
        if len(vc) > 1 and vc.iloc[0] >= 20 and vc.iloc[0] >= 10 * vc.iloc[1]:
            F.add(name, col, 1, "placeholder: one value repeated far more than any other", "WARN",
                  int(vc.iloc[0]), n,
                  f"value {vc.index[0]!r} appears {vc.iloc[0]} times; next most common appears {vc.iloc[1]} times "
                  f"- likely a default/floor, not a real measurement", [vc.index[0]])

    # --- generic extreme outliers (3 x IQR) on numeric columns with no stated range
    ranged = set(cfg.get("ranges", {}))
    for col in df.columns:
        if col in ranged or col.endswith("_id") or col == pk:
            continue
        exp = (cfg.get("types", {}).get(col)) or infer_type(df[col])
        if exp != "decimal":
            continue
        v = to_number(df[col]).dropna()
        if len(v) < 20:
            continue
        q1, q3 = v.quantile([0.25, 0.75])
        iqr = q3 - q1
        lo, hi = q1 - 3 * iqr, q3 + 3 * iqr
        out = (v < lo) | (v > hi)
        if out.any():
            F.add(name, col, 1, "outlier: beyond 3 x IQR", "INFO", int(out.sum()), len(df),
                  f"fences [{lo:.4g}, {hi:.4g}]; flagged does not mean wrong - check against business bounds",
                  df.loc[out[out].index, col].unique())


# --------------------------------------------------------------------------
# GROUP 2 - single-table rules (generic part)
# --------------------------------------------------------------------------
def single_table_generic(name: str, df: pd.DataFrame, cfg: dict, F: Findings) -> None:
    n = len(df)
    pk = cfg.get("pk") or df.columns[0]

    exact = df.duplicated(keep=False)
    F.add(name, "(all)", 2, "duplicates: exact duplicate rows", "ERROR", int(exact.sum()), n, "")

    non_key = [c for c in df.columns if c != pk]
    dup_nk = df.duplicated(subset=non_key, keep=False) & ~exact
    F.add(name, "(all but PK)", 2, "duplicates: same row content under different keys", "ERROR",
          int(dup_nk.sum()), n, f"identical except {pk}", df.loc[dup_nk, pk].unique())

    # normalised duplicates: same after trim + lower-case
    norm = df[non_key].apply(lambda c: c.str.strip().str.lower())
    dup_norm = norm.duplicated(keep=False) & ~df.duplicated(subset=non_key, keep=False)
    F.add(name, "(all but PK)", 2, "near-duplicates: identical after trim/lower-case", "WARN",
          int(dup_norm.sum()), n, "", df.loc[dup_norm, pk].unique())

    # near-duplicates: rows that agree on every non-key column except ONE
    # (catches truncated names, a changed city, a typo in one field ...)
    if len(non_key) >= 4:
        for col in non_key:
            others = [c for c in non_key if c != col]
            nd = df.duplicated(subset=others, keep=False) & ~df.duplicated(subset=non_key, keep=False)
            if nd.any():
                grp = df[nd].groupby(others, sort=False)[col].apply(lambda x: " vs ".join(x.astype(str)))
                # In a dimension, or when the differing column is a key, this is suspicious (WARN).
                # In a fact table where only a measure/date differs it can be two real events (INFO).
                sev = "WARN" if (name.startswith("dim_") or col.endswith("_id")) else "INFO"
                F.add(name, col, 2, "near-duplicates: rows identical except in this one column", sev,
                      int(nd.sum()), n,
                      f"{len(grp)} groups; other columns all match (excluding {pk})",
                      [f"{pk}s {list(df.loc[nd, pk][(df.loc[nd, others] == list(k if isinstance(k, tuple) else (k,))).all(axis=1)])}: {v}"
                       for k, v in grp.head(3).items()])

    # natural keys
    for keys in cfg.get("natural_keys", []):
        if all(k in df.columns for k in keys):
            d = df.duplicated(subset=keys, keep=False)
            F.add(name, "+".join(keys), 2, "duplicates: natural/business key not unique",
                  "ERROR" if len(keys) > 1 or keys[0] in ("date", "ticker") else "WARN",
                  int(d.sum()), n, f"key = {keys}", df.loc[d, keys].drop_duplicates().astype(str).agg("/".join, axis=1))


# --------------------------------------------------------------------------
# GROUP 2 - single-table rules specific to Wildcat Capital (row contradictions)
# --------------------------------------------------------------------------
def single_table_rules(T: dict[str, pd.DataFrame], F: Findings) -> None:
    # ---- dim_client
    if "dim_client" in T:
        c = T["dim_client"]
        n = len(c)
        aum = to_number(c["initial_aum"])
        # tier vs initial AUM: report the observed bands, flag overlaps
        bands = c.assign(aum=aum).groupby("client_tier")["aum"].agg(["min", "max"]).round(2)
        F.add("dim_client", "client_tier+initial_aum", 2, "row rule: client tier vs initial AUM bands", "INFO", 0, n,
              f"observed bands {bands.to_dict('index')}")
        rule = ((c["client_tier"] == "Retail") & (aum >= 500_000)) | \
               ((c["client_tier"] == "HNW") & ((aum < 500_000) | (aum >= 5_000_000))) | \
               ((c["client_tier"] == "UHNW") & (aum < 5_000_000))
        F.add("dim_client", "client_tier+initial_aum", 2,
              "row rule: tier contradicts initial AUM (Retail<500k, HNW 500k-5M, UHNW>=5M)", "WARN",
              int(rule.sum()), n, "", c.loc[rule, "client_id"])
        age = to_number(c["age"])
        since = to_date(c["client_since"])
        too_young = (age - (2024 - since.dt.year)) < 18
        F.add("dim_client", "age+client_since", 2, "row rule: client would have been under 18 when onboarded",
              "WARN", int(too_young.sum()), n, "age is assumed to be as of 2024", c.loc[too_young, "client_id"])

    # ---- dim_date: derived fields must agree with the date
    if "dim_date" in T:
        d = T["dim_date"]
        n = len(d)
        dt = to_date(d["date"])
        checks = {
            "year": dt.dt.year.astype("Int64").astype(str),
            "quarter": dt.dt.quarter.astype("Int64").astype(str),
            "month": dt.dt.month.astype("Int64").astype(str),
            "month_name": dt.dt.month_name(),
            "day_of_week": dt.dt.day_name(),
            "week_of_year": dt.dt.isocalendar().week.astype("Int64").astype(str),
            "is_month_end": dt.dt.is_month_end.astype(int).astype(str),
            "is_quarter_end": dt.dt.is_quarter_end.astype(int).astype(str),
            "is_year_end": dt.dt.is_year_end.astype(int).astype(str),
        }
        for col, expected in checks.items():
            if col in d.columns:
                bad = d[col].str.strip() != expected
                F.add("dim_date", col, 2, f"row rule: {col} disagrees with date", "ERROR", int(bad.sum()), n,
                      "recomputed from the date column", d.loc[bad, "date"])
        wkend = dt.dt.dayofweek >= 5
        bad = wkend & (d["is_trading_day"] == "1")
        F.add("dim_date", "is_trading_day", 2, "row rule: weekend marked as trading day", "ERROR",
              int(bad.sum()), n, "", d.loc[bad, "date"])
        hol = ~wkend & (d["is_trading_day"] == "0")
        F.add("dim_date", "is_trading_day", 2, "row rule: weekday marked non-trading (market holidays)", "INFO",
              int(hol.sum()), n, "expected ~9-10 NYSE holidays per year; 0 means holidays are not modelled")
        full = pd.date_range(dt.min(), dt.max())
        missing = len(full) - dt.nunique()
        F.add("dim_date", "date", 2, "completeness: calendar gaps between min and max date",
              "ERROR" if missing else "INFO", missing, n, f"calendar {dt.min().date()} to {dt.max().date()}")

    # ---- dim_security
    if "dim_security" in T:
        s = T["dim_security"]
        n = len(s)
        etf = s["is_etf"] == "True"
        has_er = s["expense_ratio_bps"].str.strip() != ""
        has_sector = s["sector"].str.strip() != ""
        F.add("dim_security", "expense_ratio_bps", 2, "row rule: ETF without expense ratio", "ERROR",
              int((etf & ~has_er).sum()), n, "every fund has an expense ratio")
        F.add("dim_security", "expense_ratio_bps", 2, "row rule: single stock/bond with expense ratio", "WARN",
              int((~etf & has_er).sum()), n, "")
        F.add("dim_security", "expense_ratio_bps", 2, "structure: blank expense ratio = not an ETF", "INFO",
              int((~etf & ~has_er).sum()), n, "legitimate: individual securities have no expense ratio (do not fill)")
        F.add("dim_security", "sector", 2, "structure: blank sector = ETF", "INFO",
              int((etf & ~has_sector).sum()), n, "legitimate: funds span sectors (do not fill)")
        F.add("dim_security", "sector", 2, "row rule: non-ETF without sector", "WARN",
              int((~etf & ~has_sector).sum()), n, "")
        dn = s["name"].duplicated(keep=False)
        F.add("dim_security", "name", 2, "duplicates: same security name, different ticker", "INFO", int(dn.sum()), n,
              "tickers differ, so these are different securities with similar generated names",
              s.loc[dn, "name"].unique())
        mix = s.groupby("asset_class")["benchmark_id"].nunique()
        F.add("dim_security", "asset_class+benchmark_id", 2, "consistency: benchmarks per asset class", "INFO", 0, n,
              f"distinct benchmark_ids per asset_class: {mix.to_dict()}")

    # ---- fact_fees: amount = aum_basis x bps / 10,000 / 12
    if "fact_fees" in T:
        f = T["fact_fees"]
        n = len(f)
        calc = to_number(f["aum_basis"]) * to_number(f["bps_charged"]) / 10_000 / 12
        diff = (calc - to_number(f["amount"])).abs()
        bad = diff > 0.05
        F.add("fact_fees", "amount", 2, "reconcile: amount != aum_basis x bps / 10,000 / 12", "ERROR",
              int(bad.sum()), n, f"tolerance 0.05; max abs diff {diff.max():.4f}", f.loc[bad, "fee_id"])
        not_first = to_date(f["fee_date"]).dt.day != 1
        F.add("fact_fees", "fee_date", 2, "row rule: monthly fee not dated on the 1st", "WARN",
              int(not_first.sum()), n, "")

    # ---- fact_portfolio_snapshot
    if "fact_portfolio_snapshot" in T:
        p = T["fact_portfolio_snapshot"]
        n = len(p)
        dt = to_date(p["snapshot_date"])
        bad = (p["year"] != dt.dt.year.astype("Int64").astype(str)) | \
              (to_number(p["month"]) != dt.dt.month)
        F.add("fact_portfolio_snapshot", "year+month", 2, "row rule: year/month disagree with snapshot_date",
              "ERROR", int(bad.sum()), n, "", p.loc[bad, "snapshot_id"])
        not_first = dt.dt.day != 1
        F.add("fact_portfolio_snapshot", "snapshot_date", 2, "row rule: monthly snapshot not dated on the 1st",
              "WARN", int(not_first.sum()), n, "")

        is_pct = p["return_ytd"].str.strip().str.endswith("%")
        F.add("fact_portfolio_snapshot", "return_ytd", 2,
              "format: return stored as percent string ('9.28%') while others are fractions", "ERROR",
              int(is_pct.sum()), n, "SUM/AVG will fail or be 100x off; check against benchmark + alpha before converting",
              p.loc[is_pct, "return_ytd"])
        # alpha = return - benchmark return (reconciliation)
        ret = to_number(p["return_ytd"])                      # % strings divided by 100
        alpha_calc = ret - to_number(p["benchmark_return_ytd"])
        diff = (alpha_calc - to_number(p["active_alpha_ytd"])).abs()
        tol = np.where(is_pct, 0.00006, 0.000002)              # a 2-dp percent carries rounding
        bad = diff > tol
        F.add("fact_portfolio_snapshot", "active_alpha_ytd", 2,
              "reconcile: active_alpha_ytd != return_ytd - benchmark_return_ytd", "ERROR", int(bad.sum()), n,
              f"after converting % strings /100; tolerance 2e-6 (6e-5 for % rows)", p.loc[bad, "snapshot_id"])
        pct_ok = is_pct & ~bad
        F.add("fact_portfolio_snapshot", "return_ytd", 2,
              "reconcile: % strings that match benchmark + alpha after dividing by 100", "INFO",
              int(pct_ok.sum()), n, f"{int(pct_ok.sum())} of {int(is_pct.sum())} percent strings reconcile -> "
              "the /100 conversion is confirmed by other columns, not assumed")

        # month-over-month AUM jumps within a client (plausibility)
        q = p[["snapshot_id", "client_id", "snapshot_date"]].assign(aum=to_number(p["aum"]))
        q = q.sort_values(["client_id", "snapshot_date"])
        chg = q.groupby("client_id")["aum"].pct_change()
        big = chg.abs() > 0.5
        F.add("fact_portfolio_snapshot", "aum", 2, "plausibility: AUM moves more than 50% in one month", "WARN",
              int(big.sum()), n, "could be a large deposit/withdrawal; reconcile against fact_transactions "
              "before treating as an error", q.loc[big, "snapshot_id"])

    # ---- fact_transactions
    if "fact_transactions" in T:
        t = T["fact_transactions"]
        n = len(t)
        sec_types = {"Buy", "Sell", "Dividend"}
        cash_types = {"Deposit", "Withdrawal", "Advisory Fee"}
        has_sec = t["security_id"].str.strip() != ""
        has_sh = t["shares"].str.strip() != ""
        has_px = t["price"].str.strip() != ""
        is_sec = t["txn_type"].isin(sec_types)
        is_cash = t["txn_type"].isin(cash_types)
        bad = is_sec & ~(has_sec & has_sh & has_px)
        F.add("fact_transactions", "security_id/shares/price", 2,
              "row rule: security txn missing security/shares/price", "ERROR", int(bad.sum()), n, "",
              t.loc[bad, "txn_id"])
        bad = is_cash & (has_sec | has_sh | has_px)
        F.add("fact_transactions", "security_id/shares/price", 2,
              "row rule: cash txn carries security/shares/price", "WARN", int(bad.sum()), n, "",
              t.loc[bad, "txn_id"])
        F.add("fact_transactions", "security_id/shares/price", 2,
              "structure: blank security/shares/price on cash transactions", "INFO",
              int((is_cash & ~has_sec).sum()), n,
              "legitimate: deposits, withdrawals and fee debits have no security (do not fill)")

        shares = to_number(t["shares"])
        price = to_number(t["price"])
        amt = to_number(t["amount"])
        calc = shares * price
        diff = (calc - amt).abs()
        abs_diff = (shares.abs() * price - amt).abs()
        bad = is_sec & (diff > 0.05)
        F.add("fact_transactions", "amount", 2, "reconcile: amount != shares x price", "ERROR",
              int(bad.sum()), n, f"by type {t.loc[bad, 'txn_type'].value_counts().to_dict()}; "
              f"{int((bad & (abs_diff <= 0.05)).sum())} reconcile if the sign of shares is flipped",
              t.loc[bad, "txn_id"])
        neg = shares < 0
        F.add("fact_transactions", "shares", 2, "sign rule: negative shares", "ERROR", int(neg.sum()), n,
              f"by type {t.loc[neg, 'txn_type'].value_counts().to_dict()}; direction should come from txn_type",
              t.loc[neg, "txn_id"])


# --------------------------------------------------------------------------
# GROUP 3 - referential integrity
# --------------------------------------------------------------------------
def check_referential_integrity(fact_df, fact_key, dim_df, dim_key) -> dict:
    """Raw (exact-text) referential integrity check. Blank FKs are counted
    separately as null keys and excluded from the match rate."""
    fk = fact_df[fact_key]
    null = fk.str.strip() == ""
    present = fk[~null]
    valid = set(dim_df[dim_key])
    matched = present.isin(valid)
    orphans = present[~matched]
    return {
        "rows": len(fk),
        "null_key_rows": int(null.sum()),
        "null_key_rate": float(null.mean()) if len(fk) else 0.0,
        "checked_rows": int(len(present)),
        "matched_rows": int(matched.sum()),
        "match_rate": float(matched.mean()) if len(present) else 1.0,
        "orphan_rows": int((~matched).sum()),
        "orphan_keys": sorted(orphans.unique(), key=lambda x: (len(x), x)),
    }


def infer_fks(T: dict[str, pd.DataFrame]) -> list[tuple[str, str, str, str]]:
    """For tables not in SCHEMA: <x>_id in a table -> PK <x>_id of dim_<x>."""
    rels = []
    for name, df in T.items():
        if name in SCHEMA:
            continue
        pk = df.columns[0]
        for col in df.columns:
            if col == pk or not col.endswith("_id"):
                continue
            parent = "dim_" + col[:-3]
            if parent in T and col in T[parent].columns:
                rels.append((name, col, parent, col))
    return rels


def referential_checks(T: dict[str, pd.DataFrame], F: Findings) -> pd.DataFrame:
    rels = []
    for name, cfg in SCHEMA.items():
        for col, (ptab, pcol) in cfg.get("fks", {}).items():
            rels.append((name, col, ptab, pcol))
    rels += infer_fks(T)

    out = []
    for child, col, parent, pcol in rels:
        if child not in T or parent not in T or col not in T[child] or pcol not in T[parent]:
            continue
        r = check_referential_integrity(T[child], col, T[parent], pcol)
        # does the problem disappear once '45.0' -> '45' ? (diagnostic only)
        norm_child = T[child][col].str.strip().str.replace(r"\.0+$", "", regex=True)
        norm_present = norm_child[norm_child != ""]
        norm_match = norm_present.isin(set(T[parent][pcol].str.strip())).mean() if len(norm_present) else 1.0
        rel_name = f"{child}.{col} -> {parent}.{pcol}"
        out.append({"relationship": rel_name, **{k: v for k, v in r.items() if k != "orphan_keys"},
                    "distinct_orphan_keys": len(r["orphan_keys"]),
                    "match_rate_if_trailing_.0_removed": round(norm_match, 6)})
        detail = (f"match rate {r['match_rate']:.2%} on {r['checked_rows']} non-blank keys; "
                  f"{len(r['orphan_keys'])} distinct orphan keys; null-key rate {r['null_key_rate']:.2%}")
        if r["orphan_rows"] and norm_match > r["match_rate"]:
            detail += f"; match rate becomes {norm_match:.2%} once '.0' is stripped (format problem, not missing parent)"
        sev = "ERROR" if r["orphan_rows"] else "INFO"
        F.add(child, col, 3, f"FK {rel_name}", sev, r["orphan_rows"], r["rows"], detail, r["orphan_keys"])
        if r["null_key_rows"]:
            F.add(child, col, 3, f"FK null keys {rel_name}", "INFO", r["null_key_rows"], r["rows"],
                  "blank foreign key - check whether it is structural (e.g. cash transactions)")

    # dimension members never used by any fact (reverse direction - INFO)
    for parent, pcol in [("dim_client", "client_id"), ("dim_advisor", "advisor_id"),
                         ("dim_security", "security_id"), ("dim_benchmark", "benchmark_id")]:
        if parent not in T:
            continue
        used = set()
        for child, col, p2, _ in rels:
            if p2 == parent and child.startswith("fact_") and child in T:
                used |= set(T[child][col].str.strip().str.replace(r"\.0+$", "", regex=True))
        unused = ~T[parent][pcol].isin(used)
        F.add(parent, pcol, 3, "unused dimension members (no fact rows)", "INFO", int(unused.sum()),
              len(T[parent]), "not an error by itself; check whether they are duplicates or inactive",
              T[parent].loc[unused, pcol])
    return pd.DataFrame(out)


# --------------------------------------------------------------------------
# GROUP 4 - cross-table business rules
# --------------------------------------------------------------------------
def cross_table_rules(T: dict[str, pd.DataFrame], F: Findings) -> None:
    need = {"dim_client", "fact_fees", "fact_portfolio_snapshot", "fact_transactions"}
    if not need <= set(T):
        return
    c = T["dim_client"]
    fees = T["fact_fees"]
    snap = T["fact_portfolio_snapshot"]
    tx = T["fact_transactions"]
    cl = c[["client_id", "advisor_id", "primary_account_type", "client_since", "is_active",
            "risk_profile", "first_name", "last_name", "age", "zip_code"]]

    # 4.1 advisor on the fact == client's advisor in the dimension
    for name, df, key in [("fact_fees", fees, "fee_id"), ("fact_portfolio_snapshot", snap, "snapshot_id"),
                          ("fact_transactions", tx, "txn_id")]:
        m = df[[key, "client_id", "advisor_id"]].merge(cl[["client_id", "advisor_id"]], on="client_id",
                                                       how="inner", suffixes=("", "_dim"))
        bad = m["advisor_id"] != m["advisor_id_dim"]
        F.add(name, "advisor_id", 4, "fact advisor_id differs from dim_client.advisor_id", "WARN",
              int(bad.sum()), len(df), f"{len(m)} rows with a matching client compared", m.loc[bad, key])

    # 4.2 snapshot account type == client's primary account type
    m = snap.merge(cl[["client_id", "primary_account_type"]], on="client_id", how="inner")
    bad = m["account_type"] != m["primary_account_type"]
    F.add("fact_portfolio_snapshot", "account_type", 4, "account_type differs from dim_client.primary_account_type",
          "WARN", int(bad.sum()), len(snap), "", m.loc[bad, "snapshot_id"])

    # 4.3 fee aum_basis == snapshot aum for the same client and month
    m = fees.merge(snap[["client_id", "snapshot_date", "aum"]], left_on=["client_id", "fee_date"],
                   right_on=["client_id", "snapshot_date"], how="left")
    no_snap = m["aum"].isna()
    diff = (to_number(m["aum_basis"]) - to_number(m["aum"])).abs()
    bad = ~no_snap & (diff > 0.01)
    F.add("fact_fees", "aum_basis", 4, "reconcile: fee aum_basis != snapshot aum (same client, month)", "ERROR",
          int(bad.sum()), len(fees), f"{int((~no_snap).sum())} fee rows matched to a snapshot", m.loc[bad, "fee_id"])
    F.add("fact_fees", "fee_date", 4, "fee month with no portfolio snapshot", "WARN", int(no_snap.sum()),
          len(fees), "", m.loc[no_snap, "fee_id"])
    k_fee = set(zip(fees["client_id"], fees["fee_date"]))
    no_fee = ~pd.Series(list(zip(snap["client_id"], snap["snapshot_date"]))).isin(k_fee)
    F.add("fact_portfolio_snapshot", "snapshot_date", 4, "snapshot month with no fee charged", "INFO",
          int(no_fee.sum()), len(snap), "could be fee waivers or quarterly billing - investigate, not automatically wrong")

    # 4.3b placeholder AUM: the dominant repeated AUM value (e.g. 1000.0) contaminates fees and returns
    vc = snap["aum"].value_counts()
    if len(vc) > 1 and vc.iloc[0] >= 20 and vc.iloc[0] >= 10 * vc.iloc[1]:
        ph = vc.index[0]
        ph_snap = snap[snap["aum"] == ph]
        ph_keys = set(zip(ph_snap["client_id"], ph_snap["snapshot_date"]))
        fee_on_ph = pd.Series(list(zip(fees["client_id"], fees["fee_date"])), index=fees.index).isin(ph_keys)
        F.add("fact_fees", "aum_basis+amount", 4, f"fee charged on placeholder snapshot AUM {ph!r}", "ERROR",
              int(fee_on_ph.sum()), len(fees),
              f"{int((fees.loc[fee_on_ph, 'aum_basis'] == ph).sum())} of these also carry aum_basis={ph!r}; "
              f"amounts {to_number(fees.loc[fee_on_ph, 'amount']).min()}-{to_number(fees.loc[fee_on_ph, 'amount']).max()} "
              "are fees on a fake balance", fees.loc[fee_on_ph, "fee_id"])
        ph_clients = set(ph_snap["client_id"])
        extreme = (to_number(snap["return_ytd"]).abs() > 1) | (to_number(snap["active_alpha_ytd"]).abs() > 1)
        linked = extreme & snap["client_id"].isin(ph_clients)
        F.add("fact_portfolio_snapshot", "return_ytd+active_alpha_ytd", 4,
              f"extreme return/alpha (>100%) for clients with placeholder AUM {ph!r} months", "WARN",
              int(linked.sum()), len(snap),
              f"{int(linked.sum())} of {int(extreme.sum())} extreme rows belong to the {len(ph_clients)} placeholder "
              f"clients ({int((linked & (snap['aum'] == ph)).sum())} on the placeholder months themselves); "
              "returns computed on/after a fake balance are not real performance",
              snap.loc[linked, "snapshot_id"])
        F.add("fact_portfolio_snapshot", "return_ytd+active_alpha_ytd", 4,
              "extreme return/alpha (>100%) for clients with no placeholder months", "INFO",
              int((extreme & ~linked).sum()), len(snap),
              "likely small accounts with large cash flows - reconcile against deposits before calling them wrong",
              snap.loc[extreme & ~linked, "snapshot_id"])

    # 4.4 benchmark return is a market number: one value per benchmark per date
    g = snap.groupby(["benchmark_id", "snapshot_date"])["benchmark_return_ytd"].nunique()
    bad_keys = g[g > 1]
    rows = snap.set_index(["benchmark_id", "snapshot_date"]).index.isin(bad_keys.index)
    F.add("fact_portfolio_snapshot", "benchmark_return_ytd", 4,
          "same benchmark + date has more than one benchmark return", "ERROR", int(rows.sum()), len(snap),
          f"{len(bad_keys)} benchmark-dates inconsistent of {len(g)}",
          [f"{a}/{b}" for a, b in bad_keys.index[:5]])

    # 4.5 snapshot benchmark vs client risk profile (mapping learned from the data)
    m = snap[["snapshot_id", "client_id", "benchmark_id"]].merge(cl[["client_id", "risk_profile"]], on="client_id")
    known = m[m["risk_profile"] != ""]
    mapping = known.groupby("risk_profile")["benchmark_id"].agg(lambda x: x.mode().iloc[0])
    expected = known["risk_profile"].map(mapping)
    bad = known["benchmark_id"] != expected
    F.add("fact_portfolio_snapshot", "benchmark_id", 4,
          "benchmark inconsistent with client risk_profile", "WARN", int(bad.sum()), len(snap),
          f"dominant mapping risk_profile->benchmark_id: {mapping.to_dict()}", known.loc[bad, "snapshot_id"])
    blank_rp = c[c["risk_profile"] == ""]
    bench_of_blank = snap[snap["client_id"].isin(blank_rp["client_id"])].groupby("client_id")["benchmark_id"].nunique()
    F.add("dim_client", "risk_profile", 4, "blank risk_profile - snapshot benchmark shows the client's mandate",
          "INFO", len(blank_rp), len(c),
          f"{int((bench_of_blank == 1).sum())} of {len(blank_rp)} blank-profile clients have exactly one benchmark in "
          f"snapshots; but several profiles share a benchmark, so the profile cannot be recovered with certainty",
          blank_rp["client_id"])

    # 4.6 activity before the client relationship started
    since = cl.set_index("client_id")["client_since"]
    td = tx["client_id"].map(since)
    bad = td.notna() & (tx["txn_date"] < td)
    F.add("fact_transactions", "txn_date", 4, "transaction dated before client_since", "ERROR", int(bad.sum()),
          len(tx), "", tx.loc[bad, "txn_id"])
    for name, df, dcol, key in [("fact_portfolio_snapshot", snap, "snapshot_date", "snapshot_id"),
                                ("fact_fees", fees, "fee_date", "fee_id")]:
        sd = df["client_id"].map(since)
        month_before = sd.notna() & (df[dcol].str[:7] < sd.str[:7])
        same_month = sd.notna() & (df[dcol] < sd) & ~month_before
        F.add(name, dcol, 4, f"{dcol} in a month before client_since", "ERROR", int(month_before.sum()),
              len(df), "", df.loc[month_before, key])
        F.add(name, dcol, 4, f"{dcol} (1st of month) earlier than client_since in the SAME month", "INFO",
              int(same_month.sum()), len(df),
              "structural: monthly rows are stamped on the 1st, so a client who joined mid-month has a row "
              "dated a few days before client_since - not a defect")

    # 4.7 activity after the relationship ended: snapshots/fees stop when a client leaves,
    #     so a transaction in a later month has no account to belong to
    inactive = set(c.loc[c["is_active"] == "False", "client_id"])
    last_snap = snap.groupby("client_id")["snapshot_date"].max()
    lm = tx["client_id"].map(last_snap)
    after = lm.notna() & (tx["txn_date"].str[:7] > lm.str[:7])
    F.add("fact_transactions", "txn_date", 4, "transaction in a month after the client's last portfolio snapshot",
          "WARN", int(after.sum()), len(tx),
          f"{tx.loc[after, 'client_id'].nunique()} clients ({int(tx.loc[after, 'client_id'].isin(inactive).sum())} "
          f"rows from is_active=False clients); by type {tx.loc[after, 'txn_type'].value_counts().to_dict()}",
          tx.loc[after, "txn_id"])
    active_stopped = last_snap[(last_snap < last_snap.max()) & ~last_snap.index.isin(inactive)]
    F.add("dim_client", "is_active", 4, "is_active=True but snapshots stop before the end of the extract", "WARN",
          len(active_stopped), len(c), f"extract ends {last_snap.max()}", active_stopped.index)
    stopped = last_snap[last_snap.index.isin(inactive)]
    F.add("dim_client", "is_active", 4, "inactive clients: last snapshot month", "INFO", len(stopped), len(c),
          f"{len(inactive)} inactive clients; last snapshot ranges {stopped.min()} to {stopped.max()}")

    # 4.8 trades on non-trading days
    if "dim_date" in T:
        d = T["dim_date"]
        non_trading = set(d.loc[d["is_trading_day"] == "0", "date"])
        trades = tx["txn_type"].isin({"Buy", "Sell"})
        bad = trades & tx["txn_date"].isin(non_trading)
        F.add("fact_transactions", "txn_date", 4, "Buy/Sell dated on a non-trading day (dim_date)", "WARN",
              int(bad.sum()), len(tx), f"{int(trades.sum())} trades in total; "
              "could be booking date vs trade date - investigate", tx.loc[bad, "txn_id"])

    # 4.9 dimension near-duplicates that carry facts (helps decide merge vs quarantine)
    nd_cols = ["first_name", "age", "zip_code", "advisor_id"]
    nd = c[c.duplicated(subset=nd_cols, keep=False)]
    if len(nd):
        used = set(fees["client_id"]) | set(snap["client_id"]) | set(tx["client_id"])
        no_facts = nd[~nd["client_id"].isin(used)]
        pairs = nd.groupby(nd_cols)["client_id"].apply(lambda x: "/".join(x)).head(5)
        F.add("dim_client", "client_id", 4,
              "possible duplicate clients (same first name, age, zip, advisor; different client_id)", "WARN",
              len(nd), len(c), f"{len(nd) // 2} pairs; {len(no_facts)} of the {len(nd)} rows have no fact rows at all",
              pairs.values)

    # 4.10 orphan transactions: are they copies of real transactions under a wrong client_id?
    orphan = ~tx["client_id"].isin(set(c["client_id"]))
    if orphan.any():
        body = [x for x in tx.columns if x not in ("txn_id", "client_id")]
        valid_keys = set(map(tuple, tx.loc[~orphan, body].values))
        is_copy = pd.Series([tuple(r) in valid_keys for r in tx.loc[orphan, body].values], index=tx.index[orphan])
        F.add("fact_transactions", "client_id", 4,
              "orphan transactions that duplicate a valid transaction except for client_id", "ERROR",
              int(is_copy.sum()), len(tx),
              f"{int(is_copy.sum())} of {int(orphan.sum())} orphan rows have an identical twin (same advisor, "
              f"security, date, type, shares, price, amount) under a valid client - duplicates, not lost clients",
              tx.loc[is_copy[is_copy].index, "txn_id"])

    # 4.11 transactions for securities: dividends on a security never bought by that client
    if "dim_security" in T:
        sec = tx[tx["security_id"].str.strip() != ""].copy()
        sec["sid"] = sec["security_id"].str.replace(r"\.0+$", "", regex=True)
        bought = set(zip(sec.loc[sec["txn_type"] == "Buy", "client_id"], sec.loc[sec["txn_type"] == "Buy", "sid"]))
        for ttype, sev in [("Sell", "WARN"), ("Dividend", "INFO")]:
            sub = sec[sec["txn_type"] == ttype]
            bad = ~pd.Series(list(zip(sub["client_id"], sub["sid"])), index=sub.index).isin(bought)
            F.add("fact_transactions", "security_id", 4, f"{ttype} on a security the client never bought in the extract",
                  sev, int(bad.sum()), len(tx),
                  "may be holdings that pre-date 2020 - flag rather than delete", sub.loc[bad, "txn_id"])


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def load_tables(inputs: list[str]) -> dict[str, pd.DataFrame]:
    files: list[Path] = []
    if not inputs:
        files = [DEFAULT_DATA_DIR / f"{t}.csv" for t in WILDCAT_TABLES]
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            wild = [p / f"{t}.csv" for t in WILDCAT_TABLES]
            files += wild if all(f.exists() for f in wild) else sorted(p.glob("*.csv"))
        else:
            files.append(p)
    T = {}
    for f in files:
        if not f.exists():
            sys.exit(f"File not found: {f}")
        # RULE: read everything as text, keep blanks as "" (no NaN guessing)
        T[f.stem] = pd.read_csv(f, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        print(f"  loaded {f.name:<32} {len(T[f.stem]):>8,} rows x {T[f.stem].shape[1]} cols")
    return T


def main() -> None:
    ap = argparse.ArgumentParser(description="Profile and audit CSV tables (read as text).")
    ap.add_argument("inputs", nargs="*", help="CSV files or folders (default: Wildcat Capital/)")
    ap.add_argument("--out", default=str(DEFAULT_OUT_DIR), help="output folder (default: reports/)")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print("Loading tables as text ...")
    T = load_tables(args.inputs)

    print("Profiling columns ...")
    prof_rows = []
    for name, df in T.items():
        prof_rows += profile_table(name, df, SCHEMA.get(name, {}).get("types", {}))
    prof = pd.DataFrame(prof_rows)

    F = Findings()
    print("Group 1: column-level checks ...")
    for name, df in T.items():
        column_checks(name, df, prof, SCHEMA.get(name, {}), F)
    print("Group 2: single-table rules ...")
    for name, df in T.items():
        single_table_generic(name, df, SCHEMA.get(name, {}), F)
    single_table_rules(T, F)
    print("Group 3: referential integrity ...")
    ri = referential_checks(T, F)
    print("Group 4: cross-table business rules ...")
    cross_table_rules(T, F)

    dq = F.frame()
    prof_path = out / "profile_raw.csv"
    dq_path = out / "dq_summary_raw.csv"
    prof.to_csv(prof_path, index=False)
    dq.to_csv(dq_path, index=False)

    # ---------------- console summary
    pd.set_option("display.width", 200)
    pd.set_option("display.max_colwidth", 70)
    print("\n=== Referential integrity (raw) ===")
    if not ri.empty:
        print(ri[["relationship", "match_rate", "orphan_rows", "distinct_orphan_keys", "null_key_rate"]]
              .to_string(index=False))
    print("\n=== Findings by group and severity ===")
    if not dq.empty:
        print(pd.crosstab(dq["check_group"], dq["severity"], margins=True).to_string())
        print("\n=== ERROR findings ===")
        err = dq[dq["severity"] == "ERROR"]
        print(err[["finding_id", "table", "column", "check", "rows_affected"]].to_string(index=False))
    print(f"\nSaved {prof_path} ({len(prof)} rows) and {dq_path} ({len(dq)} findings)")


if __name__ == "__main__":
    main()
