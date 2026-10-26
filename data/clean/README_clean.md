# Wildcat Capital — clean tables

Produced by `scripts/02_clean.py` on 2026-10-26 from the raw files in `Wildcat Capital/`
(which are never modified). Every decision is explained in `decision_log.md`; every changed cell is in
`data/audit/value_changes.csv`; every removed row is in a `data/audit/quarantine_*.csv` file.

## Files and row counts

| Clean file | Raw rows | Clean rows | Quarantined | Primary key | Grain |
|---|---:|---:|---:|---|---|
| `dim_advisor_clean.csv` | 25 | 25 | 0 | `advisor_id` | one advisor |
| `dim_benchmark_clean.csv` | 8 | 8 | 0 | `benchmark_id` | one benchmark |
| `dim_client_clean.csv` | 2,525 | 2,500 | 25 | `client_id` | one client |
| `dim_date_clean.csv` | 1,827 | 1,827 | 0 | `date_id` | one calendar day 2020-2024 |
| `dim_security_clean.csv` | 500 | 500 | 0 | `security_id` | one security / ETF |
| `fact_fees_clean.csv` | 122,197 | 122,197 | 0 | `fee_id` | one fee charge per client per month |
| `fact_portfolio_snapshot_clean.csv` | 129,547 | 129,547 | 0 | `snapshot_id` | one snapshot per client per month (dated the 1st) |
| `fact_transactions_clean.csv` | 298,772 | 298,572 | 200 | `txn_id` | one transaction |

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
| 10 | `fact_transactions.security_id` | `dim_security.security_id` | **blank on 101,528 cash rows** (Deposit, Withdrawal, Advisory Fee) — allow blanks |
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
| `dim_client` | `flag_risk_profile_inferred` | 75 | risk_profile was blank and was filled with "Balanced" because the client's portfolio uses benchmark 3, which only Balanced clients use | include normally; filter on it for a suitability review |
| `fact_portfolio_snapshot` | `flag_aum_placeholder` | 420 | `aum` is exactly 1000.0 — a system default/floor, true balance unknown | exclude from AUM trend and average-balance visuals |
| `fact_fees` | `flag_aum_placeholder` | 386 | fee calculated on the 1000.0 placeholder basis | exclude from fee-yield (fee ÷ AUM) analysis |
| `fact_portfolio_snapshot` | `flag_return_implausible` | 1171 | `return_ytd` beyond ±100% or measured from a 1000.0 placeholder base | exclude from average return / alpha visuals |

## Known limitations (left unfixed on purpose)

1. **`return_ytd` is a balance change, not investment performance.** It equals `aum ÷ prior-December aum − 1`, so deposits and withdrawals count as "return". Not fixable without a cash-flow-adjusted (time-weighted) method; implausible rows are flagged instead.
2. **The 1000.0 AUM values are kept.** The real balance is unknown and neighbouring months are usually 1000.0 too, so interpolation would invent data. Flagged.
3. **Transaction dates fall on weekends.** 40,630 Buy/Sell rows are on non-trading days; the weekend share of all transactions is ≈2/7, so dates look like booking dates rather than trade dates. Moving them would invent dates; monthly totals are unaffected, daily analysis is not reliable.
4. **`dim_date` has no market holidays.** Every weekday is `is_trading_day = 1`; trading-day counts are ~4% high. Fixing needs an external exchange calendar.
5. **Holdings before 2020 are not in the extract.** Some Sells and Dividends refer to securities bought before 2020, so share counts rebuilt from 2020–2024 trades can go negative.
6. **Some snapshot months have no fee** (7,350 client-months). Likely waivers/minimums; not filled because no fee can be computed without inventing a rate.
7. **Monthly facts are dated the 1st**, so the onboarding month's snapshot/fee can be a few days before `client_since`. This is structure, not an error.
8. **Blank values that are correct and stay blank:** `security_id`/`shares`/`price` on cash transactions; `sector` on ETFs; `expense_ratio_bps` on non-ETFs.
9. **Duplicate security names** (3 names × 2 securities) are different instruments; label visuals by `ticker`.
10. **Risk profiles for 75 clients are inferred**, not recorded. The inference is one-to-one in this data but is still an inference — see `flag_risk_profile_inferred`.

## Audit trail

| File | Contents |
|---|---|
| `data/audit/cleaning_log.csv` | one row per step: table, issue, action, rows affected, note |
| `data/audit/value_changes.csv` | one row per changed cell: table, key column, key, column, old value, new value (198,462 rows) |
| `data/audit/quarantine_dim_client_duplicates.csv` | 25 removed rows with every original column, `quarantine_reason` and a trace key (`duplicate_of_client_id`) |
| `data/audit/quarantine_fact_transactions_orphans.csv` | 200 removed rows with every original column, `quarantine_reason` and a trace key (`duplicate_of_txn_id`) |
