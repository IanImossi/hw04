# hw04

## Structure

## How to Run
- First go load the raw data into the "Wildcat Capital" folder
- Back out and enter the "scripts" folder
- Run 01_profile.py in order to scan all of the files in Wildcat Capital and to check for any potential issues within the files. The results will be saved to the "reports" folder under "dq_summary_raw" and "profile_raw".
- Run 02_clean to create help clean the data in "Wildcat Capital". The data cleaned will be stored in data\clean. Logs detailing the cleaning process and suspicious cases are stored in the "audit" folder of "data"

## Claude Code Findings
- Part A
    - Activity from inactive clients after their last snapshot
    - The 1000.0 AUM placeholder is flagged in each table separately but never linked across tables. 
    - No range check on active_alpha_ytd