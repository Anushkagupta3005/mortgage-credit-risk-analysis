"""
Session 5, Step 2: 12-month PD by segment (FICO band x LTV band).

PD here = probability that a loan which is NOT yet 90+ days late today
becomes 90+ days late (or defaults) within the NEXT 12 months.

Method (snapshot / "cohort" PD, the standard way banks measure it):
  1. Pick snapshot months.
  2. Take every loan reporting that month that is performing (< 90 days late).
  3. Look forward 12 months: did it hit D90+ / REO / a loss event?
  4. PD = bad loans / loans in the snapshot, pooled across snapshots.

Two regimes:
  Normal : snapshots whose 12-month window avoids COVID
           (2016-07, 2017-07, 2018-07, 2022-07, 2023-07, 2024-07, 2025-03)
  COVID  : snapshot 2019-10, window Oct 2019 - Oct 2020 (the actual shock)
The COVID / Normal ratio is an OBSERVED stress multiplier we can sanity-check
the Fed scenario against in Step 4.

Outputs:
  table  pd_segment
  file   outputs/pd_segment.csv

Run:  python pd_model.py
"""

from pathlib import Path

import duckdb

DB = "mortgage.duckdb"
OUT = Path("outputs")

SNAPSHOTS = [
    ("2016-07-01", "Normal"), ("2017-07-01", "Normal"), ("2018-07-01", "Normal"),
    ("2022-07-01", "Normal"), ("2023-07-01", "Normal"), ("2024-07-01", "Normal"),
    ("2025-03-01", "Normal"),
    ("2019-10-01", "COVID"),
]

BANDS = """
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
"""


def main():
    OUT.mkdir(exist_ok=True)
    con = duckdb.connect(DB)
    con.execute("SET preserve_insertion_order = false")
    con.execute("SET memory_limit = '4GB'")

    snaps = ", ".join(f"(DATE '{d}', '{r}')" for d, r in SNAPSHOTS)
    print("Building snapshot cohorts and 12-month outcomes...")
    con.execute(f"""
        CREATE OR REPLACE TABLE pd_obs AS
        WITH snaps AS (SELECT * FROM (VALUES {snaps}) t(snap, regime)),
        base AS (                    -- performing loans at each snapshot
            SELECT s.snap, s.regime, p.loan_seq_no
            FROM perf_clean p JOIN snaps s ON p.reporting_period = s.snap
            WHERE COALESCE(p.dq_months, 99) < 3
              AND NOT COALESCE(p.is_reo_acquisition, FALSE)
              AND p.zero_balance_code IS NULL
        ),
        bad_months AS (              -- every month a loan was in a bad state
            SELECT loan_seq_no, reporting_period
            FROM perf_clean
            WHERE dq_months >= 3
               OR COALESCE(is_reo_acquisition, FALSE)
               OR zero_balance_code IN ('02', '03', '09')
        ),
        bad AS (
            SELECT DISTINCT s.snap, b.loan_seq_no
            FROM bad_months b JOIN snaps s
              ON b.reporting_period >  s.snap
             AND b.reporting_period <= s.snap + INTERVAL 12 MONTH
        )
        SELECT base.snap, base.regime, base.loan_seq_no,
               (bad.loan_seq_no IS NOT NULL) AS went_bad_12m
        FROM base LEFT JOIN bad USING (snap, loan_seq_no)
    """)

    print("\n== PD by snapshot (whole book) ==")
    print(con.execute("""
        SELECT STRFTIME(snap, '%Y-%m') AS snapshot, regime,
               COUNT(*) AS performing_loans,
               SUM(went_bad_12m::INT)::BIGINT AS went_bad,
               ROUND(100.0 * AVG(went_bad_12m::INT), 3) AS pd_12m_pct
        FROM pd_obs GROUP BY 1, 2 ORDER BY 1
    """).fetchdf().to_string(index=False))

    con.execute(f"""
        CREATE OR REPLACE TABLE pd_segment AS
        WITH x AS (
            SELECT o.regime, o.went_bad_12m, {BANDS}
            FROM pd_obs o JOIN loan_summary l USING (loan_seq_no)
        )
        SELECT regime, fico_band, ltv_band,
               COUNT(*)                                   AS loan_snapshots,
               SUM(went_bad_12m::INT)::BIGINT             AS went_bad,
               ROUND(100.0 * AVG(went_bad_12m::INT), 3)   AS pd_12m_pct
        FROM x
        WHERE fico_band IS NOT NULL AND ltv_band IS NOT NULL
        GROUP BY 1, 2, 3
    """)

    df = con.execute("SELECT * FROM pd_segment").fetchdf()
    for regime in ["Normal", "COVID"]:
        d = df[df.regime == regime]
        print(f"\n== 12-month PD (%), {regime}: FICO band x LTV band ==")
        print(d.pivot(index="fico_band", columns="ltv_band", values="pd_12m_pct").to_string())
        print("(bad loans per cell)")
        print(d.pivot(index="fico_band", columns="ltv_band", values="went_bad").to_string())

    print("\n== Observed stress multiplier: COVID PD / Normal PD ==")
    print(con.execute("""
        SELECT regime,
               ROUND(100.0 * AVG(went_bad_12m::INT), 3) AS pd_12m_pct
        FROM pd_obs GROUP BY 1 ORDER BY 1 DESC
    """).fetchdf().to_string(index=False))
    m = df.pivot_table(index=["fico_band", "ltv_band"], columns="regime", values="pd_12m_pct")
    m["multiplier"] = (m["COVID"] / m["Normal"]).round(1)
    print("\nper segment:")
    print(m["multiplier"].unstack().to_string())

    con.execute(f"COPY pd_segment TO '{OUT / 'pd_segment.csv'}' (HEADER)")
    print(f"\nSaved {OUT / 'pd_segment.csv'}")
    con.close()


if __name__ == "__main__":
    main()
