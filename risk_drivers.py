"""
Session 4, Step 2: Risk drivers - which borrower / loan traits predict D90+?

For each driver (FICO, LTV, DTI, purpose, occupancy, ...) we bucket loans and
compare the bad rate in each bucket.

Metrics per bucket:
  d90_24m_pct : % of loans 90+ days late within their first 24 months
                (main metric: fair across vintages, see Session 2)
  ever_d90_pct: % ever 90+ days late (whole history)
  lift        : d90_24m_pct / portfolio d90_24m_pct
                -> 2.0 means "twice as risky as the average loan"

Then a FICO x LTV grid (layered risk): low score AND thin equity together.
This grid becomes the segments for Expected Loss in Session 5.

Outputs:
  tables  risk_drivers, risk_grid
  files   outputs/risk_drivers.csv, outputs/risk_grid.csv

Run:  python risk_drivers.py
"""

from pathlib import Path

import duckdb
import pandas as pd

DB = "mortgage.duckdb"
OUT = Path("outputs")

# driver name -> (bucket label SQL, sort key SQL)
DRIVERS = {
    "FICO": ("""CASE WHEN credit_score IS NULL THEN 'Missing'
                     WHEN credit_score < 620 THEN '<620'
                     WHEN credit_score < 680 THEN '620-679'
                     WHEN credit_score < 720 THEN '680-719'
                     WHEN credit_score < 760 THEN '720-759'
                     WHEN credit_score < 800 THEN '760-799'
                     ELSE '800+' END""",
             "COALESCE(credit_score, 9999)"),
    "LTV": ("""CASE WHEN orig_ltv IS NULL THEN 'Missing'
                    WHEN orig_ltv <= 60  THEN '<=60'
                    WHEN orig_ltv <= 70  THEN '61-70'
                    WHEN orig_ltv <= 80  THEN '71-80'
                    WHEN orig_ltv <= 90  THEN '81-90'
                    WHEN orig_ltv <= 95  THEN '91-95'
                    WHEN orig_ltv <= 100 THEN '96-100'
                    ELSE '>100 (HARP)' END""",
            "COALESCE(orig_ltv, 9999)"),
    "DTI": ("""CASE WHEN orig_dti IS NULL THEN 'Missing'
                    WHEN orig_dti < 20 THEN '<20'
                    WHEN orig_dti < 30 THEN '20-29'
                    WHEN orig_dti <= 36 THEN '30-36'
                    WHEN orig_dti <= 43 THEN '37-43'
                    WHEN orig_dti <= 50 THEN '44-50'
                    ELSE '>50' END""",
            "COALESCE(orig_dti, 9999)"),
    "Loan purpose": ("""CASE loan_purpose WHEN 'P' THEN 'Purchase'
                                          WHEN 'C' THEN 'Cash-out refi'
                                          WHEN 'N' THEN 'No-cash-out refi'
                                          WHEN 'R' THEN 'Refi (unspecified)'
                                          ELSE 'Missing' END""",
                     "loan_purpose"),
    "Occupancy": ("""CASE occupancy_status WHEN 'P' THEN 'Owner-occupied'
                                           WHEN 'I' THEN 'Investment'
                                           WHEN 'S' THEN 'Second home'
                                           ELSE 'Missing' END""",
                  "occupancy_status"),
    "First-time buyer": ("""CASE first_time_homebuyer WHEN 'Y' THEN 'Yes'
                                                      WHEN 'N' THEN 'No'
                                                      ELSE 'Missing' END""",
                         "first_time_homebuyer"),
    "Borrowers": ("""CASE WHEN num_borrowers IS NULL THEN 'Missing'
                          WHEN num_borrowers = 1 THEN '1 borrower'
                          ELSE '2+ borrowers' END""",
                  "COALESCE(num_borrowers, 99)"),
    "Channel": ("""CASE channel WHEN 'R' THEN 'Retail'
                                WHEN 'B' THEN 'Broker'
                                WHEN 'C' THEN 'Correspondent'
                                WHEN 'T' THEN 'TPO (unspecified)'
                                ELSE 'Missing' END""",
                "channel"),
}

GRID = """
CREATE OR REPLACE TABLE risk_grid AS
WITH b AS (
    SELECT *,
        CASE WHEN credit_score IS NULL THEN NULL
             WHEN credit_score < 680 THEN '1: <680'
             WHEN credit_score < 740 THEN '2: 680-739'
             WHEN credit_score < 780 THEN '3: 740-779'
             ELSE '4: 780+' END                          AS fico_band,
        CASE WHEN orig_ltv IS NULL THEN NULL
             WHEN orig_ltv <= 70 THEN '1: <=70'
             WHEN orig_ltv <= 80 THEN '2: 71-80'
             WHEN orig_ltv <= 90 THEN '3: 81-90'
             ELSE '4: >90' END                           AS ltv_band
    FROM loan_summary
)
SELECT fico_band, ltv_band,
       COUNT(*)                                          AS loans,
       ROUND(100.0 * AVG(d90_within_24m::INT), 2)        AS d90_24m_pct,
       ROUND(100.0 * AVG(ever_d90::INT), 2)              AS ever_d90_pct
FROM b
WHERE fico_band IS NOT NULL AND ltv_band IS NOT NULL
GROUP BY 1, 2
ORDER BY 1, 2
"""


def build_drivers(con):
    parts = []
    for name, (label, sort) in DRIVERS.items():
        parts.append(f"""
            SELECT '{name}' AS driver, {label} AS bucket, MIN({sort}) AS sort_key,
                   COUNT(*) AS loans,
                   AVG(d90_within_24m::INT) AS d90_24m,
                   AVG(ever_d90::INT)       AS ever_d90
            FROM loan_summary GROUP BY 1, 2""")
    union = " UNION ALL ".join(f"SELECT driver, bucket, CAST(sort_key AS VARCHAR) AS sort_key, "
                               f"loans, d90_24m, ever_d90 FROM ({p})" for p in parts)
    con.execute(f"""
        CREATE OR REPLACE TABLE risk_drivers AS
        WITH u AS ({union}),
        base AS (SELECT AVG(d90_within_24m::INT) AS p FROM loan_summary)
        SELECT driver, bucket, sort_key, loans,
               ROUND(100.0 * loans / SUM(loans) OVER (PARTITION BY driver), 1) AS pct_of_book,
               ROUND(100.0 * d90_24m, 2)  AS d90_24m_pct,
               ROUND(100.0 * ever_d90, 2) AS ever_d90_pct,
               ROUND(d90_24m / base.p, 2) AS lift
        FROM u, base
    """)


def main():
    OUT.mkdir(exist_ok=True)
    con = duckdb.connect(DB)
    build_drivers(con)
    con.execute(GRID)

    base = con.execute("SELECT ROUND(100.0*AVG(d90_within_24m::INT),2) FROM loan_summary").fetchone()[0]
    print(f"Portfolio D90+ within 24m: {base}%  (lift 1.0 = this)\n")

    df = con.execute("SELECT * FROM risk_drivers").fetchdf()
    df["num"] = pd.to_numeric(df["sort_key"], errors="coerce")  # numeric drivers sort by value
    for name in DRIVERS:
        d = df[df.driver == name].copy()
        d = d.sort_values(["num", "sort_key"], na_position="last") if d["num"].notna().any() \
            else d.sort_values("d90_24m_pct", ascending=False)
        print(f"== {name} ==")
        print(d[["bucket", "loans", "pct_of_book", "d90_24m_pct", "ever_d90_pct", "lift"]]
              .to_string(index=False))
        print()

    print("== States: highest and lowest D90+ within 24m (states with >= 2,000 loans) ==")
    print(con.execute("""
        WITH s AS (
            SELECT property_state AS state, COUNT(*) AS loans,
                   ROUND(100.0 * AVG(d90_within_24m::INT), 2) AS d90_24m_pct
            FROM loan_summary GROUP BY 1 HAVING COUNT(*) >= 2000
        )
        (SELECT 'Highest' AS grp, * FROM s ORDER BY d90_24m_pct DESC LIMIT 5)
        UNION ALL
        (SELECT 'Lowest', * FROM s ORDER BY d90_24m_pct ASC LIMIT 5)
    """).fetchdf().to_string(index=False))

    print("\n== FICO x LTV grid: D90+ within 24m (%) ==")
    g = con.execute("SELECT * FROM risk_grid").fetchdf()
    print(g.pivot(index="fico_band", columns="ltv_band", values="d90_24m_pct").to_string())
    print("\n(loan counts)")
    print(g.pivot(index="fico_band", columns="ltv_band", values="loans").to_string())

    con.execute(f"COPY (SELECT * EXCLUDE (sort_key) FROM risk_drivers) "
                f"TO '{OUT / 'risk_drivers.csv'}' (HEADER)")
    con.execute(f"COPY risk_grid TO '{OUT / 'risk_grid.csv'}' (HEADER)")
    print(f"\nSaved {OUT / 'risk_drivers.csv'} and {OUT / 'risk_grid.csv'}")
    con.close()


if __name__ == "__main__":
    main()
