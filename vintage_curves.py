"""
Session 3, Step 1: Vintage curves (cumulative D90+ rate by loan age).

Idea (cohort analysis):
  Group loans by origination year (vintage). For each vintage, walk month by
  month through loan age and ask: "By month N, what % of this vintage's
  ORIGINAL loans had ever gone 90+ days delinquent?"

  Comparing vintages at the SAME loan age removes the observation-window
  bias (old vintages simply had more time to go bad).

  cum_d90_pct(vintage, age) = loans whose first D90+ happened at age <= N
                              --------------------------------------------  x 100
                                       all loans in the vintage

Curve cutoff:
  Each vintage's curve stops at the age that ~95% of its still-active loans
  have reached by the data end date, so we never plot ages where most of the
  cohort has not been observed yet.

Output:
  table  vintage_curve            (vintage, loan_age, new_d90, cum_d90, cum_d90_pct)
  file   outputs/vintage_curves.csv   (for Tableau in Session 6)

Run:  python vintage_curves.py
"""

from pathlib import Path

import duckdb

DB = "mortgage.duckdb"
OUT = Path("outputs")

BUILD = """
CREATE OR REPLACE TABLE vintage_curve AS
WITH caps AS (
    -- how far each vintage can be tracked: 5th percentile of the age
    -- reached by loans still active at the data end date
    SELECT vintage,
           COUNT(*) AS n_loans,
           QUANTILE_DISC(max_loan_age, 0.05)
               FILTER (WHERE outcome = 'active') AS max_age
    FROM loan_summary
    GROUP BY vintage
),
ages AS (
    SELECT vintage, n_loans, UNNEST(RANGE(0, max_age + 1)) AS loan_age
    FROM caps
),
first_hits AS (
    SELECT vintage, first_d90_loan_age AS loan_age, COUNT(*) AS new_d90
    FROM loan_summary
    WHERE first_d90_loan_age IS NOT NULL
    GROUP BY 1, 2
)
SELECT
    a.vintage,
    a.loan_age,
    a.n_loans,
    COALESCE(f.new_d90, 0)                                     AS new_d90,
    SUM(COALESCE(f.new_d90, 0))
        OVER (PARTITION BY a.vintage ORDER BY a.loan_age)      AS cum_d90,
    ROUND(100.0 * SUM(COALESCE(f.new_d90, 0))
        OVER (PARTITION BY a.vintage ORDER BY a.loan_age)
        / a.n_loans, 3)                                        AS cum_d90_pct
FROM ages a
LEFT JOIN first_hits f USING (vintage, loan_age)
ORDER BY a.vintage, a.loan_age
"""


def main():
    con = duckdb.connect(DB)
    con.execute(BUILD)
    print("Built table: vintage_curve")

    print("\n== Curve length per vintage ==")
    print(con.execute("""
        SELECT vintage, MAX(loan_age) AS tracked_to_age_months,
               MAX(cum_d90_pct)       AS final_cum_d90_pct
        FROM vintage_curve GROUP BY 1 ORDER BY 1
    """).fetchdf().to_string(index=False))

    print("\n== Cumulative D90+ % at the same loan age (blank = not reached yet) ==")
    df = con.execute("""
        SELECT vintage, loan_age, cum_d90_pct
        FROM vintage_curve
        WHERE loan_age IN (6, 12, 18, 24, 36, 48, 60, 84, 120)
    """).fetchdf()
    pivot = df.pivot(index="loan_age", columns="vintage", values="cum_d90_pct")
    print(pivot.to_string(na_rep=""))

    print("\n== Fastest-rising 6-month window per vintage (where the shock hit) ==")
    print(con.execute("""
        WITH w AS (
            SELECT vintage, loan_age,
                   cum_d90_pct - LAG(cum_d90_pct, 6) OVER
                       (PARTITION BY vintage ORDER BY loan_age) AS jump_6m
            FROM vintage_curve
        )
        SELECT vintage,
               ARG_MAX(loan_age, jump_6m) - 6 || '-' || ARG_MAX(loan_age, jump_6m)
                                              AS age_window,
               ROUND(MAX(jump_6m), 2)         AS pct_points_added
        FROM w GROUP BY 1 ORDER BY 1
    """).fetchdf().to_string(index=False))

    OUT.mkdir(exist_ok=True)
    con.execute(f"COPY vintage_curve TO '{OUT / 'vintage_curves.csv'}' (HEADER)")
    print(f"\nSaved {OUT / 'vintage_curves.csv'}")
    con.close()


if __name__ == "__main__":
    main()
