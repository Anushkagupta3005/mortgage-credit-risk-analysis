"""
Session 2, Step 3: Build loan_summary, one row per loan.

  orig_clean (1 row per loan)  +  perf_clean (1 row per loan per month)
                    |                          |
                    |        GROUP BY loan_seq_no (collapse monthly history)
                    v                          v
                       loan_summary  (250,000 rows)

NULL trap (fixed): BOOL_OR over all-NULL values returns NULL, not FALSE,
and AVG() silently drops NULLs from the denominator. Every flag is wrapped
in COALESCE(..., FALSE) so clean loans count as FALSE, not "missing".

Key flags:
  ever_d90       : loan was ever 90+ days delinquent (dq_months >= 3),
                   OR reached REO ('RA'), OR terminated as a default (02/03/09).
                   This is our main "bad loan" definition (default proxy).
  d90_within_24m : same, but only counting the first 24 months of loan age.
                   Fair comparison across vintages, because every vintage
                   has at least ~24 months of history (2023 loans are young).
  outcome        : default / prepaid / sold / removed / active
                   (from zero_balance_code)

Run:  python loan_summary.py
"""

import duckdb

DB = "mortgage.duckdb"

BUILD = """
CREATE OR REPLACE TABLE loan_summary AS
WITH p AS (
    SELECT
        *,
        (dq_months >= 3 OR is_reo_acquisition)          AS is_d90_month
    FROM perf_clean
),
perf_agg AS (
    SELECT
        loan_seq_no,

        -- observation window
        COUNT(*)                                        AS months_observed,
        MIN(reporting_period)                           AS first_report_month,
        MAX(reporting_period)                           AS last_report_month,
        MAX(loan_age)                                   AS max_loan_age,

        -- delinquency history
        MAX(COALESCE(dq_months, 0))                     AS worst_dq_months,
        COALESCE(BOOL_OR(dq_months >= 1), FALSE)        AS ever_d30,
        COALESCE(BOOL_OR(dq_months >= 2), FALSE)        AS ever_d60,
        COALESCE(BOOL_OR(is_d90_month), FALSE)          AS ever_d90_obs,
        MIN(reporting_period) FILTER (WHERE is_d90_month) AS first_d90_month,
        MIN(loan_age)         FILTER (WHERE is_d90_month) AS first_d90_loan_age,
        COALESCE(BOOL_OR(is_d90_month AND loan_age <= 24), FALSE) AS d90_24m_obs,

        -- hardship signals
        COALESCE(BOOL_OR(borrower_assistance_status = 'F'), FALSE) AS ever_forbearance,
        COALESCE(BOOL_OR(modification_flag = 'Y'), FALSE) AS ever_modified,

        -- termination (only one month per loan carries these)
        MAX(zero_balance_code)                          AS zero_balance_code,
        MAX(zero_balance_date)                          AS zero_balance_date,
        MAX(zero_balance_removal_upb)                   AS zero_balance_removal_upb,
        SUM(actual_loss)                                AS actual_loss,
        SUM(net_sale_proceeds)                          AS net_sale_proceeds,
        SUM(mi_recoveries)                              AS mi_recoveries,
        SUM(non_mi_recoveries)                          AS non_mi_recoveries,
        SUM(total_expenses)                             AS total_expenses,

        -- latest snapshot
        ARG_MAX(current_upb, reporting_period)          AS last_upb,
        ARG_MAX(dq_months,   reporting_period)          AS last_dq_months
    FROM p
    GROUP BY loan_seq_no
)
SELECT
    o.*,
    a.* EXCLUDE (loan_seq_no),

    -- a loan that ended in default counts as D90+ even if the monthly
    -- 90+ record was not captured
    (a.ever_d90_obs OR COALESCE(a.zero_balance_code IN ('02','03','09'), FALSE)) AS ever_d90,
    COALESCE(a.d90_24m_obs, FALSE)                                AS d90_within_24m,

    CASE
        WHEN a.zero_balance_code IN ('02','03','09') THEN 'default'
        WHEN a.zero_balance_code = '01'              THEN 'prepaid'
        WHEN a.zero_balance_code IN ('15','16')      THEN 'sold'
        WHEN a.zero_balance_code = '96'              THEN 'removed'
        ELSE 'active'
    END AS outcome
FROM orig_clean o
LEFT JOIN perf_agg a USING (loan_seq_no)
"""


def show(con, title, sql):
    print(f"\n== {title} ==")
    print(con.execute(sql).fetchdf().to_string(index=False))


def main():
    con = duckdb.connect(DB)
    con.execute("SET preserve_insertion_order = false")  # saves RAM on 8GB machine

    print("Building loan_summary (13.8M monthly rows -> 250K loans)...")
    con.execute(BUILD)

    show(con, "Integrity: 1 row per loan, every loan has history", """
        SELECT COUNT(*)                          AS rows,
               COUNT(DISTINCT loan_seq_no)       AS distinct_loans,
               COUNT(*) - COUNT(months_observed) AS loans_without_perf,
               COUNT(*) - COUNT(ever_d90)        AS null_ever_d90,
               COUNT(*) - COUNT(ever_forbearance) AS null_forbearance
        FROM loan_summary
    """)

    show(con, "Outcome mix (whole portfolio)", """
        SELECT outcome, COUNT(*) AS loans,
               ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
        FROM loan_summary GROUP BY 1 ORDER BY loans DESC
    """)

    show(con, "Sanity: defaults should almost always have a D90+ month", """
        SELECT COUNT(*) AS defaults,
               SUM(CASE WHEN ever_d90_obs THEN 1 ELSE 0 END) AS with_d90_month
        FROM loan_summary WHERE outcome = 'default'
    """)

    show(con, "Risk by vintage (rates in %)", """
        SELECT vintage,
               COUNT(*)                                             AS loans,
               ROUND(AVG(months_observed), 0)                       AS avg_months_obs,
               ROUND(100.0 * AVG(ever_d90::INT), 2)                 AS ever_d90_pct,
               ROUND(100.0 * AVG(d90_within_24m::INT), 2)           AS d90_24m_pct,
               ROUND(100.0 * AVG((outcome = 'default')::INT), 2)    AS default_pct,
               ROUND(100.0 * AVG((outcome = 'prepaid')::INT), 1)    AS prepaid_pct,
               ROUND(100.0 * AVG(ever_forbearance::INT), 2)         AS forbearance_pct,
               ROUND(AVG(first_d90_loan_age), 0)                    AS avg_age_at_first_d90
        FROM loan_summary
        GROUP BY vintage ORDER BY vintage
    """)

    show(con, "When did loans first hit D90+? (calendar year)", """
        SELECT YEAR(first_d90_month) AS year, COUNT(*) AS new_d90_loans
        FROM loan_summary WHERE first_d90_month IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)

    con.close()


if __name__ == "__main__":
    main()
