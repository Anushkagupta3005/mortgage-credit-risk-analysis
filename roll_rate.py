"""
Session 4, Step 1: Roll-rate matrix (kept small).

Question: if a loan is in bucket X this month, where is it NEXT month?

  States:  Current | 30 | 60 | 90+ (incl. REO) | Prepaid | Default | Other exit

  roll rate  = % moving to a WORSE bucket next month   (e.g. 30 -> 60)
  cure rate  = % moving back to Current                (e.g. 30 -> Current)

Banks use this to staff collections teams and forecast losses: if 30->60 jumps,
more loans will reach 90+ in two months.

We compare normal months vs the COVID forbearance window (Apr 2020 - Dec 2021).

Outputs:
  table  roll_rate                  (period, from_state, to_state, n, pct)
  file   outputs/roll_rate.csv      (for Tableau)

Run:  python roll_rate.py
"""

from pathlib import Path

import duckdb

DB = "mortgage.duckdb"
OUT = Path("outputs")

STATES = ["Current", "30", "60", "90+", "Prepaid", "Default", "Other exit"]

BUILD = """
CREATE OR REPLACE TABLE roll_rate AS
WITH s AS (
    SELECT
        loan_seq_no,
        reporting_period,
        CASE
            WHEN zero_balance_code = '01'                  THEN 'Prepaid'
            WHEN zero_balance_code IN ('02','03','09')     THEN 'Default'
            WHEN zero_balance_code IS NOT NULL             THEN 'Other exit'
            WHEN is_reo_acquisition OR dq_months >= 3      THEN '90+'
            WHEN dq_months = 2                             THEN '60'
            WHEN dq_months = 1                             THEN '30'
            ELSE 'Current'
        END AS state
    FROM perf_clean
),
t AS (
    SELECT
        reporting_period,
        state AS from_state,
        LEAD(state) OVER (PARTITION BY loan_seq_no ORDER BY reporting_period) AS to_state
    FROM s
),
agg AS (
    SELECT
        CASE WHEN reporting_period BETWEEN DATE '2020-04-01' AND DATE '2021-12-01'
             THEN 'COVID (Apr20-Dec21)' ELSE 'Normal' END   AS period,
        from_state,
        to_state,
        COUNT(*)                                            AS n
    FROM t
    WHERE to_state IS NOT NULL
      AND from_state IN ('Current', '30', '60', '90+')
    GROUP BY 1, 2, 3
)
SELECT period, from_state, to_state, n,
       ROUND(100.0 * n / SUM(n) OVER (PARTITION BY period, from_state), 3) AS pct
FROM agg
"""


def matrix(con, period):
    df = con.execute(f"""
        SELECT from_state, to_state, n FROM roll_rate WHERE period = '{period}'
    """).fetchdf()
    m = df.pivot_table(index="from_state", columns="to_state", values="n",
                       aggfunc="sum", fill_value=0)
    m = m.div(m.sum(axis=1), axis=0) * 100
    rows = [s for s in STATES[:4] if s in m.index]
    cols = [s for s in STATES if s in m.columns]
    return m.loc[rows, cols].round(2)


def main():
    OUT.mkdir(exist_ok=True)
    con = duckdb.connect(DB)
    con.execute("SET preserve_insertion_order = false")
    con.execute("SET memory_limit = '4GB'")  # 8GB Mac: leave room, spill rest to disk

    print("Building month-to-month transitions (13.8M rows)...")
    con.execute(BUILD)

    for period in ["Normal", "COVID (Apr20-Dec21)"]:
        print(f"\n== Roll-rate matrix, {period}: row = this month, column = next month (%) ==")
        print(matrix(con, period).to_string())

    print("\n== Key rates: Normal vs COVID (%) ==")
    print(con.execute("""
        SELECT period,
               MAX(pct) FILTER (WHERE from_state='Current' AND to_state='30')      AS current_to_30,
               MAX(pct) FILTER (WHERE from_state='30'      AND to_state='60')      AS roll_30_to_60,
               MAX(pct) FILTER (WHERE from_state='60'      AND to_state='90+')     AS roll_60_to_90,
               MAX(pct) FILTER (WHERE from_state='30'      AND to_state='Current') AS cure_30,
               MAX(pct) FILTER (WHERE from_state='90+'     AND to_state='Current') AS cure_90,
               MAX(pct) FILTER (WHERE from_state='90+'     AND to_state='Default') AS d90_to_default
        FROM roll_rate GROUP BY 1 ORDER BY 1 DESC
    """).fetchdf().to_string(index=False))

    con.execute(f"COPY roll_rate TO '{OUT / 'roll_rate.csv'}' (HEADER)")
    print(f"\nSaved {OUT / 'roll_rate.csv'}")
    con.close()


if __name__ == "__main__":
    main()
