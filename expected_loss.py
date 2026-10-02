"""
Session 5, Step 3: EAD + baseline Expected Loss.

  EL = PD x LGD x EAD                (per loan, then summed by segment)

  EAD : current unpaid balance of every active loan in the LATEST month
  PD  : 12-month PD of the loan's FICO x LTV segment (from pd_model.py)
          TTC = through-the-cycle (all normal snapshots pooled)
          PIT = point-in-time     (latest snapshots: 2024-07, 2025-03)
        loans ALREADY 90+ days late get PD = 100%
  LGD : loss given D90 = conversion x severity
          severity   : actual loss / UPB at default, by LTV band (lgd.py)
          conversion : share of D90+ loans that end in a loss event, measured
                       only on D90+ loans that are already RESOLVED (paid off,
                       sold or defaulted), because loans still active may yet
                       default (right-censoring)

Outputs:
  tables  el_loan, el_segment
  files   outputs/el_segment.csv, outputs/el_vintage.csv

Run:  python expected_loss.py   (needs loan_summary, pd_obs, pd_segment, lgd_summary)
"""

from pathlib import Path

import duckdb

DB = "mortgage.duckdb"
OUT = Path("outputs")
PIT_SNAPSHOTS = ("2024-07-01", "2025-03-01")

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


def show(con, title, sql):
    print(f"\n== {title} ==")
    print(con.execute(sql).fetchdf().to_string(index=False))


def main():
    OUT.mkdir(exist_ok=True)
    con = duckdb.connect(DB)
    con.execute("SET preserve_insertion_order = false")
    con.execute("SET memory_limit = '4GB'")

    asof = con.execute("SELECT MAX(reporting_period) FROM perf_clean").fetchone()[0]
    print(f"As-of month (EAD snapshot): {asof}")

    # ---- conversion: D90+ -> loss event, on resolved D90+ loans only ----------
    show(con, "D90+ loans by outcome (why we only use resolved ones)", """
        SELECT outcome, COUNT(*) AS ever_d90_loans
        FROM loan_summary WHERE ever_d90 GROUP BY 1 ORDER BY 2 DESC
    """)
    conv = con.execute("""
        SELECT AVG((outcome = 'default')::INT)
        FROM loan_summary WHERE ever_d90 AND outcome <> 'active'
    """).fetchone()[0]
    print(f"\nConversion D90+ -> loss (resolved D90+ loans): {100 * conv:.1f}%")

    # ---- LGD given D90 by LTV band -------------------------------------------
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE lgd_ltv AS
        SELECT segment AS ltv_band, severity_pct / 100.0 AS severity,
               {conv} * severity_pct / 100.0 AS lgd_d90
        FROM lgd_summary WHERE segment_type = 'LTV band'
    """)
    port_sev = con.execute("""SELECT severity_pct / 100.0 FROM lgd_summary
                              WHERE segment_type = 'Portfolio'""").fetchone()[0]
    show(con, "LGD inputs by LTV band", """
        SELECT ltv_band, ROUND(100 * severity, 1) AS severity_pct,
               ROUND(100 * lgd_d90, 2) AS loss_given_d90_pct
        FROM lgd_ltv ORDER BY 1
    """)

    # ---- PD tables: TTC and PIT -----------------------------------------------
    pit = ", ".join(f"DATE '{d}'" for d in PIT_SNAPSHOTS)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE pd_pit AS
        WITH x AS (
            SELECT o.went_bad_12m, {BANDS}
            FROM pd_obs o JOIN loan_summary l USING (loan_seq_no)
            WHERE o.snap IN ({pit})
        )
        SELECT fico_band, ltv_band, AVG(went_bad_12m::INT) AS pd_pit
        FROM x GROUP BY 1, 2
    """)
    pd_ttc_all = con.execute("SELECT AVG(went_bad_12m::INT) FROM pd_obs "
                             "WHERE regime = 'Normal'").fetchone()[0]
    pd_pit_all = con.execute(f"SELECT AVG(went_bad_12m::INT) FROM pd_obs "
                             f"WHERE snap IN ({pit})").fetchone()[0]

    # ---- loan-level EL ---------------------------------------------------------
    con.execute(f"""
        CREATE OR REPLACE TABLE el_loan AS
        WITH book AS (
            SELECT p.loan_seq_no, p.current_upb AS ead,
                   (p.dq_months >= 3 OR COALESCE(p.is_reo_acquisition, FALSE)) AS already_d90
            FROM perf_clean p
            WHERE p.reporting_period = DATE '{asof}'
              AND p.zero_balance_code IS NULL
              AND p.current_upb > 0
        ),
        l AS (SELECT loan_seq_no, vintage, {BANDS} FROM loan_summary)
        SELECT b.loan_seq_no, l.vintage, l.fico_band, l.ltv_band, b.ead, b.already_d90,
               CASE WHEN b.already_d90 THEN 1.0
                    ELSE COALESCE(t.pd_12m_pct / 100.0, {pd_ttc_all}) END   AS pd_ttc,
               CASE WHEN b.already_d90 THEN 1.0
                    ELSE COALESCE(pp.pd_pit, {pd_pit_all}) END              AS pd_pit,
               COALESCE(g.lgd_d90, {conv} * {port_sev})                      AS lgd_d90
        FROM book b
        JOIN l USING (loan_seq_no)
        LEFT JOIN pd_segment t
               ON t.regime = 'Normal' AND t.fico_band = l.fico_band AND t.ltv_band = l.ltv_band
        LEFT JOIN pd_pit pp ON pp.fico_band = l.fico_band AND pp.ltv_band = l.ltv_band
        LEFT JOIN lgd_ltv g ON g.ltv_band = l.ltv_band
    """)
    con.execute("""
        CREATE OR REPLACE TABLE el_loan AS
        SELECT *, pd_ttc * lgd_d90 * ead AS el_ttc, pd_pit * lgd_d90 * ead AS el_pit
        FROM el_loan
    """)

    show(con, "Portfolio Expected Loss (12-month)", """
        SELECT COUNT(*)                                        AS active_loans,
               ROUND(SUM(ead) / 1e6, 1)                        AS ead_mn,
               SUM(already_d90::INT)::BIGINT                   AS already_d90_loans,
               ROUND(100.0 * SUM(ead * pd_ttc) / SUM(ead), 3)  AS pd_ttc_pct,
               ROUND(100.0 * SUM(ead * pd_pit) / SUM(ead), 3)  AS pd_pit_pct,
               ROUND(SUM(el_ttc) / 1e6, 3)                     AS el_ttc_mn,
               ROUND(SUM(el_pit) / 1e6, 3)                     AS el_pit_mn,
               ROUND(1e4 * SUM(el_ttc) / SUM(ead), 1)          AS el_ttc_bps,
               ROUND(1e4 * SUM(el_pit) / SUM(ead), 1)          AS el_pit_bps
        FROM el_loan
    """)

    con.execute("""
        CREATE OR REPLACE TABLE el_segment AS
        SELECT COALESCE(fico_band, 'Missing') AS fico_band,
               COALESCE(ltv_band, 'Missing')  AS ltv_band,
               COUNT(*)                                        AS loans,
               ROUND(SUM(ead) / 1e6, 2)                        AS ead_mn,
               ROUND(100.0 * SUM(ead) / SUM(SUM(ead)) OVER (), 1)       AS pct_of_ead,
               ROUND(SUM(el_pit) / 1e3, 1)                     AS el_pit_k,
               ROUND(100.0 * SUM(el_pit) / SUM(SUM(el_pit)) OVER (), 1) AS pct_of_el,
               ROUND(1e4 * SUM(el_pit) / SUM(ead), 1)          AS el_pit_bps
        FROM el_loan GROUP BY 1, 2 ORDER BY 1, 2
    """)
    df = con.execute("SELECT * FROM el_segment").fetchdf()
    print("\n== EL rate (bps of balance, PIT): FICO x LTV ==")
    print(df.pivot(index="fico_band", columns="ltv_band", values="el_pit_bps").to_string())
    print("\n== Concentration: share of balance vs share of expected loss (%) ==")
    print(df.sort_values("pct_of_el", ascending=False).head(6)[
        ["fico_band", "ltv_band", "loans", "pct_of_ead", "pct_of_el", "el_pit_bps"]
    ].to_string(index=False))

    show(con, "Where the expected loss comes from: already-D90 vs performing", """
        SELECT CASE WHEN already_d90 THEN 'Already 90+ late' ELSE 'Performing' END AS bucket,
               COUNT(*) AS loans,
               ROUND(100.0 * SUM(ead) / SUM(SUM(ead)) OVER (), 1)    AS pct_of_ead,
               ROUND(100.0 * SUM(el_pit) / SUM(SUM(el_pit)) OVER (), 1) AS pct_of_el
        FROM el_loan GROUP BY 1
    """)

    con.execute("""
        CREATE OR REPLACE TEMP VIEW el_vintage AS
        SELECT vintage, COUNT(*) AS loans,
               ROUND(SUM(ead) / 1e6, 1)                                  AS ead_mn,
               ROUND(100.0 * SUM(ead) / SUM(SUM(ead)) OVER (), 1)        AS pct_of_ead,
               ROUND(SUM(el_pit) / 1e3, 1)                               AS el_pit_k,
               ROUND(100.0 * SUM(el_pit) / SUM(SUM(el_pit)) OVER (), 1)  AS pct_of_el,
               ROUND(1e4 * SUM(el_pit) / SUM(ead), 1)                    AS el_pit_bps
        FROM el_loan GROUP BY 1 ORDER BY 1
    """)
    show(con, "Expected loss by vintage (PIT)", "SELECT * FROM el_vintage")

    con.execute(f"COPY el_segment TO '{OUT / 'el_segment.csv'}' (HEADER)")
    con.execute(f"COPY (SELECT * FROM el_vintage) TO '{OUT / 'el_vintage.csv'}' (HEADER)")
    print(f"\nSaved {OUT / 'el_segment.csv'} and {OUT / 'el_vintage.csv'}")
    con.close()


if __name__ == "__main__":
    main()
