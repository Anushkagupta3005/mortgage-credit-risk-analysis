"""
Session 5, Step 1: Loss Given Default (LGD) from actual Freddie Mac losses.

Expected Loss = PD x LGD x EAD
  PD  : probability a loan goes bad            (Step 2)
  LGD : % of the balance lost when it does     (THIS STEP)
  EAD : balance outstanding at that moment     (Step 3)

For every loan that ended in a loss event (zero_balance_code 02 third-party
sale, 03 short sale, 09 REO disposition) Freddie reports the actual loss after
selling the house, collecting mortgage insurance (MI) and paying expenses.

  severity (LGD) = net loss / UPB at default

We also measure the D90 -> default conversion: most loans that go 90+ days
late cure or are modified and never cause a loss. Since our PD will be
"probability of D90+", the loss we expect per D90+ loan is

  loss given D90 = conversion rate x severity

Outputs:
  table  lgd_summary
  file   outputs/lgd_summary.csv

Run:  python lgd.py
"""

from pathlib import Path

import duckdb

DB = "mortgage.duckdb"
OUT = Path("outputs")

# states where foreclosure must go through the courts (slower, costlier)
JUDICIAL = ("CT", "DE", "FL", "HI", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
            "NJ", "NM", "NY", "ND", "OH", "OK", "PA", "SC", "SD", "VT", "WI")


def main():
    OUT.mkdir(exist_ok=True)
    con = duckdb.connect(DB)

    print("== Loss events: is the loss data filled in? ==")
    print(con.execute("""
        SELECT zero_balance_code,
               COUNT(*)                                  AS loans,
               COUNT(actual_loss)                        AS with_actual_loss,
               COUNT(zero_balance_removal_upb)           AS with_default_upb,
               ROUND(MIN(actual_loss), 0)                AS min_loss,
               ROUND(MEDIAN(actual_loss), 0)             AS median_loss,
               ROUND(MAX(actual_loss), 0)                AS max_loss
        FROM loan_summary WHERE outcome = 'default'
        GROUP BY 1 ORDER BY 1
    """).fetchdf().to_string(index=False))

    # Freddie reports a loss as a NEGATIVE number; detect it instead of assuming
    med = con.execute("""SELECT MEDIAN(actual_loss) FROM loan_summary
                         WHERE outcome = 'default' AND actual_loss <> 0""").fetchone()[0]
    sign = -1 if (med is not None and med < 0) else 1
    print(f"\nLoss sign convention: losses are {'negative' if sign == -1 else 'positive'} "
          f"in the raw data (median non-zero = {med:,.0f})")

    judicial = ", ".join(f"'{s}'" for s in JUDICIAL)
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW losses AS
        SELECT *,
            {sign} * actual_loss                                   AS net_loss,
            {sign} * actual_loss / zero_balance_removal_upb        AS severity,
            CASE WHEN orig_ltv <= 70 THEN '1: <=70'
                 WHEN orig_ltv <= 80 THEN '2: 71-80'
                 WHEN orig_ltv <= 90 THEN '3: 81-90'
                 ELSE '4: >90' END                                 AS ltv_band,
            CASE WHEN property_state IN ({judicial})
                 THEN 'Judicial' ELSE 'Non-judicial' END           AS foreclosure_type,
            CASE WHEN COALESCE(mi_pct, 0) > 0 THEN 'Has MI' ELSE 'No MI' END AS mi_flag,
            CASE zero_balance_code WHEN '02' THEN 'Third-party sale'
                                   WHEN '03' THEN 'Short sale'
                                   WHEN '09' THEN 'REO disposition' END AS loss_type
        FROM loan_summary
        WHERE outcome = 'default'
          AND actual_loss IS NOT NULL
          AND zero_balance_removal_upb > 0
    """)

    def seg(col, label):
        return f"""
            SELECT '{label}' AS segment_type, {col} AS segment,
                   COUNT(*)                                        AS loss_events,
                   ROUND(SUM(zero_balance_removal_upb) / 1e6, 2)   AS upb_mn,
                   ROUND(SUM(net_loss) / 1e6, 2)                   AS loss_mn,
                   ROUND(100.0 * SUM(net_loss)
                         / SUM(zero_balance_removal_upb), 1)       AS severity_pct,
                   ROUND(100.0 * MEDIAN(severity), 1)              AS median_loan_severity_pct
            FROM losses GROUP BY 1, 2"""

    con.execute(f"""
        CREATE OR REPLACE TABLE lgd_summary AS
        {seg("'All'", 'Portfolio')}
        UNION ALL {seg('ltv_band', 'LTV band')}
        UNION ALL {seg('mi_flag', 'Mortgage insurance')}
        UNION ALL {seg('foreclosure_type', 'Foreclosure type')}
        UNION ALL {seg('loss_type', 'Loss type')}
        UNION ALL {seg('CAST(vintage AS VARCHAR)', 'Vintage')}
    """)

    print("\n== LGD (severity) = net loss / UPB at default, balance-weighted ==")
    df = con.execute("SELECT * FROM lgd_summary ORDER BY segment_type, segment").fetchdf()
    for t in ["Portfolio", "LTV band", "Mortgage insurance", "Foreclosure type",
              "Loss type", "Vintage"]:
        print(f"\n-- {t} --")
        print(df[df.segment_type == t].drop(columns="segment_type").to_string(index=False))

    print("\n== D90+ -> loss event conversion (how many D90+ loans actually cause a loss) ==")
    print(con.execute("""
        SELECT CAST(vintage AS VARCHAR)                             AS vintage,
               SUM(ever_d90::INT)::BIGINT                           AS ever_d90_loans,
               SUM((outcome = 'default')::INT)::BIGINT              AS loss_events,
               ROUND(100.0 * SUM((outcome = 'default')::INT)
                     / NULLIF(SUM(ever_d90::INT), 0), 1)            AS conversion_pct
        FROM loan_summary GROUP BY 1
        UNION ALL
        SELECT 'ALL', SUM(ever_d90::INT)::BIGINT, SUM((outcome = 'default')::INT)::BIGINT,
               ROUND(100.0 * SUM((outcome = 'default')::INT) / SUM(ever_d90::INT), 1)
        FROM loan_summary
        ORDER BY vintage
    """).fetchdf().to_string(index=False))

    con.execute(f"COPY lgd_summary TO '{OUT / 'lgd_summary.csv'}' (HEADER)")
    print(f"\nSaved {OUT / 'lgd_summary.csv'}")
    con.close()


if __name__ == "__main__":
    main()
