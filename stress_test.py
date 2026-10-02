"""
Session 5, Step 4: Stress test with the Fed's 2026 severely adverse scenario.

Fed 2026 supervisory scenario (final, Feb 4 2026), severely adverse:
  - unemployment 4.5% (Q4 2025) -> peak 10% in Q3 2027  (+5.5 pp)
  - house prices fall ~30% from Q4 2025 to a trough in Q4 2027
  - horizon Q1 2026 - Q1 2029
  Source: federalreserve.gov/publications/files/2026-final-supervisory-stress-test-scenarios-20260204.pdf

We translate the scenario into the four EL levers, one at a time, so the
result is a WATERFALL (how much each shock adds):

  0. Baseline        : PIT expected loss from expected_loss.py
  1. Unemployment    : PD x segment stress multiplier OBSERVED in COVID
                       (pd_model.py: COVID PD / Normal PD per FICO x LTV cell)
  2. Negative equity : house prices -30% -> stressed current LTV = ELTV / 0.70;
                       loans pushed underwater (> 100) get PD x NEG_EQUITY_MULT
  3. Conversion      : D90 -> loss conversion rises to the rate of the 2012
                       vintage (post-crisis, underwater HARP loans = our closest
                       in-sample analog of a falling-house-price world)
  4. Severity        : recovery on sale falls with house prices; uplift computed
                       per loan from its ELTV, net of mortgage insurance, and
                       added on top of the empirical severity

Assumptions are constants below; change them and re-run to see sensitivity.

Outputs:
  tables  stress_loan
  files   outputs/stress_waterfall.csv, outputs/stress_vintage.csv,
          outputs/stress_sensitivity.csv

Run:  python stress_test.py   (needs el_loan, pd_segment, loan_summary)
"""

from pathlib import Path

import duckdb
import pandas as pd

DB = "mortgage.duckdb"
OUT = Path("outputs")

HPI_SHOCK = 0.30          # Fed severely adverse: house prices -30%
NEG_EQUITY_MULT = 2.0     # assumption: PD uplift for loans pushed underwater
SALE_COST = 0.10          # assumption: foreclosure + selling costs, % of value
CARRY_COST = 0.05         # assumption: unpaid interest, taxes, insurance, % of UPB


def main():
    OUT.mkdir(exist_ok=True)
    con = duckdb.connect(DB)
    con.execute("SET preserve_insertion_order = false")
    con.execute("SET memory_limit = '4GB'")
    asof = con.execute("SELECT MAX(reporting_period) FROM perf_clean").fetchone()[0]

    conv_base = con.execute("""SELECT AVG((outcome = 'default')::INT) FROM loan_summary
                               WHERE ever_d90 AND outcome <> 'active'""").fetchone()[0]
    conv_stress = con.execute("""SELECT AVG((outcome = 'default')::INT) FROM loan_summary
                                 WHERE ever_d90 AND outcome <> 'active'
                                   AND vintage = 2012""").fetchone()[0]
    print(f"Conversion D90 -> loss: baseline {100*conv_base:.1f}%  "
          f"stressed (2012-vintage analog) {100*conv_stress:.1f}%")

    con.execute(f"""
        CREATE OR REPLACE TABLE stress_loan AS
        WITH mult AS (
            SELECT fico_band, ltv_band,
                   MAX(pd_12m_pct) FILTER (WHERE regime = 'COVID')
                 / MAX(pd_12m_pct) FILTER (WHERE regime = 'Normal') AS ue_mult
            FROM pd_segment GROUP BY 1, 2
        ),
        base AS (
            SELECT e.*, l.mi_pct, l.orig_ltv, l.orig_upb,
                   COALESCE(p.eltv, l.orig_ltv * e.ead / l.orig_upb)  AS eltv_now,
                   e.lgd_d90 / {conv_base}                            AS sev_base
            FROM el_loan e
            JOIN loan_summary l USING (loan_seq_no)
            LEFT JOIN perf_clean p
                   ON p.loan_seq_no = e.loan_seq_no AND p.reporting_period = DATE '{asof}'
        ),
        x AS (
            SELECT b.*,
                   COALESCE(m.ue_mult, (SELECT MEDIAN(ue_mult) FROM mult)) AS ue_mult,
                   eltv_now / (1 - {HPI_SHOCK})                            AS eltv_stress,
                   -- recovery model: sell at (value x (1-shock)) minus costs
                   GREATEST(0, LEAST(1, 1 - (100.0 / eltv_now) * (1 - {SALE_COST})
                                        + {CARRY_COST} - COALESCE(mi_pct, 0) / 100.0))
                                                                           AS sev_model_0,
                   GREATEST(0, LEAST(1, 1 - (100.0 / eltv_now) * (1 - {HPI_SHOCK})
                                        * (1 - {SALE_COST})
                                        + {CARRY_COST} - COALESCE(mi_pct, 0) / 100.0))
                                                                           AS sev_model_s
            FROM base b LEFT JOIN mult m USING (fico_band, ltv_band)
        )
        SELECT *,
            -- step 1: unemployment
            CASE WHEN already_d90 THEN 1.0 ELSE LEAST(1, pd_pit * ue_mult) END   AS pd_s1,
            -- step 2: + negative equity
            CASE WHEN already_d90 THEN 1.0
                 ELSE LEAST(1, pd_pit * ue_mult
                              * CASE WHEN eltv_stress > 100 THEN {NEG_EQUITY_MULT} ELSE 1 END)
            END                                                                  AS pd_s2,
            LEAST(1, sev_base + (sev_model_s - sev_model_0))                     AS sev_stress
        FROM x
    """)

    w = con.execute(f"""
        SELECT
          SUM(ead)                                                   AS ead,
          SUM(pd_pit * {conv_base}   * sev_base   * ead)             AS s0,
          SUM(pd_s1  * {conv_base}   * sev_base   * ead)             AS s1,
          SUM(pd_s2  * {conv_base}   * sev_base   * ead)             AS s2,
          SUM(pd_s2  * {conv_stress} * sev_base   * ead)             AS s3,
          SUM(pd_s2  * {conv_stress} * sev_stress * ead)             AS s4,
          -- 2-year cumulative (scenario's stress peak lasts ~2 years)
          SUM(CASE WHEN already_d90 THEN 1 ELSE 1 - POWER(1 - pd_s2, 2) END
              * {conv_stress} * sev_stress * ead)                    AS s4_2y
        FROM stress_loan
    """).fetchdf().iloc[0]

    steps = [
        ("0. Baseline (PIT)", w.s0, w.s0),
        ("1. + Unemployment (PD)", w.s1, w.s1 - w.s0),
        ("2. + Negative equity (PD)", w.s2, w.s2 - w.s1),
        ("3. + D90->loss conversion", w.s3, w.s3 - w.s2),
        ("4. + Loss severity (HPI -30%)", w.s4, w.s4 - w.s3),
    ]
    wf = pd.DataFrame(steps, columns=["step", "el_mn", "added_mn"])
    wf["el_mn"] = (wf.el_mn / 1e6).round(2)
    wf["added_mn"] = (wf.added_mn / 1e6).round(2)
    wf["el_bps"] = (1e4 * wf.el_mn * 1e6 / w.ead).round(1)
    wf["x_baseline"] = (wf.el_mn / wf.el_mn.iloc[0]).round(1)
    print(f"\n== Stress waterfall: 12-month expected loss on ${w.ead/1e9:.1f}B book ==")
    print(wf.to_string(index=False))
    print(f"\n2-year cumulative stressed loss: ${w.s4_2y/1e6:,.1f}M "
          f"= {100 * w.s4_2y / w.ead:.2f}% of balance")

    show = lambda t, q: print(f"\n== {t} ==\n" + con.execute(q).fetchdf().to_string(index=False))
    show("Negative equity under the scenario", f"""
        SELECT COUNT(*) FILTER (WHERE eltv_now > 100)                      AS underwater_today,
               COUNT(*) FILTER (WHERE eltv_stress > 100)                   AS underwater_stressed,
               ROUND(100.0 * COUNT(*) FILTER (WHERE eltv_stress > 100) / COUNT(*), 1)
                                                                           AS pct_underwater_stressed,
               ROUND(AVG(eltv_now), 1)                                     AS avg_eltv_now,
               ROUND(AVG(eltv_stress), 1)                                  AS avg_eltv_stressed,
               ROUND(100 * SUM(sev_base * ead) / SUM(ead), 1)              AS severity_base_pct,
               ROUND(100 * SUM(sev_stress * ead) / SUM(ead), 1)            AS severity_stress_pct
        FROM stress_loan
    """)

    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW stress_vintage AS
        SELECT vintage,
               ROUND(SUM(ead) / 1e6, 1)                                         AS ead_mn,
               ROUND(SUM(pd_pit * {conv_base} * sev_base * ead) / 1e6, 2)       AS el_base_mn,
               ROUND(SUM(pd_s2 * {conv_stress} * sev_stress * ead) / 1e6, 2)    AS el_stress_mn,
               ROUND(SUM(pd_s2 * {conv_stress} * sev_stress * ead)
                   / SUM(pd_pit * {conv_base} * sev_base * ead), 1)            AS x_baseline,
               ROUND(1e4 * SUM(pd_s2 * {conv_stress} * sev_stress * ead) / SUM(ead), 1)
                                                                                AS el_stress_bps,
               ROUND(100.0 * COUNT(*) FILTER (WHERE eltv_stress > 100) / COUNT(*), 1)
                                                                                AS pct_underwater
        FROM stress_loan GROUP BY 1 ORDER BY 1
    """)
    show("Stressed EL by vintage", "SELECT * FROM stress_vintage")

    # sensitivity: how much does the answer move with our two softest assumptions?
    rows = []
    for neg in (1.0, 1.5, 2.0, 3.0):
        for ue_scale in (0.5, 1.0, 1.5):
            v = con.execute(f"""
                SELECT SUM(CASE WHEN already_d90 THEN 1.0 ELSE LEAST(1, pd_pit * (1 + (ue_mult - 1) * {ue_scale})
                           * CASE WHEN eltv_stress > 100 THEN {neg} ELSE 1 END) END
                           * {conv_stress} * sev_stress * ead) FROM stress_loan
            """).fetchone()[0]
            rows.append((neg, ue_scale, round(v / 1e6, 2), round(1e4 * v / w.ead, 1)))
    sens = pd.DataFrame(rows, columns=["neg_equity_mult", "ue_shock_scale", "el_mn", "el_bps"])
    print("\n== Sensitivity: stressed 12-month EL ($M) ==")
    print("rows = negative-equity PD multiplier, cols = unemployment shock (1.0 = COVID-observed)")
    print(sens.pivot(index="neg_equity_mult", columns="ue_shock_scale", values="el_mn").to_string())

    wf.to_csv(OUT / "stress_waterfall.csv", index=False)
    sens.to_csv(OUT / "stress_sensitivity.csv", index=False)
    con.execute(f"COPY (SELECT * FROM stress_vintage) TO '{OUT / 'stress_vintage.csv'}' (HEADER)")
    print(f"\nSaved {OUT / 'stress_waterfall.csv'}, {OUT / 'stress_vintage.csv'}, "
          f"{OUT / 'stress_sensitivity.csv'}")
    con.close()


if __name__ == "__main__":
    main()
