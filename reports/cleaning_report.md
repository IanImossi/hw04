# Wildcat Capital Data Cleaning Report

**To:** Portfolio Committee  **Re:** Quality of the 2020–2024 warehouse extract (8 tables)  **Sources:** `decision_log.md`, `data/audit/cleaning_log.csv`, `reports/data_quality_scorecard.csv`, `reports/reconciliations.csv`

## Executive summary

The extract (5 dimension tables, 3 fact tables) had never been checked. We audited every table and logged 25 issues. Of these, **7 were defects that we repaired or removed**. **2 were suspect values we cannot correct, so we kept them and flagged them.** The other **16 were left as they are**; most are legitimate structure, not errors. No raw file was modified. Every changed cell (198,462) and every removed row (225) is recorded in an audit file.

Three findings matter most for the committee:

- **$10.9M of transactions were counted twice.** 200 transactions were exact copies of real transactions, booked under client IDs that do not exist ($10,888,495.37). They have been quarantined.
- **No security-level analysis was possible.** In the raw data, 0% of transactions linked to a security, because IDs were stored as `45.0` instead of `45`. After cleaning, 100% link.
- **`return_ytd` is not investment performance.** It equals AUM ÷ prior-December AUM − 1 on 100% of 104,823 rows we could test, so deposits count as "return". 1,171 snapshot rows are now flagged as implausible and should be excluded from performance figures.

After cleaning, every table scores 100% on completeness, key uniqueness and type validity, and all 13 table relationships match 100%.

## Cleaning summary

| Issue | Table | Action | Rows | How the fix was verified |
|---|---|---|---:|---|
| Duplicate clients (last name off by one letter) | dim_client | Quarantine | 25 | 0 fact rows referenced the copies; survivor ID recorded |
| State written as full name | dim_client | Standardize to USPS code | 77 | 100% of states valid afterwards |
| Blank risk profile | dim_client | Fill "Balanced" + flag | 75 | Benchmark 3 is used only by Balanced clients (757 to 0) |
| Security ID stored as `45.0` | fact_transactions | Convert | 197,175 | Link to `dim_security`: 0.00% → 100.00% |
| Orphan transactions (copies with a bad client ID) | fact_transactions | Quarantine | 200 | All 200 have an exact twin under a valid client |
| Negative shares on Buys | fact_transactions | Absolute value | 835 | amount = shares × price: 99.576% → 100% |
| Returns stored as text (`9.28%`) | fact_portfolio_snapshot | ÷ 100 | 300 | Every row = benchmark return + alpha |
| AUM exactly $1,000.00 (placeholder) | snapshot / fees | Flag | 420 / 386 | Every flagged fee sits on a flagged snapshot |
| Return beyond ±100% or on a $1,000 base | fact_portfolio_snapshot | Flag | 1,171 | Values reconcile to AUM, so they are kept |

**Scorecard (raw → clean).** Only the tables that changed are shown; `dim_advisor`, `dim_benchmark`, `dim_date` and `dim_security` scored 100% on every measure in both stages.

| Table | Rows | Completeness | Key uniqueness | Type validity | Rule pass | Referential match |
|---|---|---|---|---|---|---|
| dim_client | 2,525 → 2,500 | 99.785% → 100% | 98.020% → 100% | 100% → 100% | 99.390% → 100% | 100% → 100% |
| fact_transactions | 298,772 → 298,572 | 100% → 100% | 99.866% → 100% | 90.545% → 100% | 99.947% → 100% | 81.95% → 100% |
| fact_portfolio_snapshot | 129,547 → 129,547 | 100% → 100% | 100% → 100% | 99.981% → 100% | 99.925% → 99.931% | 100% → 100% |
| fact_fees | 122,197 → 122,197 | 100% → 100% | 100% → 100% | 100% → 100% | 99.9999% → 99.9999% | 100% → 100% |

Rule pass stays below 100% in two tables on purpose: the 977 returns above +100% and the single 100.38 bps fee are kept and flagged, not changed.

Row counts reconcile for all 8 tables: raw = clean + quarantined (`dim_client` 2,525 = 2,500 + 25; `fact_transactions` 298,772 = 298,572 + 200).

## Key judgment calls

**1. Fill the 75 blank risk profiles instead of leaving them blank.**
- *Justification:* A risk profile is required to show that a portfolio is suitable for the client. In this data the portfolio benchmark identifies the profile: all 757 clients with a known profile on benchmark 3 are Balanced, and every one of the 75 blank-profile clients is on benchmark 3.
- *Risk:* A client placed on benchmark 3 by exception would get the wrong profile. The column `flag_risk_profile_inferred` marks all 75, so compliance can review or exclude them.

**2. Quarantine the 200 orphan transactions rather than keep them under an "unknown client".**
- *Justification:* These are not lost clients. Each one matches a valid transaction on all 7 other fields (advisor, security, date, type, shares, price, amount). Keeping them would double-count $10.9M of activity.
- *Risk:* If they were real transactions, activity would be understated by that amount. Given an exact twin for every row, this is unlikely, and all 200 are kept with their original `txn_id` in `quarantine_fact_transactions_orphans.csv`.

**3. Flag, but do not change, the $1,000 AUM values and extreme returns.**
- *Justification:* $1,000.00 appears 420 times in AUM; the next most common value appears twice. That looks like a system default, but the true balance is unknown, and the neighbouring months are usually $1,000 too, so interpolating would invent data. The extreme returns are arithmetically correct given AUM, so changing them would also invent data.
- *Risk:* If they are not excluded, average client return and alpha will be badly overstated. Committee visuals should filter on `flag_aum_placeholder = 0` and `flag_return_implausible = 0`.

**4. Leave 101,597 blank security/share/price cells blank (raw count; 101,528 after the orphan quarantine).**
- *Justification:* They occur exactly on Deposits (35,981), Advisory Fees (35,766) and Withdrawals (29,850). A cash movement has no security. Filling them with 0 would pull share and price averages down.
- *Risk:* None, provided the BI model allows a blank `security_id` (34% of transaction rows).

## Limitations (not fixed)

- **Returns include deposits.** `return_ytd` is a change in balance, not a time-weighted return. A true performance figure needs cash-flow data the extract does not provide.
- **Transactions do not explain AUM.** Only 123 of 797 month-over-month AUM jumps above 50% (15.4%) match that month's net deposits/withdrawals. The correlation between monthly AUM change and net cash flow is −0.029. AUM and transactions should not be combined in one analysis without further investigation.
- **Dates fall on weekends.** 28.4% of trades are on non-trading days, roughly 2/7, which suggests booking dates, not trade dates. Monthly totals are reliable; daily analysis is not.
- **No market holidays in the calendar.** Trading-day counts are about 4% too high.
- **Positions held before 2020 are missing.** 7,960 sells and 7,128 dividends refer to securities not bought within the extract, so holdings rebuilt from these trades can go negative.
- **7,350 client-months have a snapshot but no fee.** These could be waivers or minimum thresholds. We did not fill them, because that would require inventing a fee rate.

---

`AI-assisted report — reviewed for factual accuracy on 10/26 by Ian Imossi.`
