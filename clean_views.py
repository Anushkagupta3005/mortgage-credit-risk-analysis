"""
Session 2, Step 2: Create typed + cleaned views on top of the raw VARCHAR tables.

  orig  (raw, all VARCHAR)  ->  orig_clean  (numbers, dates, missing codes -> NULL)
  perf  (raw, all VARCHAR)  ->  perf_clean  (numbers, dates, delinquency as integer months)

Why views (not tables)?
  - A view is just a saved SELECT. It takes no extra disk space and always
    reflects the raw data, so if we fix a cleaning rule we just re-run this file.
  - The heavy per-loan summary (next step) will be a real table.

Missing-value codes (Freddie Mac SFLLD User Guide):
  credit_score 9999 | orig_dti, orig_ltv, orig_cltv, mi_pct 999
  num_units, property_type, num_borrowers 99
  first_time_homebuyer, occupancy_status, channel, loan_purpose,
  program_indicator, property_valuation_method 9

Run:  python clean_views.py
"""

import duckdb

DB = "mortgage.duckdb"

# Helper SQL snippets ---------------------------------------------------------
# blank -> NULL, then strip a missing code, then cast safely (TRY_CAST never crashes)
def num(col, missing=None, typ="DOUBLE"):
    inner = f"NULLIF(TRIM({col}), '')"
    if missing is not None:
        inner = f"NULLIF({inner}, '{missing}')"
    return f"TRY_CAST({inner} AS {typ})"

def txt(col, missing=None):
    inner = f"NULLIF(TRIM({col}), '')"
    if missing is not None:
        inner = f"NULLIF({inner}, '{missing}')"
    return inner

def yyyymm(col):
    # '202003' -> DATE 2020-03-01
    return f"TRY_STRPTIME(NULLIF(TRIM({col}), '') || '01', '%Y%m%d')::DATE"


ORIG_CLEAN = f"""
CREATE OR REPLACE VIEW orig_clean AS
SELECT
    TRIM(loan_seq_no)                                   AS loan_seq_no,
    TRY_CAST(vintage AS INTEGER)                        AS vintage,

    -- borrower
    {num('credit_score', '9999', 'INTEGER')}            AS credit_score,
    {num('orig_dti', '999', 'INTEGER')}                 AS orig_dti,
    {txt('first_time_homebuyer', '9')}                  AS first_time_homebuyer,
    {num('num_borrowers', '99', 'INTEGER')}             AS num_borrowers,

    -- loan terms
    {num('orig_upb')}                                   AS orig_upb,
    {num('orig_interest_rate')}                         AS orig_interest_rate,
    {num('orig_loan_term', None, 'INTEGER')}            AS orig_loan_term,
    {num('orig_ltv', '999', 'INTEGER')}                 AS orig_ltv,
    {num('orig_cltv', '999', 'INTEGER')}                AS orig_cltv,
    {num('mi_pct', '999', 'INTEGER')}                   AS mi_pct,
    {txt('loan_purpose', '9')}                          AS loan_purpose,
    {txt('channel', '9')}                               AS channel,
    {txt('amortization_type')}                          AS amortization_type,
    {txt('ppm_flag')}                                   AS ppm_flag,
    {txt('interest_only_indicator')}                    AS interest_only_indicator,
    {txt('super_conforming_flag')}                      AS super_conforming_flag,
    {txt('program_indicator', '9')}                     AS program_indicator,
    {txt('harp_indicator')}                             AS harp_indicator,
    {txt('property_valuation_method', '9')}             AS property_valuation_method,

    -- dates
    {yyyymm('first_payment_date')}                      AS first_payment_date,
    {yyyymm('maturity_date')}                           AS maturity_date,

    -- property / geography
    {txt('occupancy_status', '9')}                      AS occupancy_status,
    {txt('property_type', '99')}                        AS property_type,
    {num('num_units', '99', 'INTEGER')}                 AS num_units,
    {txt('property_state')}                             AS property_state,
    {txt('msa')}                                        AS msa,
    {txt('postal_code')}                                AS postal_code,

    {txt('seller_name')}                                AS seller_name
FROM orig
"""

PERF_CLEAN = f"""
CREATE OR REPLACE VIEW perf_clean AS
SELECT
    TRIM(loan_seq_no)                                   AS loan_seq_no,
    TRY_CAST(vintage AS INTEGER)                        AS vintage,
    {yyyymm('reporting_period')}                        AS reporting_period,
    {num('loan_age', None, 'INTEGER')}                  AS loan_age,
    {num('remaining_months', None, 'INTEGER')}          AS remaining_months,

    -- balances & rate
    {num('current_upb')}                                AS current_upb,
    {num('interest_bearing_upb')}                       AS interest_bearing_upb,
    {num('non_interest_bearing_upb')}                   AS non_interest_bearing_upb,
    {num('current_interest_rate')}                      AS current_interest_rate,
    {num('eltv', '999', 'INTEGER')}                     AS eltv,

    -- delinquency: '00'->0, '01'->1 (30d), '03'->3 (90d) ... 'RA'->NULL
    {txt('delinquency_status')}                         AS dq_status_raw,
    TRY_CAST({txt('delinquency_status')} AS INTEGER)    AS dq_months,
    ({txt('delinquency_status')} = 'RA')                AS is_reo_acquisition,

    -- hardship / COVID-era signals
    {txt('borrower_assistance_status')}                 AS borrower_assistance_status,
    {txt('payment_deferral')}                           AS payment_deferral,
    {txt('delinquency_due_to_disaster')}                AS delinquency_due_to_disaster,
    {txt('modification_flag')}                          AS modification_flag,

    -- termination
    {txt('zero_balance_code')}                          AS zero_balance_code,
    {yyyymm('zero_balance_date')}                       AS zero_balance_date,
    {num('zero_balance_removal_upb')}                   AS zero_balance_removal_upb,
    {yyyymm('last_paid_installment_date')}              AS last_paid_installment_date,

    -- loss components (only filled on defaulted loans; 'C'/'U' codes become NULL)
    {num('actual_loss')}                                AS actual_loss,
    {num('net_sale_proceeds')}                          AS net_sale_proceeds,
    {num('mi_recoveries')}                              AS mi_recoveries,
    {num('non_mi_recoveries')}                          AS non_mi_recoveries,
    {num('total_expenses')}                             AS total_expenses,
    {num('delinquent_accrued_interest')}                AS delinquent_accrued_interest,

    {txt('servicer_name')}                              AS servicer_name
FROM perf
"""


def main():
    con = duckdb.connect(DB)
    con.execute(ORIG_CLEAN)
    con.execute(PERF_CLEAN)
    print("Created views: orig_clean, perf_clean\n")

    print("== orig_clean: row count + NULLs after cleaning ==")
    print(con.execute("""
        SELECT COUNT(*)                     AS loans,
               COUNT(*) - COUNT(credit_score) AS null_fico,
               COUNT(*) - COUNT(orig_dti)     AS null_dti,
               COUNT(*) - COUNT(orig_ltv)     AS null_ltv,
               ROUND(AVG(credit_score), 1)    AS avg_fico,
               ROUND(AVG(orig_dti), 1)        AS avg_dti,
               ROUND(AVG(orig_ltv), 1)        AS avg_ltv,
               MIN(first_payment_date)        AS min_first_pay,
               MAX(first_payment_date)        AS max_first_pay
        FROM orig_clean
    """).fetchdf().to_string(index=False))

    print("\n== orig_clean: FICO / LTV ranges (should be ~300-850 and ~1-200) ==")
    print(con.execute("""
        SELECT MIN(credit_score) AS min_fico, MAX(credit_score) AS max_fico,
               MIN(orig_ltv) AS min_ltv,      MAX(orig_ltv) AS max_ltv,
               MIN(orig_dti) AS min_dti,      MAX(orig_dti) AS max_dti
        FROM orig_clean
    """).fetchdf().to_string(index=False))

    print("\n== perf_clean: types parsed correctly? ==")
    print(con.execute("""
        SELECT COUNT(*)                                         AS rows,
               COUNT(*) - COUNT(reporting_period)               AS bad_dates,
               MIN(reporting_period)                            AS first_month,
               MAX(reporting_period)                            AS last_month,
               MAX(dq_months)                                   AS max_dq_months,
               SUM(CASE WHEN is_reo_acquisition THEN 1 END)     AS ra_rows,
               SUM(CASE WHEN dq_status_raw IS NULL THEN 1 END)  AS blank_dq_rows,
               SUM(CASE WHEN dq_months >= 3 THEN 1 END)         AS d90_rows
        FROM perf_clean
    """).fetchdf().to_string(index=False))

    print("\n== perf_clean: borrower_assistance_status (F = forbearance) ==")
    print(con.execute("""
        SELECT borrower_assistance_status, COUNT(*) AS n
        FROM perf_clean GROUP BY 1 ORDER BY n DESC
    """).fetchdf().to_string(index=False))

    con.close()


if __name__ == "__main__":
    main()
