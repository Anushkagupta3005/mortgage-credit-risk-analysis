"""
Session 8, Step 1: Build the modelling dataset for a statistical PD model.

One row = one performing loan at one snapshot month (from pd_obs, built by
pd_model.py). Target = went 90+ days late / REO / loss event in the NEXT 12
months (same definition as the segment PD, so results are comparable).

Features (all known AT the snapshot, so no look-ahead):
  origination : FICO, original LTV, DTI, interest rate, loan purpose,
                first-time buyer, number of borrowers, occupancy, channel,
                property type, mortgage insurance, vintage
  behaviour   : loan age, months late right now (0 / 1 / 2),
                worst delinquency in the 12 months BEFORE the snapshot,
                estimated current LTV (ELTV), share of balance still owed

Split (out-of-time, the way banks validate):
  train : snapshots 2016-07, 2017-07, 2018-07, 2022-07, 2023-07
  test  : snapshots 2024-07, 2025-03   (never seen by the model)
  COVID snapshot 2019-10 is left out: forbearance makes it a different world.

Output:
  data/pd_model_data.parquet   (kept in data/, which is git-ignored)

Run:  python pd_features.py   (needs pd_obs from pd_model.py)
"""

from pathlib import Path

import duckdb

DB = "mortgage.duckdb"
OUT = Path("data") / "pd_model_data.parquet"

TRAIN_SNAPS = ("2016-07-01", "2017-07-01", "2018-07-01", "2022-07-01", "2023-07-01")
TEST_SNAPS = ("2024-07-01", "2025-03-01")


def main():
    OUT.parent.mkdir(exist_ok=True)
    con = duckdb.connect(DB)
    con.execute("SET preserve_insertion_order = false")
    con.execute("SET memory_limit = '4GB'")

    snaps = ", ".join(f"DATE '{d}'" for d in TRAIN_SNAPS + TEST_SNAPS)
    test = ", ".join(f"DATE '{d}'" for d in TEST_SNAPS)

    print("Building modelling dataset (one row per loan per snapshot)...")
    con.execute(f"""
        CREATE OR REPLACE TABLE pd_model_data AS
        WITH base AS (
            SELECT snap, loan_seq_no, went_bad_12m::INT AS bad
            FROM pd_obs
            WHERE snap IN ({snaps})
        ),
        at_snap AS (                 -- loan status in the snapshot month itself
            SELECT b.snap, b.loan_seq_no,
                   p.loan_age, COALESCE(p.dq_months, 0) AS dq_now,
                   p.eltv, p.current_upb
            FROM base b
            JOIN perf_clean p
              ON p.loan_seq_no = b.loan_seq_no AND p.reporting_period = b.snap
        ),
        late_rows AS (               -- only months where the loan was late (small)
            SELECT loan_seq_no, reporting_period, dq_months
            FROM perf_clean WHERE dq_months >= 1
        ),
        lookback AS (                -- worst delinquency in the 12 months BEFORE snap
            SELECT b.snap, b.loan_seq_no, MAX(l.dq_months) AS worst_dq_prev12
            FROM base b
            JOIN late_rows l
              ON l.loan_seq_no = b.loan_seq_no
             AND l.reporting_period >  b.snap - INTERVAL 12 MONTH
             AND l.reporting_period <= b.snap
            GROUP BY 1, 2
        )
        SELECT
            b.snap,
            CASE WHEN b.snap IN ({test}) THEN 'test' ELSE 'train' END AS split,
            b.loan_seq_no,
            b.bad,
            o.vintage,
            o.credit_score, o.orig_ltv, o.orig_dti, o.orig_interest_rate,
            o.loan_purpose, o.first_time_homebuyer, o.num_borrowers,
            o.occupancy_status, o.channel, o.property_type,
            COALESCE(o.mi_pct, 0) AS mi_pct,
            s.loan_age, s.dq_now,
            COALESCE(lb.worst_dq_prev12, 0) AS worst_dq_prev12,
            s.eltv,
            s.current_upb / NULLIF(o.orig_upb, 0) AS upb_ratio
        FROM base b
        JOIN orig_clean o USING (loan_seq_no)
        JOIN at_snap   s USING (snap, loan_seq_no)
        LEFT JOIN lookback lb USING (snap, loan_seq_no)
    """)

    show = lambda t, q: print(f"\n== {t} ==\n" + con.execute(q).fetchdf().to_string(index=False))

    show("Rows and bad rate by snapshot", """
        SELECT STRFTIME(snap, '%Y-%m') AS snapshot, split,
               COUNT(*) AS loans, SUM(bad) AS bad,
               ROUND(100.0 * AVG(bad), 3) AS bad_rate_pct
        FROM pd_model_data GROUP BY 1, 2 ORDER BY 1
    """)
    show("Train vs test", """
        SELECT split, COUNT(*) AS loans, SUM(bad) AS bad,
               ROUND(100.0 * AVG(bad), 3) AS bad_rate_pct
        FROM pd_model_data GROUP BY 1 ORDER BY 1 DESC
    """)
    show("Missing values per feature (count)", """
        SELECT COUNT(*) - COUNT(credit_score)       AS fico,
               COUNT(*) - COUNT(orig_ltv)           AS ltv,
               COUNT(*) - COUNT(orig_dti)           AS dti,
               COUNT(*) - COUNT(orig_interest_rate) AS rate,
               COUNT(*) - COUNT(loan_age)           AS loan_age,
               COUNT(*) - COUNT(eltv)               AS eltv,
               COUNT(*) - COUNT(upb_ratio)          AS upb_ratio,
               COUNT(*) - COUNT(first_time_homebuyer) AS first_time
        FROM pd_model_data
    """)
    show("Sanity: bad rate by current delinquency (should rise steeply)", """
        SELECT dq_now AS months_late_now, COUNT(*) AS loans,
               ROUND(100.0 * AVG(bad), 2) AS bad_rate_pct
        FROM pd_model_data GROUP BY 1 ORDER BY 1
    """)
    show("Sanity: bad rate by worst delinquency in prior 12 months", """
        SELECT worst_dq_prev12, COUNT(*) AS loans,
               ROUND(100.0 * AVG(bad), 2) AS bad_rate_pct
        FROM pd_model_data GROUP BY 1 ORDER BY 1
    """)

    con.execute(f"COPY pd_model_data TO '{OUT}' (FORMAT PARQUET)")
    print(f"\nSaved {OUT}  (table pd_model_data also kept in the database)")
    con.close()


if __name__ == "__main__":
    main()
