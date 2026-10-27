# HW04

## Structure
- HW04 is divided into 5 parts: Wildcat Capital, descriptions, scripts, data, and reports
    - Wildcat Capital is full of the raw datat that the scripts will use in their calcualtions
    - descriptions contains the descriptions put into Claude to create the scripts
    - scripts has the python programs used to profile, clean, and validate the data in Wildcat Capital
    - data has the cleaned data from 02_clean.py
    - reports contains the final audits with validation
## How to Run
- First go load the raw data into the "Wildcat Capital" folder
- Back out and enter the "scripts" folder
- Run 01_profile.py in order to scan all of the files in Wildcat Capital and to check for any potential issues within the files. The results will be saved to the "reports" folder under "dq_summary_raw" and "profile_raw".
- Run 02_clean.py to create help clean the data in "Wildcat Capital". The data cleaned will be stored in data\clean. Logs detailing the cleaning process and suspicious cases are stored in the "audit" folder of "data"
- Run 03_validate.py to validate the data in the previous step. It will create csv files in the "reports" folder detailing iqr outliers, reconciliations, ri results, rule results, and a data quality scorecard.


## Claude Code Findings
- Part A
    - Activity from inactive clients after their last snapshot
    - The 1000.0 AUM placeholder is flagged in each table separately but never linked across tables. 
    - No range check on active_alpha_ytd
- Part C
    - Found no problem with the code itself
    - Added defense for the step order
- Part D 
    - Cleared up what it meant to be an "orphan row" when dealing with DataFrame