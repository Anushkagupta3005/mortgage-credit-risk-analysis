"""
Session 1: Load Freddie Mac SFLLD sample files into DuckDB and sanity-check them.

Folder setup (run this script from the project folder):
    mortgage-risk/
    ├── load_data.py          <- this file
    └── data/
        └── raw/
            ├── sample_orig_2012.txt
            ├── sample_perf_2012.txt
            ├── ... (2016, 2019, 2021, 2023)

Install once:   pip3 install duckdb pandas
Run:            python3 load_data.py
"""

import glob
from pathlib import Path

import duckdb

RAW = Path("data/raw")
DB = "mortgage.duckdb"

# Column order from the Freddie Mac General User Guide (file layout section).
# The script checks the column count and the sanity checks below will catch
# a misalignment, so verify against the PDF if anything looks off.
ORIG_COLS = [
    "credit_score", "first_payment_date", "first_time_homebuyer", "maturity_date",
    "msa", "mi_pct", "num_units", "occupancy_status", "orig_cltv", "orig_dti",
    "orig_upb", "orig_ltv", "orig_interest_rate", "channel", "ppm_flag",
    "amortization_type", "property_state", "property_type", "postal_code",
    "loan_seq_no", "loan_purpose", "orig_loan_term", "num_borrowers",
    "seller_name", "super_conforming_flag", "pre_harp_loan_seq_no",
    "program_indicator", "harp_indicator", "property_valuation_method",
    "interest_only_indicator", "orig_unknown_31",
]

PERF_COLS = [
    "loan_seq_no", "reporting_period", "current_upb", "delinquency_status",
    "loan_age", "remaining_months", "defect_settlement_date", "modification_flag",
    "zero_balance_code", "zero_balance_date", "current_interest_rate",
    "non_interest_bearing_upb", "last_paid_installment_date", "mi_recoveries",
    "net_sale_proceeds", "non_mi_recoveries", "total_expenses", "legal_costs",
    "maintenance_costs", "taxes_insurance", "misc_expenses", "actual_loss",
    "cumulative_mod_cost", "step_mod_flag", "payment_deferral", "eltv",
    "zero_balance_removal_upb", "delinquent_accrued_interest",
    "delinquency_due_to_disaster", "borrower_assistance_status",
    "current_month_mod_cost", "interest_bearing_upb",
    "mi_cancellation_indicator", "servicer_name", "perf_unknown_amount_35",
]

con = duckdb.connect(DB)


def load(pattern: str, table: str, names: list[str]) -> None:
    files = sorted(glob.glob(str(RAW / pattern)))
    if not files:
        raise SystemExit(f"No files match {pattern} in {RAW}/ — check the folder.")
    print(f"\n[{table}] loading {len(files)} files...")

    # Read everything as text first; types get fixed in Session 2.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE tmp AS
        SELECT * FROM read_csv({files!r}, delim='|', header=false,
                               all_varchar=true, filename=true)
    """)

    raw_cols = [r[0] for r in con.execute("DESCRIBE tmp").fetchall() if r[0] != "filename"]
    if len(raw_cols) != len(names):
        print(f"  ⚠ file has {len(raw_cols)} columns, expected {len(names)}. "
              f"Extra columns are named extra_1, extra_2... — check the User Guide.")

    select = []
    for i, col in enumerate(raw_cols):
        new = names[i] if i < len(names) else f"extra_{i - len(names) + 1}"
        select.append(f'"{col}" AS {new}')
    select.append(r"regexp_extract(filename, 'sample_(?:orig|perf)_(\d{4})', 1) AS vintage")

    con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT {', '.join(select)} FROM tmp")
    con.execute("DROP TABLE tmp")
    n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"  ✓ {table}: {n:,} rows")


def show(title: str, sql: str) -> None:
    print(f"\n--- {title} ---")
    print(con.execute(sql).df().to_string(index=False))


load("sample_orig_*.txt", "orig", ORIG_COLS)
load("sample_perf_*.txt", "perf", PERF_COLS)

# ---------- Sanity checks: if these look wrong, columns are misaligned ----------

show("Loans per vintage (expect ~50,000 each)",
     "SELECT vintage, COUNT(*) AS loans FROM orig GROUP BY 1 ORDER BY 1")

show("Performance rows per vintage",
     """SELECT vintage, COUNT(*) AS rows, COUNT(DISTINCT loan_seq_no) AS loans,
               MIN(reporting_period) AS first_month, MAX(reporting_period) AS last_month
        FROM perf GROUP BY 1 ORDER BY 1""")

show("Credit score check (expect mostly 300-850; 9999 = missing)",
     """SELECT
          SUM(CASE WHEN TRY_CAST(credit_score AS INT) BETWEEN 300 AND 850 THEN 1 ELSE 0 END) AS valid,
          SUM(CASE WHEN TRY_CAST(credit_score AS INT) = 9999 THEN 1 ELSE 0 END) AS missing_9999,
          ROUND(AVG(TRY_CAST(credit_score AS INT)) FILTER (
                WHERE TRY_CAST(credit_score AS INT) BETWEEN 300 AND 850), 0) AS avg_fico
        FROM orig""")

show("Top states (expect 2-letter codes like CA, TX, FL)",
     "SELECT property_state, COUNT(*) AS loans FROM orig GROUP BY 1 ORDER BY 2 DESC LIMIT 5")

show("Loan IDs join between files (expect ~100% matched)",
     """SELECT ROUND(100.0 * COUNT(DISTINCT p.loan_seq_no) /
                   (SELECT COUNT(*) FROM orig), 1) AS pct_orig_loans_with_perf
        FROM perf p JOIN orig o USING (loan_seq_no)""")

show("Delinquency status values (0 = current, 1 = 30 days late, 2 = 60, 3+ = 90+)",
     """SELECT delinquency_status, COUNT(*) AS rows FROM perf
        GROUP BY 1 ORDER BY 2 DESC LIMIT 10""")

con.close()
print(f"\nDone. Database saved to {DB}")
