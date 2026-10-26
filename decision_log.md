# HW4 Decision Log — Wildcat Capital
| # | Table / column | Issue | Decision | Type |
|---|---|---|---|---|
| 1 | dim_client (all columns) | 25 duplicate clients with truncated last names | quarantine | Defect |
| 2 | dim_client.state | 78 full state names instead of USPS codes | standardize | Defect |
| 3 | dim_client.risk_profile | 76 blanks | fill (75) + flag; 1 leaves with its quarantined duplicate | Defect |
| 4 | fact_transactions.security_id | Integer keys stored as `45.0` | convert | Defect |
| 5 | fact_transactions.client_id | 200 orphan rows that copy real transactions | quarantine | Defect |
| 6 | fact_transactions.shares | 836 Buy rows with negative shares | convert (sign) | Defect |
| 7 | fact_portfolio_snapshot.return_ytd | 300 values stored as `9.28%` text | convert (÷100) | Defect |
| 8 | fact_portfolio_snapshot.aum / fact_fees.aum_basis | `1000.0` placeholder (420 + 386 rows) | flag | Suspect, not fixable |
| 9 | fact_portfolio_snapshot.return_ytd | 977 returns outside ±100% | flag | Suspect, value reconciles |
| 10 | fact_portfolio_snapshot.aum | 797 month-over-month jumps over 50% | leave as is | Suspect, value reconciles |
| 11 | fact_transactions.txn_date | 40,649 trades on non-trading days | leave as is (documented limitation) | System property |
| 12 | fact_transactions.security_id | Sells/dividends on securities not bought in the extract | leave as is | Legitimate |
| 13 | fact_transactions (security_id, shares, price) | 101,597 blanks on cash transactions | leave as is | Legitimate structure |
| 14 | dim_security.sector / expense_ratio_bps | 219 / 281 blanks | leave as is | Legitimate structure |
| 15 | dim_advisor.certifications | 6 values of the word `None` | leave as is | Legitimate value |
| 16 | fact_portfolio_snapshot / fact_fees dates | 930 / 858 rows dated before client_since in the same month | leave as is | Legitimate structure |
| 17 | fact_portfolio_snapshot | 7,350 snapshot months with no fee | leave as is | Legitimate (probably) |
| 18 | fact_portfolio_snapshot.active_alpha_ytd | 24,543 IQR outliers | leave as is | Statistical flag, not a defect |
| 19 | fact_fees.bps_charged | 1 row at 100.38 bps | leave as is | Plausible |
| 20 | fact_transactions | 1,064 rows that differ from another row only in amount or date | leave as is | Legitimate |
| 21 | fact_fees | 2 rows that differ only in fee_date | leave as is | Legitimate |
| 22 | dim_security.name | 3 names shared by 2 securities each | leave as is | Legitimate |
| 23 | dim_benchmark | 5 of 8 benchmarks unused by snapshots | leave as is | Legitimate |
| 24 | Several decimal columns | Values written as `426254.0` | leave as is (set type on load) | Formatting only |
| 25 | dim_date.is_trading_day | No market holidays modelled | leave as is (documented limitation) | Limitation |

Decisions 1–7 change or remove data. Decisions 8–9 add flag columns. Decisions 10–25 change nothing but are logged so the reasoning is on record.

---

## A. Defects that will be repaired or removed

### 1. Duplicate clients (IDs 2501–2525)

| Field | Entry |
|---|---|
| **Table / column** | `dim_client` — whole row (`client_id`, `last_name`) |
| **Evidence** | F026 / F062: 50 rows form 25 pairs. Within each pair, all 12 columns other than `client_id` and `last_name` are identical, and the last name is cut short: client 2501 "Karina **Taylo**" = client 674 "Karina **Taylor**" (same age 45, Port Patrick, ZIP 73097, advisor 1, initial AUM 117,194.33). F047: all 25 of IDs 2501–2525 have **0 rows** in any fact table. |
| **Dimension violated** | Uniqueness, Accuracy |
| **Decision** | **quarantine** IDs 2501–2525 → `data/audit/quarantine_dim_client_duplicates.csv`, with a column naming the surviving `client_id` |
| **Justification** | One real person stored twice inflates the client count (2,525 instead of 2,500) and splits any "clients per advisor / tier / state" measure. The truncated copies carry no facts, so removing them loses no fees, trades or AUM. The higher, original ID keeps the full name and all the history. |
| **Risk** | If they are really different people (e.g. twins with the same first name, age and ZIP), we would under-count clients by 25. This is unlikely: every attribute down to `initial_aum` to the cent matches. Because nothing references these IDs, the quarantine cannot orphan a fact row. That will be asserted after cleaning. |

### 2. State stored as full names

| Field | Entry |
|---|---|
| **Table / column** | `dim_client.state` |
| **Evidence** | F005: 78 values not in the USPS code list — Pennsylvania 25, Florida 15, New York 14, New Jersey 13, California 5, Illinois 4, Texas 2. The other 2,447 rows use two-letter codes. |
| **Dimension violated** | Validity, Consistency |
| **Decision** | **standardize** to the two-letter code (Pennsylvania→PA, Florida→FL, New York→NY, New Jersey→NJ, California→CA, Illinois→IL, Texas→TX) |
| **Justification** | A map or slicer by state would show "PA" and "Pennsylvania" as two separate states, so state-level AUM would be split. The mapping is one-to-one and fixed, so nothing is invented. |
| **Risk** | Very low. Each of the 7 names has exactly one code. After cleaning, the script asserts that 100% of states are in the code list and that the per-state counts equal the old code count + the old full-name count. |

### 3. Blank risk profile

| Field | Entry |
|---|---|
| **Table / column** | `dim_client.risk_profile` |
| **Evidence** | F004: 76 blanks (Retail 49, HNW 18, UHNW 9). F063, cross-checked against `fact_portfolio_snapshot.benchmark_id`: benchmark 1 is used by Aggressive (242) and Moderately Aggressive (459) clients; **benchmark 3 is used only by Balanced clients (757, and no other profile)**; benchmark 8 by Conservative (466) and Moderately Conservative (501). **75 of the 76 blank-profile clients use benchmark 3 and no other.** The 76th, client 2510, has no snapshots and is one of the duplicates quarantined in #1. |
| **Dimension violated** | Completeness |
| **Decision** | **fill** the 75 with "Balanced" and add the flag `flag_risk_profile_inferred` = 1. Client 2510 leaves with the duplicate quarantine. |
| **Justification** | A risk profile is a required suitability attribute; the firm cannot evidence that a portfolio fits a client without one. The fill is not a guess: in this data, the benchmark a client's portfolio is measured against identifies the profile one-to-one for benchmark 3. The flag keeps the fill visible to compliance. |
| **Risk** | If benchmark assignment does not follow profile for these clients (e.g. a Moderately Aggressive client put on the 60/40 blend by exception), we would misstate their profile. Mitigations: the flag column lets anyone exclude or review the 75 inferred rows, and every change is written to `value_changes.csv`. *Alternative considered:* leave blank and flag. Rejected because the evidence (757 to 0) is strong and a blank profile breaks every "by risk profile" visual. |

### 4. `security_id` stored as a float (`45.0`)

| Field | Entry |
|---|---|
| **Table / column** | `fact_transactions.security_id` |
| **Evidence** | F020: all 197,175 non-blank values look like `45.0`. F058: with raw text, the match rate to `dim_security.security_id` is **0.00%**. With `.0` removed it is **100.00%**, and all 500 securities are referenced. The trailing `.0` comes from the column being saved as float because it contains blanks. |
| **Dimension violated** | Validity, Referential Integrity |
| **Decision** | **convert**: strip the trailing `.0` and store as a whole-number key; blanks stay blank |
| **Justification** | Every security-level analysis (holdings by sector, asset class, ETF vs. stock) depends on this join. The conversion only changes the format; the number is the same. |
| **Risk** | If a value were truly fractional (e.g. `45.5`), stripping would corrupt it. None are: 100% of non-blank values match `^\d+\.0$`, which will be asserted before the change. The script also asserts a 100% match rate afterwards. |

### 5. Orphan transactions (client IDs 2702–3192)

| Field | Entry |
|---|---|
| **Table / column** | `fact_transactions.client_id` |
| **Evidence** | F057: 200 rows (200 distinct `client_id`s, range 2702–3192) have no client in `dim_client`. They are the last 200 rows of the file (`txn_id` 298573–298772). F068 / F040: **all 200** are exact copies of an existing valid transaction (same advisor, security, date, type, shares, price, amount); only `client_id` differs. Example: `txn_id` 298581 (client 3071) duplicates `txn_id` 234232 (client 1958, Withdrawal $91,140.30 on 2023-02-14). Their total amount is $10,888,495.37. |
| **Dimension violated** | Referential Integrity, Uniqueness |
| **Decision** | **quarantine** → `data/audit/quarantine_fact_transactions_orphans.csv`, with the `txn_id` of the original row each one copies |
| **Justification** | These are not missing clients. They are duplicate transactions with a corrupted key. If they were kept under an "Unknown client" member, $10.9M of activity would be counted twice in firm totals. |
| **Risk** | If they were real transactions for clients missing from the dimension, removing them would under-state activity. An exact twin on all 7 other fields for every one of the 200 rows makes that very unlikely. The quarantine file keeps them traceable, and the script asserts raw = clean + quarantined. |

### 6. Negative shares on Buy transactions

| Field | Entry |
|---|---|
| **Table / column** | `fact_transactions.shares` (and the `amount = shares × price` rule) |
| **Evidence** | F039: 836 rows with negative shares, **all `Buy`**. F038: the same 836 rows fail `amount = shares × price`. **836 of 836 reconcile to within $0.05 when the sign of shares is flipped.** `amount` and `price` are positive on every row (F021 shows the range check). These Buys total $52,023,499.48. |
| **Dimension violated** | Validity (sign rule), Consistency |
| **Decision** | **convert**: `shares = abs(shares)` for these 836 rows |
| **Justification** | In this table, direction comes from `txn_type`, and quantities are stored as positive magnitudes. A negative Buy would make net-shares-held calculations subtract purchases. The repair is confirmed by `amount` and `price` on the same row, not assumed. |
| **Risk** | If the negative sign meant something (e.g. a cancelled or reversed buy), the reversal would be lost. Nothing supports that reading: the type is Buy, the amount is positive, and there is no reversal type in the code list. Each change goes to `value_changes.csv`. The script asserts 0 negative shares and 100% `shares × price` reconciliation for security transactions afterwards. |

### 7. Returns stored as percent text

| Field | Entry |
|---|---|
| **Table / column** | `fact_portfolio_snapshot.return_ytd` |
| **Evidence** | F014 / F015 / F035: 300 of 129,547 values are strings like `9.28%`; the rest are fractions (`0.019504`). F037: **300 of 300** satisfy `return_ytd/100 − benchmark_return_ytd = active_alpha_ytd` within rounding. Example: snapshot 42: 9.28% → 0.0928; benchmark 0.08279 + alpha 0.009966 = 0.092756. A second check: across the file, `return_ytd = aum ÷ prior-December aum − 1` (see #9), and the converted values agree with that. |
| **Dimension violated** | Validity, Consistency |
| **Decision** | **convert**: strip `%` and divide by 100 |
| **Justification** | A numeric column with text in it either fails to load or loads as text, and then average return cannot be computed. If the `%` were only stripped without dividing, those 300 returns would be 100× too large. |
| **Risk** | If any `%` value were already a fraction with a stray `%` sign, dividing would make it 100× too small. The alpha reconciliation rules this out row by row. The script asserts it again after conversion. |

---

## B. Suspect values that cannot be corrected without inventing data → flag

### 8. AUM placeholder value 1000.0

| Field | Entry |
|---|---|
| **Table / column** | `fact_portfolio_snapshot.aum`; `fact_fees.aum_basis` |
| **Evidence** | F017: `aum = 1000.0` appears 420 times (92 clients); the next most common value appears twice. By year: 2020 15, 2021 38, 2022 183, 2023 69, 2024 115. They come in runs: the median AUM of the month before and the month after is also 1000.0. F012: `fee.aum_basis = 1000.0` on 386 rows, and **100% of those match a snapshot with aum 1000.0**. Those fees total only $282.85. |
| **Dimension violated** | Accuracy/Plausibility |
| **Decision** | **flag**: add `flag_aum_placeholder` (0/1) to both tables; keep the value |
| **Justification** | Exactly $1,000.00 in hundreds of monthly snapshots is a system default or minimum-balance floor, not a measured balance. The true balance is unknown. Interpolating between months would invent numbers, and the neighbouring months are usually 1000.0 too. Fees are internally consistent with the 1000 basis (`amount` reconciles), so the rows stay and are flagged. |
| **Risk** | If these are real $1,000 balances (accounts drawn down to a minimum), flagging them only adds a column and changes nothing. If they are placeholders and we did not flag them, AUM trends and fee-yield analyses for 92 clients would be distorted. |

### 9. Year-to-date returns beyond ±100%

| Field | Entry |
|---|---|
| **Table / column** | `fact_portfolio_snapshot.return_ytd` (and `active_alpha_ytd`, which inherits it) |
| **Evidence** | F016: 977 rows (144 clients) outside [−1, +1]; the maximum is 94.67 (= +9,467%; client 123), and there is one 1170.15% string (snapshot 26403). Verification: on the 104,823 rows that have a prior-December snapshot, **`return_ytd = aum ÷ prior-December aum − 1`** (ratio mean 1.000000, range 0.99993–1.0015). So the stored return is a simple change in balance that **includes deposits and withdrawals**; it is not an investment return. **180 of the 977** extreme rows have a prior-December AUM of exactly 1000.0 (#8). Client 123's 2024 AUM of $43,836 over a $1,000 base gives +4,284%. 67% of the extreme rows are accounts under $100k, where one deposit moves the ratio a lot. |
| **Dimension violated** | Accuracy/Plausibility |
| **Decision** | **flag**: add `flag_return_implausible` = 1 where `abs(return_ytd) > 1` or the prior-December AUM is the 1000.0 placeholder. Keep the values. |
| **Justification** | The numbers are arithmetically correct given the AUM, so "correcting" them would invent data. They are not investment performance, though, and a committee averaging `return_ytd` would get an average skewed by a few small accounts. The flag lets Power BI exclude them from performance visuals. |
| **Risk** | If unflagged, average client return and "alpha vs. benchmark" would be badly overstated. If the rule is too broad, a few genuine large gains get excluded from performance averages; they stay in the data and can be included again. |

---

## C. Checked and deliberately left as is

### 10. Large month-over-month AUM moves

| Field | Entry |
|---|---|
| **Table / column** | `fact_portfolio_snapshot.aum` |
| **Evidence** | F036: 797 month-to-month changes over 50%. Many are the 1000.0 runs starting or ending (#8); the rest are small accounts. `return_ytd` reconciles to `aum` on every row (#9), so the AUM series agrees with itself. |
| **Dimension violated** | Accuracy/Plausibility (suspected only) |
| **Decision** | **leave as is**; the overlap with the 1000.0 placeholder is already covered by `flag_aum_placeholder` |
| **Justification** | A large deposit or withdrawal legitimately moves AUM by more than 50%. There is no second source for the true balance. |
| **Risk** | Some jumps may be errors we cannot see. Part D will compare them with deposit/withdrawal amounts in `fact_transactions` as an extra reconciliation. |

### 11. Trades on non-trading days

| Field | Entry |
|---|---|
| **Table / column** | `fact_transactions.txn_date` vs. `dim_date.is_trading_day` |
| **Evidence** | F069: 40,649 of 143,311 Buy/Sell rows (28.4%) fall on weekends. The weekend share of *all* transactions is 28.4%, almost exactly 2/7 = 28.6%, so dates are spread evenly across the week for every transaction type. |
| **Dimension violated** | Accuracy/Plausibility |
| **Decision** | **leave as is**; record as a known limitation in `README_clean.md` |
| **Justification** | This is a property of how the whole extract was produced (probably a booking date, not the trade date), not a defect in particular rows. Moving the dates to the next trading day would invent a date, and it would also change which month some transactions fall in. |
| **Risk** | Daily or trading-day analyses would be misleading; monthly and yearly totals are unaffected. A flag on 28% of trades would add noise without telling anyone what the real date is. |

### 12. Sells and dividends on securities never bought in the extract

| Field | Entry |
|---|---|
| **Table / column** | `fact_transactions.security_id` |
| **Evidence** | F070: 7,960 Sells; F071: 7,128 Dividends on a (client, security) pair with no Buy in 2020–2024. Clients have `client_since` dates going back to 2015, which is before the extract starts. |
| **Dimension violated** | Consistency (suspected) |
| **Decision** | **leave as is** |
| **Justification** | Positions bought before 2020 can be sold or pay dividends inside the window. Nothing marks these rows as wrong. |
| **Risk** | Position-level share counts built only from 2020–2024 trades will go negative for these securities. This will be documented as a limitation. |

### 13. Blank security, shares and price on cash transactions — legitimate structure

| Field | Entry |
|---|---|
| **Table / column** | `fact_transactions.security_id`, `shares`, `price` |
| **Evidence** | F022–F024 / F060: 101,597 blanks in each column. F043: the blank rows are **exactly** Deposit 35,981 + Advisory Fee 35,766 + Withdrawal 29,850 = 101,597. All 197,175 Buy/Sell/Dividend rows have all three fields. |
| **Dimension violated** | None. Blank is the correct value here. |
| **Decision** | **leave as is** (do not fill; do not treat as an RI failure) |
| **Justification** | A cash deposit has no security, share count or price. Filling with 0 would pull share-weighted averages towards zero, and an "Unknown security" member would look like a data problem when there isn't one. |
| **Risk** | The BI model must allow a blank `security_id` (null-key rate 34.0%). Part D reports the RI match rate on non-blank keys only. |

### 14. Blank sector and expense ratio in `dim_security` — legitimate structure

| Field | Entry |
|---|---|
| **Table / column** | `dim_security.sector`, `dim_security.expense_ratio_bps` |
| **Evidence** | F030 / F031: all 219 blank sectors are ETFs (`is_etf = True`), and all 281 blank expense ratios are non-ETFs. The relationship is exactly 1:1 with `is_etf`; there are 0 exceptions in either direction. |
| **Dimension violated** | None |
| **Decision** | **leave as is** |
| **Justification** | Funds hold many sectors, so they have no single sector. Single stocks and bonds have no expense ratio. Filling either column would create false facts. |
| **Risk** | Visuals by sector will show a blank bucket for ETFs; the README will suggest labelling it "Fund (multi-sector)" in the BI layer, not in the data. |

### 15. `certifications = "None"`

| Field | Entry |
|---|---|
| **Table / column** | `dim_advisor.certifications` |
| **Evidence** | F001: 6 of 25 advisors have the word `None`. The other values are CFA, CFP, "CFP, CFA" and "CFP, CIMA". |
| **Dimension violated** | None. It is a real value meaning "no professional certification". |
| **Decision** | **leave as is** |
| **Justification** | This is the reason the assignment requires `keep_default_na=False`: default pandas parsing would turn these into missing values and "advisors without a certification" would disappear from counts. |
| **Risk** | A careless downstream load could still turn it into a null. The README will tell BI users to load the column as text. |

### 16. Monthly rows dated before `client_since` in the same month

| Field | Entry |
|---|---|
| **Table / column** | `fact_portfolio_snapshot.snapshot_date` (930 rows), `fact_fees.fee_date` (858 rows) |
| **Evidence** | F067 / F065: these rows are dated the 1st of the month in which the client joined (e.g. client 2: snapshot 2020-11-01, `client_since` 2020-11-18). **0 rows** fall in a month before `client_since`, and 0 transactions predate `client_since`. |
| **Dimension violated** | None |
| **Decision** | **leave as is** |
| **Justification** | Monthly facts are stamped on the first day of the month, so the onboarding month's row always looks a few days early. |
| **Risk** | None, as long as nobody compares month-stamped facts with day-level dates. This will be noted in the README. |

### 17. Snapshot months with no fee

| Field | Entry |
|---|---|
| **Table / column** | `fact_portfolio_snapshot` vs. `fact_fees` |
| **Evidence** | F066: 7,350 client-months have a snapshot but no fee. Every fee row has a matching snapshot, and `aum_basis` equals that snapshot's `aum` on 100% of rows. |
| **Dimension violated** | Completeness (suspected only) |
| **Decision** | **leave as is** |
| **Justification** | Fee waivers, onboarding months and minimum-fee thresholds are normal business. The fee table reconciles perfectly where it does exist. |
| **Risk** | Fee revenue may be understated if fees are genuinely missing; the README will list this as a limitation. |

### 18. Alpha outliers by IQR

| Field | Entry |
|---|---|
| **Table / column** | `fact_portfolio_snapshot.active_alpha_ytd` |
| **Evidence** | F019: 24,543 rows outside 3×IQR (fences −0.113 to +0.121). Every row reconciles exactly to `return_ytd − benchmark_return_ytd`. |
| **Dimension violated** | None on its own |
| **Decision** | **leave as is** |
| **Justification** | The interquartile range of alpha is very narrow, so ordinary outperformance gets flagged. Being flagged means "unusual", not "wrong". The truly implausible values are caught by #9. |
| **Risk** | None. |

### 19. One fee above 100 bps

| Field | Entry |
|---|---|
| **Table / column** | `fact_fees.bps_charged` |
| **Evidence** | F011: fee 68809 = 100.38 bps (Management). All other fees are 25.56–99.88 bps, and `amount` reconciles to `aum_basis × bps ÷ 10,000 ÷ 12` on 100% of rows. |
| **Dimension violated** | Accuracy/Plausibility (weak) |
| **Decision** | **leave as is** |
| **Justification** | 100.38 bps is 0.38 bps above a soft rule of thumb, not an impossible value, and the charged amount agrees with it. |
| **Risk** | Negligible ($90.68 fee). |

### 20. Transactions that differ only in amount or date

| Field | Entry |
|---|---|
| **Table / column** | `fact_transactions` |
| **Evidence** | F042: 1,050 rows match another row in every column except `amount` (e.g. client 1 made two deposits on 2024-09-25, of $94,893.52 and $26,587.27). F041: 14 rows match except `txn_date` (e.g. the same $306.32 advisory fee in 2020 and 2022). |
| **Dimension violated** | None |
| **Decision** | **leave as is** |
| **Justification** | Two deposits on the same day, or the same fixed fee charged in different periods, are normal events. Different amounts or dates mean these are not double entries. |
| **Risk** | If any are genuine double-bookings, cash flows are slightly overstated; there is no field that could prove it either way. |

### 21. Fees that differ only in date

| Field | Entry |
|---|---|
| **Table / column** | `fact_fees` |
| **Evidence** | F034: fees 57446 (2020-06-01) and 57490 (2024-04-01) for client 1169 are identical except for the date. Both are on the 1000.0 placeholder basis, so their amount is the same ($0.71). |
| **Dimension violated** | None |
| **Decision** | **leave as is** (the 1000.0 basis is flagged by #8) |
| **Justification** | Different months mean different charges. They match only because the placeholder basis is the same. |
| **Risk** | None. |

### 22. Duplicate security names

| Field | Entry |
|---|---|
| **Table / column** | `dim_security.name` |
| **Evidence** | F032: 3 names are each shared by 2 securities ("Jones Inc Fund", "Davis PLC Fund", "Graham Group Inc."). Each pair has different tickers, IDs, and asset classes or sectors. `ticker` is 100% unique. |
| **Dimension violated** | None |
| **Decision** | **leave as is** |
| **Justification** | Different instruments from the same issuer (e.g. an alternatives fund and a bond fund) can share a name. The ticker is the business key. |
| **Risk** | A visual labelled by name alone would merge them. The README will recommend using the ticker as the label. |

### 23. Unused benchmarks

| Field | Entry |
|---|---|
| **Table / column** | `dim_benchmark.benchmark_id` |
| **Evidence** | F045: client snapshots use only benchmarks 1, 3 and 8. Securities use 1, 2, 3, 4, 5 and 7. Benchmark 6 is used by neither. |
| **Dimension violated** | None |
| **Decision** | **leave as is** |
| **Justification** | Dimensions can contain members with no facts, and benchmark 6 (EEM) may be used in future. |
| **Risk** | None. |

### 24. Whole numbers written as `426254.0` in decimal columns

| Field | Entry |
|---|---|
| **Table / column** | `dim_client.initial_aum`, `fact_fees.aum_basis` / `bps_charged` / `amount`, `fact_portfolio_snapshot.aum`, `fact_transactions.shares` / `price` / `amount`, `dim_security.expense_ratio_bps` |
| **Evidence** | `profile_raw.csv`: these values parse as valid decimals (e.g. 28 in `initial_aum`, 2,978 in `fact_transactions.amount`). They are float-export artefacts with the same value. |
| **Dimension violated** | None (only a format difference) |
| **Decision** | **leave as is**; the clean script converts these columns to numeric on purpose, which removes the difference |
| **Justification** | `426254.0` and `426254` are the same amount, so rewriting the text would add about 10,900 meaningless entries to `value_changes.csv`. This is not the same as #4: there the column is a **key**, and text matching breaks. |
| **Risk** | None once the column is typed as numeric. |

### 25. No market holidays in `dim_date`

| Field | Entry |
|---|---|
| **Table / column** | `dim_date.is_trading_day` |
| **Evidence** | F028: 0 weekdays are marked non-trading; a real NYSE calendar has about 9–10 holidays a year. All other derived columns (year, quarter, month, names, ISO week, month/quarter/year end) recompute exactly from `date`; there are 0 mismatches and 0 calendar gaps (F029). |
| **Dimension violated** | Accuracy (minor) |
| **Decision** | **leave as is**; document as a limitation |
| **Justification** | Adding holidays would mean importing an outside calendar the assignment doesn't provide. Monthly analysis is unaffected. |
| **Risk** | Trading-day counts are about 4% too high. |

---

## Checks that passed (no action)

- Primary keys are unique in all 8 tables (F002, F003, F006, F007, F010, F013, F018, F025).
- 11 of 13 relationships match 100%; the two failures are #4 and #5 (F046–F061).
- The advisor on every fee, snapshot and transaction equals the client's advisor in `dim_client` (0 mismatches).
- Snapshot `account_type` = client `primary_account_type` (0 mismatches).
- Fee `amount = aum_basis × bps ÷ 10,000 ÷ 12` holds on 100% of rows, and `aum_basis` = snapshot `aum` on 100% of rows.
- The benchmark return is identical across all clients for each benchmark and date.
- Client tier agrees with initial AUM bands (Retail < $500k, HNW $500k–$5M, UHNW ≥ $5M): 0 contradictions (F027).
- No inactive client has transactions in the last 6 months of the extract (F064).
- No exact duplicate rows in any table.

## Cleaning order for `02_clean.py`

1. **Quarantine duplicate clients (#1)** first, so later counts and fills apply only to real clients. This also takes client 2510 out of the risk-profile fill.
2. **Standardize state (#2)**, then **fill risk_profile (#3)**. Dimension attributes are repaired before facts are checked against them.
3. **Convert `security_id` (#4)**: keys are repaired before any referential-integrity step.
4. **Quarantine orphan transactions (#5)**, only after #1, so that we are sure the quarantine of 2501–2525 orphaned no facts.
5. **Fix share signs (#6)**, then **convert `%` returns (#7)**. Each is verified against `amount` and alpha.
6. **Add flags (#8, #9)** last, so they are computed on the cleaned values.

After every step: print rows affected, `assert` the after-state, and append to `cleaning_log.csv`. At the end, assert raw rows = clean rows + quarantined rows for every table: `dim_client` 2,525 = 2,500 + 25; `fact_transactions` 298,772 = 298,572 + 200; the other six tables keep all their rows.
