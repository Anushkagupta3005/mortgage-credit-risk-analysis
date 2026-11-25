"""
Session 10: Management overlay for the 2023 vintage.

Why: the backtest (pd_backtest.py) showed 2023 loans default more often than
their FICO, LTV and DTI predict. The likely cause is payment shock at high
rates, which no feature in the model captures. Until the model is rebuilt with
that driver, a bank would not leave the gap uncovered: it adds a separate,
documented amount on top of the model's expected loss (a "management overlay"
or "post-model adjustment"). The loss is the same either way; the overlay
books it now instead of letting it hit earnings later as a surprise.

Steps:
  1. Re-run el_scorecard.py's recipe (fit + point-in-time calibration) one
     step back in time, twice, so each time there is a snapshot it has NOT seen.
  2. On that unseen snapshot, measure the gap that is LEFT after calibration,
     by vintage: actual bad rate / predicted bad rate.
     (Measuring on the snapshot the model was calibrated on makes the gap look
     ~0 by construction; the 1.39x in the backtest is BEFORE calibration, so it
     double counts. The out-of-time gap after calibration is the honest one.)
  3. Overlay factor for each overlay vintage = that gap (point estimate from
     the latest window, low/high range from both windows). Floored at 1.
  4. Apply it to PERFORMING loans of that vintage only:
        PD_overlay = min(PD_scorecard x factor, 100%)
     Loans already 90+ days late are already at PD = 100%.
  5. Report model EL + overlay = total EL, by vintage, with the range.

Outputs:
  table   el_overlay_loan
  files   outputs/overlay_gap.csv, outputs/overlay_summary.csv,
          outputs/overlay_vintage.csv

Run:  python overlay.py   (after el_scorecard.py)
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

OVERLAY_VINTAGES = [2019, 2023]   # 2023: practitioner feedback + backtest; 2019: COVID vintage, also above prediction


def main():
    con = duckdb.connect(DB)
    con.execute("SET preserve_insertion_order = false")
    con.execute("SET memory_limit = '4GB'")

    # ---- 1-2. out-of-time gap, by vintage ----------------------------------
    # Repeat el_scorecard.py's recipe one step back in time, twice:
    #   window A: fit on snapshots before 2024-07, calibrate on the latest of them,
    #             then measure the gap on 2024-07 (never seen)
    #   window B: fit on snapshots before 2025-03, calibrate on 2024-07,
    #             then measure the gap on 2025-03 (never seen)
    # This is how far the SAME procedure missed on data it had not seen, so it is
    # the honest estimate of how far today's model will miss next year.
    hist = con.execute(f"SELECT * FROM '{DATA}'").df()
    hist["snap"] = pd.to_datetime(hist.snap)
    snaps = sorted(hist.snap.unique())
    X_all = design_matrix(make_bands(hist))

    gaps = []
    for test_snap in snaps[-2:]:
        train = hist.snap < test_snap
        cal = hist.snap == max(s for s in snaps if s < test_snap)
        test = hist.snap == test_snap
        model = sm.Logit(hist.bad[train].astype(int), X_all[train]).fit(disp=0, maxiter=200)
        raw = model.predict(X_all)
        p, a = raw[cal].mean(), hist.bad[cal].mean()
        f = (a / (1 - a)) / (p / (1 - p))
        odds = raw[test] / (1 - raw[test]) * f
        t = hist[test].assign(pd_cal=odds / (1 + odds))
        g = (t.groupby("vintage")
              .agg(loans=("bad", "size"), actual_pct=("bad", "mean"), predicted_pct=("pd_cal", "mean"))
              .reset_index())
        g[["actual_pct", "predicted_pct"]] *= 100
        g.insert(0, "test_snapshot", pd.Timestamp(test_snap).strftime("%Y-%m"))
        g["gap_x"] = g.actual_pct / g.predicted_pct
        gaps.append(g)
        print(f"Window fit on {int(train.sum()):,} rows, calibrated odds x {f:.3f}, "
              f"tested on {pd.Timestamp(test_snap):%Y-%m}")
    gap = pd.concat(gaps, ignore_index=True)
    print("\n== Out-of-time gap after calibration (actual / predicted bad rate) ==")
    print(gap.round(3).to_string(index=False))

    # ---- 3. overlay factor per vintage: point + range ----------------------
    latest = pd.Timestamp(snaps[-1]).strftime("%Y-%m")
    factors = {}
    for v in OVERLAY_VINTAGES:
        g = gap[gap.vintage == v].set_index("test_snapshot").gap_x
        factors[v] = {"low": max(1.0, g.min()), "point": max(1.0, g.loc[latest]),
                      "high": max(1.0, g.max())}
        print(f"\nOverlay factor for {v}: point {factors[v]['point']:.2f}x  "
              f"(range {factors[v]['low']:.2f}x - {factors[v]['high']:.2f}x)")

    # ---- 4. apply to today's book ------------------------------------------
    book = con.execute("""
        SELECT loan_seq_no, vintage, ead, already_d90, lgd_d90, pd_scorecard, el_scorecard
        FROM el_scorecard_loan
    """).df()
    perf = ~book.already_d90

    def overlay_pd(case):
        f = book.vintage.map({v: x[case] for v, x in factors.items()}).fillna(1.0)
        return np.where(perf, np.minimum(book.pd_scorecard * f, 1.0), book.pd_scorecard)

    for case in ("low", "point", "high"):
        book[f"overlay_{case}"] = (overlay_pd(case) - book.pd_scorecard) * book.lgd_d90 * book.ead
    book["pd_overlay"] = overlay_pd("point")
    book["el_total"] = book.el_scorecard + book.overlay_point

    # ---- 5. report ---------------------------------------------------------
    tot_ead = book.ead.sum()
    model_el = book.el_scorecard.sum()
    rows = [{"line": "Model EL (scorecard PD)", "el_mn": model_el / 1e6}]
    for case in ("point", "low", "high"):
        ov = book[f"overlay_{case}"].sum()
        label = "Overlay" if case == "point" else f"Overlay ({case})"
        rows.append({"line": label, "el_mn": ov / 1e6})
        if case == "point":
            rows.append({"line": "Total EL (model + overlay)", "el_mn": (model_el + ov) / 1e6})
    summary = pd.DataFrame(rows)
    summary["el_bps"] = 1e4 * summary.el_mn * 1e6 / tot_ead
    summary["vs_model_pct"] = 100 * summary.el_mn * 1e6 / model_el
    print("\n== Portfolio 12-month expected loss with overlay ==")
    print(summary.round(2).to_string(index=False))

    vin = book.groupby("vintage").agg(
        loans=("loan_seq_no", "size"), ead_mn=("ead", "sum"),
        model_el_mn=("el_scorecard", "sum"), overlay_mn=("overlay_point", "sum"),
        total_el_mn=("el_total", "sum")).reset_index()
    vin[["ead_mn", "model_el_mn", "overlay_mn", "total_el_mn"]] /= 1e6
    vin["total_el_bps"] = 1e4 * vin.total_el_mn / vin.ead_mn
    vin["share_total_el_pct"] = 100 * vin.total_el_mn / vin.total_el_mn.sum()
    print("\n== By vintage ==")
    print(vin.round(3).to_string(index=False))

    con.register("ov", book)
    con.execute("CREATE OR REPLACE TABLE el_overlay_loan AS SELECT * FROM ov")
    OUT.mkdir(exist_ok=True)
    gap.round(4).to_csv(OUT / "overlay_gap.csv", index=False)
    summary.round(3).to_csv(OUT / "overlay_summary.csv", index=False)
    vin.round(3).to_csv(OUT / "overlay_vintage.csv", index=False)
    print(f"\nSaved table el_overlay_loan and 3 CSVs in {OUT}/")
    con.close()


if __name__ == "__main__":
    main()
