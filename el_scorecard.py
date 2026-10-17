"""
Session 9: Plug the scorecard PD into the expected-loss engine.

Until now EL = PD x LGD x EAD used a SEGMENT PD: every loan in the same
FICO x LTV cell got the same PD. Here every loan gets its OWN PD from the
logistic scorecard (pd_logit.py), using its FICO, LTV, DTI, current LTV, loan
age and, most importantly, its recent payment history.

Steps:
  1. Refit the scorecard on ALL normal snapshots (2016-2025). Validation is
     done (pd_backtest.py), so the final model uses all the data.
  2. Point-in-time calibration: scale the odds so the average PD on the latest
     snapshot (2025-03) equals what actually happened there.
  3. Score today's book (the as-of month in el_loan) with features as of today.
  4. EL_scorecard = PD_scorecard x LGD x EAD, compared with the segment-PD EL.
     Loans already 90+ days late keep PD = 100%, exactly as before.

Outputs:
  table   el_scorecard_loan
  files   outputs/el_scorecard_summary.csv, outputs/el_scorecard_vintage.csv

Run:  python el_scorecard.py   (needs el_loan, perf_clean, orig_clean and
                                data/pd_model_data.parquet; imports pd_logit.py)
"""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import statsmodels.api as sm

from pd_logit import design_matrix, make_bands

DB = "mortgage.duckdb"
DATA = Path("data") / "pd_model_data.parquet"
OUT = Path("outputs")


def main():
    con = duckdb.connect(DB)
    con.execute("SET preserve_insertion_order = false")
    con.execute("SET memory_limit = '4GB'")

    # ---- 1. refit on all normal snapshots ----------------------------------
    hist = con.execute(f"SELECT * FROM '{DATA}'").df()
    hist["snap"] = pd.to_datetime(hist.snap)
    X_hist = design_matrix(make_bands(hist))
    model = sm.Logit(hist.bad.astype(int), X_hist).fit(disp=0, maxiter=200)
    hist["pd_raw"] = model.predict(X_hist)
    print(f"Refit on {len(hist):,} loan-snapshots (all 7 normal snapshots), "
          f"pseudo R-squared {model.prsquared:.3f}")

    # ---- 2. point-in-time calibration on the latest snapshot ---------------
    latest = hist[hist.snap == hist.snap.max()]
    p, a = latest.pd_raw.mean(), latest.bad.mean()
    factor = (a / (1 - a)) / (p / (1 - p))
    print(f"PIT calibration on {latest.snap.max():%Y-%m}: actual {100 * a:.3f}% vs "
          f"model {100 * p:.3f}%  ->  odds x {factor:.3f}")

    # ---- 3. today's book with features as of today -------------------------
    asof = con.execute("SELECT MAX(reporting_period) FROM perf_clean").fetchone()[0]
    book = con.execute(f"""
        WITH cur AS (
            SELECT loan_seq_no, loan_age, COALESCE(dq_months, 0) AS dq_now, eltv
            FROM perf_clean WHERE reporting_period = DATE '{asof}'
        ),
        lookback AS (
            SELECT loan_seq_no, MAX(dq_months) AS worst_dq_prev12
            FROM perf_clean
            WHERE dq_months >= 1
              AND reporting_period >  DATE '{asof}' - INTERVAL 12 MONTH
              AND reporting_period <= DATE '{asof}'
            GROUP BY 1
        )
        SELECT e.loan_seq_no, e.vintage, e.fico_band, e.ltv_band, e.ead, e.already_d90,
               e.lgd_d90, e.pd_pit AS pd_segment, e.el_pit AS el_segment,
               o.credit_score, o.orig_ltv, o.orig_dti, o.loan_purpose,
               o.first_time_homebuyer, o.num_borrowers, o.occupancy_status,
               COALESCE(o.mi_pct, 0) AS mi_pct,
               c.loan_age, c.dq_now, c.eltv,
               COALESCE(lb.worst_dq_prev12, 0) AS worst_dq_prev12
        FROM el_loan e
        JOIN orig_clean o USING (loan_seq_no)
        JOIN cur c USING (loan_seq_no)
        LEFT JOIN lookback lb USING (loan_seq_no)
    """).df()
    print(f"Scoring {len(book):,} active loans as of {asof:%Y-%m}")

    X_book = design_matrix(make_bands(book)).reindex(columns=X_hist.columns, fill_value=0.0)
    raw = model.predict(X_book)
    odds = raw / (1 - raw) * factor
    book["pd_scorecard"] = np.where(book.already_d90, 1.0, odds / (1 + odds))
    book["el_scorecard"] = book.pd_scorecard * book.lgd_d90 * book.ead

    # ---- 4. compare --------------------------------------------------------
    perf = ~book.already_d90
    tot_ead = book.ead.sum()
    summary = pd.DataFrame([
        {"pd_source": name,
         "el_mn": round(book[col].sum() / 1e6, 2),
         "el_bps": round(1e4 * book[col].sum() / tot_ead, 2),
         "avg_pd_performing_pct": round(100 * np.average(book.loc[perf, pdcol],
                                                         weights=book.loc[perf, "ead"]), 3),
         "el_from_already_d90_pct": round(100 * book.loc[~perf, col].sum() / book[col].sum(), 1)}
        for name, col, pdcol in [("Segment PD (FICO x LTV, PIT)", "el_segment", "pd_segment"),
                                 ("Scorecard PD (loan level, PIT)", "el_scorecard", "pd_scorecard")]
    ])
    print("\n== Portfolio 12-month expected loss ==")
    print(summary.to_string(index=False))

    # where does performing-loan EL sit? top 10% of loans by each PD
    for name, pdcol, elcol in [("segment", "pd_segment", "el_segment"),
                               ("scorecard", "pd_scorecard", "el_scorecard")]:
        pl = book[perf].sort_values(pdcol, ascending=False)
        top = pl.head(int(len(pl) * 0.10))
        print(f"  Riskiest 10% of performing loans by {name} PD carry "
              f"{100 * top[elcol].sum() / pl[elcol].sum():.0f}% of performing-loan EL")

    vin = book.groupby("vintage").apply(lambda g: pd.Series({
        "loans": len(g),
        "ead_mn": g.ead.sum() / 1e6,
        "el_segment_mn": g.el_segment.sum() / 1e6,
        "el_scorecard_mn": g.el_scorecard.sum() / 1e6,
    }), include_groups=False).reset_index()
    vin["el_segment_bps"] = 1e4 * vin.el_segment_mn / vin.ead_mn
    vin["el_scorecard_bps"] = 1e4 * vin.el_scorecard_mn / vin.ead_mn
    vin["share_el_scorecard_pct"] = 100 * vin.el_scorecard_mn / vin.el_scorecard_mn.sum()
    vin["change_pct"] = 100 * (vin.el_scorecard_mn / vin.el_segment_mn - 1)
    vin["loans"] = vin.loans.astype(int)
    print("\n== Expected loss by vintage: segment PD vs scorecard PD ==")
    print(vin.round(2).to_string(index=False))

    # loans the two approaches disagree on most (performing only)
    book["pd_ratio"] = book.pd_scorecard / book.pd_segment
    print("\n== Performing loans: how much does the scorecard move each loan's PD? ==")
    q = book.loc[perf, "pd_ratio"].quantile([0.1, 0.5, 0.9]).round(2)
    print(f"  10th pct {q.iloc[0]}x   median {q.iloc[1]}x   90th pct {q.iloc[2]}x of the segment PD")
    late_now = perf & (book.dq_now > 0)
    print(f"  Loans 30-60 days late today: {int(late_now.sum()):,}  |  avg PD segment "
          f"{100 * book.loc[late_now, 'pd_segment'].mean():.1f}% vs scorecard "
          f"{100 * book.loc[late_now, 'pd_scorecard'].mean():.1f}%")

    con.register("scored", book.drop(columns=["pd_ratio"]))
    con.execute("CREATE OR REPLACE TABLE el_scorecard_loan AS SELECT * FROM scored")
    OUT.mkdir(exist_ok=True)
    summary.to_csv(OUT / "el_scorecard_summary.csv", index=False)
    vin.round(3).to_csv(OUT / "el_scorecard_vintage.csv", index=False)
    print(f"\nSaved table el_scorecard_loan, {OUT / 'el_scorecard_summary.csv'}, "
          f"{OUT / 'el_scorecard_vintage.csv'}")
    con.close()


if __name__ == "__main__":
    main()
