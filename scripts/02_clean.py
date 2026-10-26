"""
HW4 Part C - Cleaning program for the Wildcat Capital extract.
MIS3060 Business Intelligence with AI

Implements decision_log.md, one transformation at a time:

    Step 0  Load the 8 raw tables as text (dtype=str, keep_default_na=False)
    Step 1  Trim leading/trailing whitespace in every column (safety step)
    Step 2  #1  Quarantine duplicate clients 2501-2525 (truncated last names)
    Step 3  #2  Standardize dim_client.state to USPS codes
    Step 4  #3  Fill blank risk_profile from the snapshot benchmark + flag
    Step 5  #4  Convert fact_transactions.security_id '45.0' -> '45'
    Step 6  #5  Quarantine orphan transactions (copies with a corrupted client_id)
    Step 7  #6  Fix the sign of negative shares on Buy transactions
    Step 8  #7  Convert return_ytd percent strings ('9.28%') to fractions
    Step 9  #8  Flag the 1000.0 AUM placeholder (snapshots and fees)
    Step 10 #9  Flag implausible YTD returns
    Step 11 Final reconciliations and raw = clean + quarantined proof

After EVERY step the script prints rows affected and a before/after check,
then asserts the after-state. If an assert fails, the script stops and no
clean files are written (outputs are only saved at the very end).

The raw files are only read, never written.

Outputs
    data/clean/<table>_clean.csv            8 clean tables (same columns + 0/1 flag columns)
    data/clean/README_clean.md              relationships, BI column types, flags, limitations
    data/audit/cleaning_log.csv             table, issue, action, rows affected, note
    data/audit/value_changes.csv            table, key column, key, column, old value, new value
    data/audit/quarantine_<set>.csv         one file per quarantined set (with reason + trace key)

Usage (from the hw04 folder):
    python scripts/02_clean.py
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# Paths and settings
# --------------------------------------------------------------------------
HW_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = HW_DIR / "Wildcat Capital"
CLEAN_DIR = HW_DIR / "data" / "clean"
AUDIT_DIR = HW_DIR / "data" / "audit"

# The brief says "same file names"; set CLEAN_SUFFIX = "" to write e.g. dim_client.csv instead.
CLEAN_SUFFIX = "_clean"

TABLES = ["dim_advisor", "dim_benchmark", "dim_client", "dim_date", "dim_security",
          "fact_fees", "fact_portfolio_snapshot", "fact_transactions"]

PRIMARY_KEYS = {
    "dim_advisor": "advisor_id", "dim_benchmark": "benchmark_id", "dim_client": "client_id",
    "dim_date": "date_id", "dim_security": "security_id", "fact_fees": "fee_id",
    "fact_portfolio_snapshot": "snapshot_id", "fact_transactions": "txn_id",
}

STATE_CODES = {"Pennsylvania": "PA", "Florida": "FL", "New York": "NY", "New Jersey": "NJ",
               "California": "CA", "Illinois": "IL", "Texas": "TX"}
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
    "VA", "WA", "WV", "WI", "WY", "DC",
}
AUM_PLACEHOLDER = 1000.0
MONEY_TOL = 0.05          # $ tolerance for amount = shares x price, fee recompute
RETURN_TOL = 2e-6         # tolerance for alpha = return - benchmark (6-dp data)
RETURN_TOL_PCT = 6e-5     # converted 2-dp percent strings carry rounding

# --------------------------------------------------------------------------
# Audit-trail helpers
# --------------------------------------------------------------------------
cleaning_log: list[dict] = []
value_changes: list[pd.DataFrame] = []
quarantines: dict[str, pd.DataFrame] = {}


def num(s: pd.Series) -> pd.Series:
    """Text -> number on purpose (blanks become NaN)."""
    return pd.to_numeric(s.str.strip(), errors="coerce")


def banner(step: str, title: str) -> None:
    print("\n" + "=" * 78)
    print(f"{step}: {title}")
    print("=" * 78)


def log(table: str, issue: str, action: str, rows: int, note: str = "") -> None:
    cleaning_log.append({"step": len(cleaning_log) + 1, "table": table, "issue": issue,
                         "action": action, "rows_affected": int(rows), "note": note})
    print(f"  rows affected: {rows:,}")


def record_changes(table: str, df_before: pd.DataFrame, df_after: pd.DataFrame,
                   mask: pd.Series, column: str) -> None:
    """Append one value_changes row per changed cell."""
    key = PRIMARY_KEYS[table]
    ch = pd.DataFrame({
        "table": table,
        "key_column": key,
        "key": df_before.loc[mask, key].values,
        "column": column,
        "old_value": df_before.loc[mask, column].values,
        "new_value": df_after.loc[mask, column].values,
    })
    value_changes.append(ch)


def quarantine(name: str, rows: pd.DataFrame, reason: str, trace: dict[str, pd.Series] | None = None) -> None:
    """Keep removed rows with every original column, the reason and a trace key."""
    q = rows.copy()
    q.insert(0, "quarantine_reason", reason)
    for col, values in (trace or {}).items():
        q[col] = values.reindex(q.index).values
    quarantines[name] = q


def check(label: str, before, after) -> None:
    print(f"  check  {label:<58} before: {before!s:<14} after: {after!s}")


# --------------------------------------------------------------------------
# Step 0 - load
# --------------------------------------------------------------------------
banner("Step 0", "Load raw tables as text")
raw: dict[str, pd.DataFrame] = {}
for t in TABLES:
    path = RAW_DIR / f"{t}.csv"
    raw[t] = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    print(f"  {t:<26} {len(raw[t]):>8,} rows x {raw[t].shape[1]} cols")
RAW_ROWS = {t: len(df) for t, df in raw.items()}
RAW_COLS = {t: list(df.columns) for t, df in raw.items()}
T = {t: df.copy() for t, df in raw.items()}   # working copies; raw stays untouched


# --------------------------------------------------------------------------
# Step 1 - whitespace (safety step: the audit found none, this proves it stays that way)
# --------------------------------------------------------------------------
banner("Step 1", "Trim leading/trailing whitespace in all text cells")
total = 0
for t, df in T.items():
    for col in df.columns:
        stripped = df[col].str.strip()
        mask = stripped != df[col]
        if mask.any():
            before = df.copy()
            df[col] = stripped
            record_changes(t, before, df, mask, col)
            total += int(mask.sum())
    after_ws = sum(int((df[c] != df[c].str.strip()).sum()) for c in df.columns)
    assert after_ws == 0, f"{t}: whitespace remains"
check("cells with leading/trailing whitespace (all tables)", total, 0)
log("(all)", "leading/trailing whitespace", "standardize (trim)", total,
    "audit found none; step kept as a guard")


# --------------------------------------------------------------------------
# Step 2 - decision #1: quarantine duplicate clients
# --------------------------------------------------------------------------
banner("Step 2", "#1 Quarantine duplicate clients (last name off by one letter)")
c = T["dim_client"]
match_cols = [x for x in c.columns if x not in ("client_id", "last_name")]
fact_clients = set(T["fact_fees"]["client_id"]) | set(T["fact_portfolio_snapshot"]["client_id"]) \
    | set(T["fact_transactions"]["client_id"])
# pairs of clients identical in every column except client_id and last_name
pairs = c.merge(c, on=match_cols, suffixes=("_dup", "_orig"))
pairs = pairs[pairs["client_id_dup"] != pairs["client_id_orig"]]
# the copy is the member WITHOUT fact rows; the survivor is the one the facts use
pairs = pairs[~pairs["client_id_dup"].isin(fact_clients) & pairs["client_id_orig"].isin(fact_clients)]
dup_ids = pairs.set_index("client_id_dup")["client_id_orig"]

# verify: 25 pairs, each with exactly one member that has facts, and last names differ by one letter
n_groups = int(c.duplicated(subset=match_cols, keep="first").sum())
name_diff = (pairs["last_name_orig"].str.len() - pairs["last_name_dup"].str.len()).abs()
print(f"  duplicate pairs found: {len(dup_ids)} of {n_groups} matching groups "
      f"(e.g. {dup_ids.index[0]} '{pairs['last_name_dup'].iloc[0]}' -> {dup_ids.iloc[0]} "
      f"'{pairs['last_name_orig'].iloc[0]}')")
print(f"  last-name length difference within pairs: {name_diff.value_counts().to_dict()} "
      f"(23 truncated, e.g. Taylo/Taylor; 2 with a letter added: Leen/Lee, Pacen/Pace)")
assert len(dup_ids) == n_groups == 25, "expected 25 duplicate pairs, each with exactly one member that has facts"
assert dup_ids.index.is_unique and dup_ids.is_unique
assert (name_diff == 1).all(), "a pair differs by more than one letter in last_name"

mask = c["client_id"].isin(dup_ids.index)
quarantine("dim_client_duplicates", c[mask],
           "duplicate client: all columns equal to another client except a one-letter last_name variant; no fact rows",
           {"duplicate_of_client_id": c.loc[mask, "client_id"].map(dup_ids)})
before_rows = len(c)
T["dim_client"] = c[~mask].reset_index(drop=True)
c = T["dim_client"]
check("dim_client rows", before_rows, len(c))
check("rows still matching another client except last_name", len(dup_ids),
      int(c.duplicated(subset=match_cols, keep=False).sum()))
assert len(c) == before_rows - 25
assert c["client_id"].is_unique
assert not c.duplicated(subset=match_cols).any(), "near-duplicates remain"
log("dim_client", "25 duplicate clients (last_name off by one letter), IDs 2501-2525", "quarantine", int(mask.sum()),
    "survivor ID kept in quarantine column duplicate_of_client_id; 0 facts referenced the duplicates")


# --------------------------------------------------------------------------
# Step 3 - decision #2: standardize state
# --------------------------------------------------------------------------
banner("Step 3", "#2 Standardize dim_client.state to USPS two-letter codes")
c = T["dim_client"]
before = c.copy()
bad_before = int((~c["state"].isin(US_STATES)).sum())
counts_before = c["state"].replace(STATE_CODES).value_counts()
mask = c["state"].isin(STATE_CODES)
c.loc[mask, "state"] = c.loc[mask, "state"].map(STATE_CODES)
record_changes("dim_client", before, c, mask, "state")
bad_after = int((~c["state"].isin(US_STATES)).sum())
check("states not in USPS code list", bad_before, bad_after)
assert bad_after == 0, "states outside the USPS list remain"
assert c["state"].value_counts().sort_index().equals(counts_before.sort_index()), \
    "per-state counts changed: mapping was not one-to-one"
log("dim_client", "state stored as full name", "standardize", int(mask.sum()),
    "; ".join(f"{k}->{v}" for k, v in STATE_CODES.items())
    + f"; audit found {78} - the other {78 - int(mask.sum())} was on a duplicate client quarantined in step 2")


# --------------------------------------------------------------------------
# Step 4 - decision #3: fill risk_profile from the benchmark (reconciled) + flag
# --------------------------------------------------------------------------
banner("Step 4", "#3 Fill blank risk_profile where the snapshot benchmark identifies it")
c = T["dim_client"]
snap = T["fact_portfolio_snapshot"]
known = snap[["client_id", "benchmark_id"]].drop_duplicates().merge(
    c.loc[c["risk_profile"] != "", ["client_id", "risk_profile"]], on="client_id")
profiles_per_bench = known.groupby("benchmark_id")["risk_profile"].agg(lambda x: sorted(set(x)))
unique_bench = {b: p[0] for b, p in profiles_per_bench.items() if len(p) == 1}
print(f"  profiles per benchmark: {profiles_per_bench.to_dict()}")
print(f"  benchmarks that identify exactly one profile: {unique_bench}")

client_bench = snap.groupby("client_id")["benchmark_id"].agg(lambda x: sorted(set(x)))
blank = c["risk_profile"] == ""
inferred = c.loc[blank, "client_id"].map(
    lambda cid: unique_bench.get(client_bench[cid][0]) if cid in client_bench and len(client_bench[cid]) == 1 else None)
fill_mask = blank & inferred.reindex(c.index).notna()
blanks_before = int(blank.sum())
before = c.copy()
c["flag_risk_profile_inferred"] = 0
c.loc[fill_mask, "risk_profile"] = inferred[fill_mask[fill_mask].index]
c.loc[fill_mask, "flag_risk_profile_inferred"] = 1
record_changes("dim_client", before, c, fill_mask, "risk_profile")
blanks_after = int((c["risk_profile"] == "").sum())
check("blank risk_profile", blanks_before, blanks_after)
check("flag_risk_profile_inferred = 1", 0, int(c["flag_risk_profile_inferred"].sum()))
# reconcile: every filled client's benchmark must be one used only by that profile
recon = c[fill_mask].apply(lambda r: unique_bench.get(client_bench[r["client_id"]][0]) == r["risk_profile"], axis=1)
assert recon.all(), "a filled risk_profile does not match the benchmark mapping"
assert blanks_after == 0, f"{blanks_after} blank risk_profile remain (expected 0 after quarantine of #1)"
assert int(c["flag_risk_profile_inferred"].sum()) == int(fill_mask.sum()) == 75
log("dim_client", "blank risk_profile", "fill + flag", int(fill_mask.sum()),
    f"filled from snapshot benchmark; benchmark->profile used: {unique_bench}; flag_risk_profile_inferred=1")


# --------------------------------------------------------------------------
# Step 5 - decision #4: security_id '45.0' -> '45'
# --------------------------------------------------------------------------
banner("Step 5", "#4 Convert fact_transactions.security_id from '45.0' to '45'")
tx = T["fact_transactions"]
sec_ids = set(T["dim_security"]["security_id"])
present = tx["security_id"] != ""
match_before = tx.loc[present, "security_id"].isin(sec_ids).mean()
mask = tx["security_id"].str.match(r"^\d+\.0+$")
assert (mask == present).all(), "a non-blank security_id is not of the form N.0 - cannot strip safely"
before = tx.copy()
tx.loc[mask, "security_id"] = tx.loc[mask, "security_id"].str.replace(r"\.0+$", "", regex=True)
record_changes("fact_transactions", before, tx, mask, "security_id")
match_after = tx.loc[present, "security_id"].isin(sec_ids).mean()
check("security_id RI match rate (non-blank keys)", f"{match_before:.2%}", f"{match_after:.2%}")
check("blank security_id (cash transactions, unchanged)", int((~present).sum()), int((tx["security_id"] == "").sum()))
assert match_after == 1.0, "security_id still has orphans after conversion"
assert int((tx["security_id"] == "").sum()) == int((~present).sum()), "blank keys changed"
assert tx.loc[present, "security_id"].str.match(r"^\d+$").all()
log("fact_transactions", "integer key stored as float text ('45.0')", "convert", int(mask.sum()),
    f"RI match {match_before:.2%} -> {match_after:.2%}; blanks on cash transactions left blank")


# --------------------------------------------------------------------------
# Step 6 - decision #5: quarantine orphan transactions
# --------------------------------------------------------------------------
banner("Step 6", "#5 Quarantine orphan transactions (copies of valid ones with a bad client_id)")
tx = T["fact_transactions"]
valid_clients = set(T["dim_client"]["client_id"])
orphan = ~tx["client_id"].isin(valid_clients)
body = [x for x in tx.columns if x not in ("txn_id", "client_id")]
twins = tx[orphan].reset_index().merge(tx[~orphan], on=body, suffixes=("", "_orig"))
twin_of = twins.groupby("index")["txn_id_orig"].min()
print(f"  orphan rows: {int(orphan.sum())}; orphan rows with an exact twin under a valid client: {len(twin_of)}")
assert int(orphan.sum()) == 200, "expected 200 orphan transactions (decision log #5)"
assert len(twin_of) == int(orphan.sum()), "an orphan has no valid twin - it may be a real transaction"

quarantine("fact_transactions_orphans", tx[orphan],
           "client_id not in dim_client; exact copy of a valid transaction except client_id",
           {"duplicate_of_txn_id": twin_of})
amount_q = num(tx.loc[orphan, "amount"]).sum()
before_rows = len(tx)
T["fact_transactions"] = tx[~orphan].reset_index(drop=True)
tx = T["fact_transactions"]
after_orphans = int((~tx["client_id"].isin(valid_clients)).sum())
check("fact_transactions rows", before_rows, len(tx))
check("orphan client_id rows", int(orphan.sum()), after_orphans)
assert after_orphans == 0
assert len(tx) == before_rows - 200
log("fact_transactions", "orphan client_id (2702-3192)", "quarantine", int(orphan.sum()),
    f"all are duplicates of valid txns (trace: duplicate_of_txn_id); amount removed ${amount_q:,.2f}")


# --------------------------------------------------------------------------
# Step 7 - decision #6: negative shares on Buy
# --------------------------------------------------------------------------
banner("Step 7", "#6 Fix the sign of negative shares on Buy transactions")
tx = T["fact_transactions"]
sh, px, amt = num(tx["shares"]), num(tx["price"]), num(tx["amount"])
is_sec = tx["txn_type"].isin({"Buy", "Sell", "Dividend"})
neg = sh < 0
recon_before = ((sh * px - amt).abs() <= MONEY_TOL)[is_sec].mean()
# verify the repair before applying it: |shares| x price must equal amount on every negative row
assert set(tx.loc[neg, "txn_type"]) == {"Buy"}, "negative shares outside Buy"
assert ((sh[neg].abs() * px[neg] - amt[neg]).abs() <= MONEY_TOL).all(), \
    "some negative rows do not reconcile with the sign flipped - cannot assume a sign error"
before = tx.copy()
tx.loc[neg, "shares"] = tx.loc[neg, "shares"].str.lstrip("-")
record_changes("fact_transactions", before, tx, neg, "shares")
sh = num(tx["shares"])
recon_after = ((sh * px - amt).abs() <= MONEY_TOL)[is_sec].mean()
check("negative shares", int(neg.sum()), int((sh < 0).sum()))
check("security txns where amount = shares x price", f"{recon_before:.4%}", f"{recon_after:.4%}")
assert int((sh < 0).sum()) == 0
assert recon_after == 1.0, "amount = shares x price does not hold for every security transaction"
log("fact_transactions", "negative shares on Buy", "convert (absolute value)", int(neg.sum()),
    f"reconciled: amount = shares x price now holds on 100% of Buy/Sell/Dividend rows "
    f"(was {recon_before:.4%}); audit found 836 - the other {836 - int(neg.sum())} was on an orphan row "
    f"quarantined in step 6")


# --------------------------------------------------------------------------
# Step 8 - decision #7: return_ytd percent strings -> fractions
# --------------------------------------------------------------------------
banner("Step 8", "#7 Convert return_ytd percent strings to fractions")
snap = T["fact_portfolio_snapshot"]
is_pct = snap["return_ytd"].str.endswith("%")
bench, alpha = num(snap["benchmark_return_ytd"]), num(snap["active_alpha_ytd"])
candidate = num(snap.loc[is_pct, "return_ytd"].str.rstrip("%")) / 100
# verify BEFORE converting: converted value must equal benchmark + alpha on every row
ok = ((candidate - bench[is_pct] - alpha[is_pct]).abs() <= RETURN_TOL_PCT)
assert ok.all(), f"{int((~ok).sum())} percent rows do not reconcile to benchmark + alpha after /100"
before = snap.copy()
snap.loc[is_pct, "return_ytd"] = candidate.map(lambda v: f"{v:.6f}".rstrip("0").rstrip(".") if v != 0 else "0")
record_changes("fact_portfolio_snapshot", before, snap, is_pct, "return_ytd")
ret = num(snap["return_ytd"])
tol = np.where(is_pct, RETURN_TOL_PCT, RETURN_TOL)
alpha_ok = ((ret - bench - alpha).abs() <= tol)
check("return_ytd values that are not numbers", int(is_pct.sum()), int(ret.isna().sum()))
check("rows where alpha = return - benchmark", "n/a (text)", f"{alpha_ok.mean():.4%}")
assert ret.notna().all(), "return_ytd still has non-numeric values"
assert alpha_ok.all(), "alpha reconciliation fails after conversion"
log("fact_portfolio_snapshot", "return_ytd stored as percent string", "convert (/100)", int(is_pct.sum()),
    "verified per row: converted return = benchmark_return_ytd + active_alpha_ytd")


# --------------------------------------------------------------------------
# Step 9 - decision #8: flag the 1000.0 AUM placeholder
# --------------------------------------------------------------------------
banner("Step 9", "#8 Flag the 1000.0 AUM placeholder in snapshots and fees")
snap, fees = T["fact_portfolio_snapshot"], T["fact_fees"]
snap["flag_aum_placeholder"] = (num(snap["aum"]) == AUM_PLACEHOLDER).astype(int)
fees["flag_aum_placeholder"] = (num(fees["aum_basis"]) == AUM_PLACEHOLDER).astype(int)
# reconcile: every flagged fee must sit on a flagged snapshot for the same client-month
m = fees.merge(snap[["client_id", "snapshot_date", "flag_aum_placeholder"]],
               left_on=["client_id", "fee_date"], right_on=["client_id", "snapshot_date"],
               how="left", suffixes=("", "_snap"))
fee_on_flagged = (m.loc[m["flag_aum_placeholder"] == 1, "flag_aum_placeholder_snap"] == 1).mean()
check("snapshots flagged", 0, int(snap["flag_aum_placeholder"].sum()))
check("fees flagged", 0, int(fees["flag_aum_placeholder"].sum()))
check("flagged fees whose snapshot is also flagged", "", f"{fee_on_flagged:.2%}")
assert int(snap["flag_aum_placeholder"].sum()) == 420
assert int(fees["flag_aum_placeholder"].sum()) == 386
assert fee_on_flagged == 1.0, "a placeholder fee basis does not match a placeholder snapshot"
log("fact_portfolio_snapshot", "aum = 1000.0 placeholder", "flag", int(snap["flag_aum_placeholder"].sum()),
    "flag_aum_placeholder=1; value kept (true balance unknown)")
log("fact_fees", "aum_basis = 1000.0 placeholder", "flag", int(fees["flag_aum_placeholder"].sum()),
    "flag_aum_placeholder=1; 100% match a flagged snapshot for the same client-month")


# --------------------------------------------------------------------------
# Step 10 - decision #9: flag implausible returns
# --------------------------------------------------------------------------
banner("Step 10", "#9 Flag implausible YTD returns")
snap = T["fact_portfolio_snapshot"]
s = snap[["client_id", "year", "month"]].copy()
s["aum"] = num(snap["aum"])
s["ret"] = num(snap["return_ytd"])
dec = s[s["month"] == "12"].assign(year=lambda d: (d["year"].astype(int) + 1).astype(str))
dec = dec.set_index(["client_id", "year"])["aum"]
s["prior_dec_aum"] = pd.MultiIndex.from_frame(s[["client_id", "year"]]).map(lambda k: dec.get(k, np.nan))
# reconcile: return_ytd = aum / prior-December aum - 1 (shows what the column measures)
has_base = s["prior_dec_aum"].notna()
recon = ((s["aum"] / s["prior_dec_aum"] - 1 - s["ret"]).abs() <= 0.001)[has_base].mean()
print(f"  return_ytd = aum / prior-December aum - 1 holds on {recon:.2%} of {int(has_base.sum()):,} rows "
      f"with a prior December -> the column is a balance change incl. deposits")
flag = (s["ret"].abs() > 1) | (s["prior_dec_aum"] == AUM_PLACEHOLDER)
snap["flag_return_implausible"] = flag.astype(int)
check("rows with |return_ytd| > 1", int((s["ret"].abs() > 1).sum()), "flagged")
check("flag_return_implausible = 1", 0, int(flag.sum()))
assert recon > 0.999, "return_ytd no longer reconciles to AUM - check earlier steps"
assert snap.loc[s["ret"].abs() > 1, "flag_return_implausible"].eq(1).all()
log("fact_portfolio_snapshot", "return_ytd outside +/-100% or measured from the 1000.0 placeholder",
    "flag", int(flag.sum()),
    f"flag_return_implausible=1; values kept (they reconcile to AUM: {recon:.2%})")


# --------------------------------------------------------------------------
# Step 11 - final reconciliations and row-count proof
# --------------------------------------------------------------------------
banner("Step 11", "Final validation: reconciliations, referential integrity, raw = clean + quarantined")

# 11a. Fee amount recomputed from basis and bps
f = T["fact_fees"]
fee_ok = ((num(f["aum_basis"]) * num(f["bps_charged"]) / 10_000 / 12 - num(f["amount"])).abs() <= MONEY_TOL).mean()
check("fees: amount = aum_basis x bps / 10,000 / 12", "", f"{fee_ok:.2%}")
assert fee_ok == 1.0

# 11b. Two tables, one fact: fee aum_basis = snapshot aum for the same client-month
m = f.merge(T["fact_portfolio_snapshot"][["client_id", "snapshot_date", "aum"]],
            left_on=["client_id", "fee_date"], right_on=["client_id", "snapshot_date"], how="left")
basis_ok = ((num(m["aum_basis"]) - num(m["aum"])).abs() <= 0.01).mean()
check("fees: aum_basis = snapshot aum (same client, month)", "", f"{basis_ok:.2%}")
assert basis_ok == 1.0

# 11c. Advisor on each fact = client's advisor in dim_client
adv = T["dim_client"].set_index("client_id")["advisor_id"]
for t in ["fact_fees", "fact_portfolio_snapshot", "fact_transactions"]:
    ok = (T[t]["client_id"].map(adv) == T[t]["advisor_id"]).mean()
    check(f"{t}: advisor_id = dim_client.advisor_id", "", f"{ok:.2%}")
    assert ok == 1.0

# 11d. Referential integrity on every relationship (blank FKs excluded = cash transactions)
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
for child, col, parent, pcol in RELATIONSHIPS:
    keys = T[child][col]
    keys = keys[keys != ""]
    rate = keys.isin(set(T[parent][pcol])).mean()
    check(f"RI {child}.{col} -> {parent}.{pcol}", "", f"{rate:.2%}")
    assert rate == 1.0, f"orphans remain in {child}.{col}"

# 11e. Primary keys unique, same columns as raw (+ flags only)
for t, df in T.items():
    assert df[PRIMARY_KEYS[t]].is_unique, f"{t}: PK not unique"
    extra = [c for c in df.columns if c not in RAW_COLS[t]]
    assert list(df.columns[:len(RAW_COLS[t])]) == RAW_COLS[t], f"{t}: original columns changed"
    assert all(c.startswith("flag_") and set(df[c].unique()) <= {0, 1} for c in extra), f"{t}: bad extra column"

# 11f. Nothing deleted silently: raw rows = clean rows + quarantined rows
q_rows = {"dim_client": len(quarantines.get("dim_client_duplicates", [])),
          "fact_transactions": len(quarantines.get("fact_transactions_orphans", []))}
print()
print(f"  {'table':<26}{'raw':>10}{'clean':>10}{'quarantined':>13}  ok")
for t in TABLES:
    qn = q_rows.get(t, 0)
    ok = RAW_ROWS[t] == len(T[t]) + qn
    print(f"  {t:<26}{RAW_ROWS[t]:>10,}{len(T[t]):>10,}{qn:>13,}  {'yes' if ok else 'NO'}")
    assert ok, f"{t}: raw != clean + quarantined"

# 11g. Raw files untouched (re-read and compare row counts and a checksum)
for t in TABLES:
    again = pd.read_csv(RAW_DIR / f"{t}.csv", dtype=str, keep_default_na=False, encoding="utf-8-sig")
    assert again.equals(raw[t]), f"raw file {t} changed"
print("  raw files re-read: unchanged")


# --------------------------------------------------------------------------
# Save outputs (only reached if every assert passed)
# --------------------------------------------------------------------------
banner("Save", "Write clean tables, audit trail and README_clean.md")
CLEAN_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

for t, df in T.items():
    out = CLEAN_DIR / f"{t}{CLEAN_SUFFIX}.csv"
    df.to_csv(out, index=False)
    print(f"  {out.relative_to(HW_DIR)}  ({len(df):,} rows)")

pd.DataFrame(cleaning_log).to_csv(AUDIT_DIR / "cleaning_log.csv", index=False)
vc = pd.concat(value_changes, ignore_index=True) if value_changes else pd.DataFrame(
    columns=["table", "key_column", "key", "column", "old_value", "new_value"])
vc.to_csv(AUDIT_DIR / "value_changes.csv", index=False)
for name, q in quarantines.items():
    q.to_csv(AUDIT_DIR / f"quarantine_{name}.csv", index=False)
print(f"  data/audit/cleaning_log.csv ({len(cleaning_log)} steps)")
print(f"  data/audit/value_changes.csv ({len(vc):,} cell changes: "
      f"{vc.groupby(['table', 'column']).size().to_dict()})")
for name, q in quarantines.items():
    print(f"  data/audit/quarantine_{name}.csv ({len(q)} rows)")


# --------------------------------------------------------------------------
# README_clean.md (numbers come from this run, so they always match the files)
# --------------------------------------------------------------------------
n_flag = {
    "rp": int(T["dim_client"]["flag_risk_profile_inferred"].sum()),
    "aum_s": int(T["fact_portfolio_snapshot"]["flag_aum_placeholder"].sum()),
    "aum_f": int(T["fact_fees"]["flag_aum_placeholder"].sum()),
    "ret": int(T["fact_portfolio_snapshot"]["flag_return_implausible"].sum()),
}
blank_sec = int((T["fact_transactions"]["security_id"] == "").sum())
weekend = T["fact_transactions"].merge(T["dim_date"][["date", "is_trading_day"]], left_on="txn_date", right_on="date")
trades = weekend["txn_type"].isin({"Buy", "Sell"})
weekend_trades = int((trades & (weekend["is_trading_day"] == "0")).sum())
S = CLEAN_SUFFIX

readme = f"""# Wildcat Capital — clean tables

Produced by `scripts/02_clean.py` on {date.today().isoformat()} from the raw files in `Wildcat Capital/`
(which are never modified). Every decision is explained in `decision_log.md`; every changed cell is in
`data/audit/value_changes.csv`; every removed row is in a `data/audit/quarantine_*.csv` file.

## Files and row counts

| Clean file | Raw rows | Clean rows | Quarantined | Primary key | Grain |
|---|---:|---:|---:|---|---|
""" + "\n".join(
    f"| `{t}{S}.csv` | {RAW_ROWS[t]:,} | {len(T[t]):,} | {q_rows.get(t, 0):,} | `{PRIMARY_KEYS[t]}` | "
    + {"dim_advisor": "one advisor", "dim_benchmark": "one benchmark", "dim_client": "one client",
       "dim_date": "one calendar day 2020-2024", "dim_security": "one security / ETF",
       "fact_fees": "one fee charge per client per month",
       "fact_portfolio_snapshot": "one snapshot per client per month (dated the 1st)",
       "fact_transactions": "one transaction"}[t] + " |"
    for t in TABLES) + f"""

## Relationships (star schema)

All relationships are many-to-one from the fact (or child dimension) to the dimension, single direction.

| # | From (many) | To (one) | Notes |
|---|---|---|---|
| 1 | `dim_client.advisor_id` | `dim_advisor.advisor_id` | client's assigned advisor |
| 2 | `dim_security.benchmark_id` | `dim_benchmark.benchmark_id` | security's reference benchmark |
| 3 | `fact_fees.client_id` | `dim_client.client_id` | |
| 4 | `fact_fees.advisor_id` | `dim_advisor.advisor_id` | always equals the client's advisor |
| 5 | `fact_portfolio_snapshot.client_id` | `dim_client.client_id` | |
| 6 | `fact_portfolio_snapshot.advisor_id` | `dim_advisor.advisor_id` | always equals the client's advisor |
| 7 | `fact_portfolio_snapshot.benchmark_id` | `dim_benchmark.benchmark_id` | portfolio's benchmark (1, 3 or 8) |
| 8 | `fact_transactions.client_id` | `dim_client.client_id` | |
| 9 | `fact_transactions.advisor_id` | `dim_advisor.advisor_id` | always equals the client's advisor |
| 10 | `fact_transactions.security_id` | `dim_security.security_id` | **blank on {blank_sec:,} cash rows** (Deposit, Withdrawal, Advisory Fee) — allow blanks |
| 11 | `fact_fees.fee_date` | `dim_date.date` | join on the **date** column, not `date_id` |
| 12 | `fact_portfolio_snapshot.snapshot_date` | `dim_date.date` | join on the **date** column |
| 13 | `fact_transactions.txn_date` | `dim_date.date` | join on the **date** column |

`dim_date.date_id` is a surrogate key that no fact uses; mark `dim_date` as the date table using `date`.
The three advisor relationships (4, 6, 9) duplicate the path fact → client → advisor. In Power BI keep one
active path (e.g. fact → advisor) and set the others inactive to avoid ambiguous filter paths.
All 13 relationships match 100% on non-blank keys after cleaning.

## Column types for a BI tool

Load every file with explicit types — do **not** let the tool auto-detect.

| Column(s) | Type | Why |
|---|---|---|
| all `*_id` columns | Whole number | keys; `security_id` is blank on cash transactions |
| `dim_client.zip_code` | **Text** | keeps leading zeros (e.g. `03897`) |
| `dim_client.client_since`, `dim_date.date`, `fee_date`, `snapshot_date`, `txn_date` | Date (YYYY-MM-DD) | |
| `initial_aum`, `aum`, `aum_basis`, `amount`, `price` | Fixed decimal / currency | money |
| `shares` | Decimal (4 dp) | fractional shares |
| `bps_charged`, `expense_ratio_bps` | Decimal | basis points (1 bp = 0.01%); `expense_ratio_bps` blank for non-ETFs |
| `return_ytd`, `benchmark_return_ytd`, `active_alpha_ytd`, `cash_pct` | Decimal, **format as %** | stored as fractions (0.0928 = 9.28%) |
| `years_experience`, `aum_target_mm`, `age`, `year`, `quarter`, `month`, `week_of_year` | Whole number | `aum_target_mm` is in $ millions |
| `is_active`, `is_etf` | True/False | stored as the text `True`/`False` |
| `is_month_end`, `is_quarter_end`, `is_year_end`, `is_trading_day`, all `flag_*` | Whole number 0/1 (or True/False) | |
| `certifications` | **Text** | the value `None` means "no certification" — must not become null |
| `sector` | Text | blank for ETFs — label as "Fund (multi-sector)" in the report layer |
| other name/code columns | Text | |
| `dim_date.month_name`, `day_of_week` | Text, sort by `month` / weekday number | |

## Flag columns added (0/1)

| Table | Column | Rows = 1 | Meaning | How to use it |
|---|---|---:|---|---|
| `dim_client` | `flag_risk_profile_inferred` | {n_flag['rp']} | risk_profile was blank and was filled with "Balanced" because the client's portfolio uses benchmark 3, which only Balanced clients use | include normally; filter on it for a suitability review |
| `fact_portfolio_snapshot` | `flag_aum_placeholder` | {n_flag['aum_s']} | `aum` is exactly 1000.0 — a system default/floor, true balance unknown | exclude from AUM trend and average-balance visuals |
| `fact_fees` | `flag_aum_placeholder` | {n_flag['aum_f']} | fee calculated on the 1000.0 placeholder basis | exclude from fee-yield (fee ÷ AUM) analysis |
| `fact_portfolio_snapshot` | `flag_return_implausible` | {n_flag['ret']} | `return_ytd` beyond ±100% or measured from a 1000.0 placeholder base | exclude from average return / alpha visuals |

## Known limitations (left unfixed on purpose)

1. **`return_ytd` is a balance change, not investment performance.** It equals `aum ÷ prior-December aum − 1`, so deposits and withdrawals count as "return". Not fixable without a cash-flow-adjusted (time-weighted) method; implausible rows are flagged instead.
2. **The 1000.0 AUM values are kept.** The real balance is unknown and neighbouring months are usually 1000.0 too, so interpolation would invent data. Flagged.
3. **Transaction dates fall on weekends.** {weekend_trades:,} Buy/Sell rows are on non-trading days; the weekend share of all transactions is ≈2/7, so dates look like booking dates rather than trade dates. Moving them would invent dates; monthly totals are unaffected, daily analysis is not reliable.
4. **`dim_date` has no market holidays.** Every weekday is `is_trading_day = 1`; trading-day counts are ~4% high. Fixing needs an external exchange calendar.
5. **Holdings before 2020 are not in the extract.** Some Sells and Dividends refer to securities bought before 2020, so share counts rebuilt from 2020–2024 trades can go negative.
6. **Some snapshot months have no fee** (7,350 client-months). Likely waivers/minimums; not filled because no fee can be computed without inventing a rate.
7. **Monthly facts are dated the 1st**, so the onboarding month's snapshot/fee can be a few days before `client_since`. This is structure, not an error.
8. **Blank values that are correct and stay blank:** `security_id`/`shares`/`price` on cash transactions; `sector` on ETFs; `expense_ratio_bps` on non-ETFs.
9. **Duplicate security names** (3 names × 2 securities) are different instruments; label visuals by `ticker`.
10. **Risk profiles for {n_flag['rp']} clients are inferred**, not recorded. The inference is one-to-one in this data but is still an inference — see `flag_risk_profile_inferred`.

## Audit trail

| File | Contents |
|---|---|
| `data/audit/cleaning_log.csv` | one row per step: table, issue, action, rows affected, note |
| `data/audit/value_changes.csv` | one row per changed cell: table, key column, key, column, old value, new value ({len(vc):,} rows) |
""" + "\n".join(f"| `data/audit/quarantine_{n}.csv` | {len(q)} removed rows with every original column, "
                f"`quarantine_reason` and a trace key (`{q.columns[-1]}`) |" for n, q in quarantines.items()) + "\n"

(CLEAN_DIR / "README_clean.md").write_text(readme, encoding="utf-8")
print("  data/clean/README_clean.md")
print("\nAll steps passed. Raw files unchanged.")
