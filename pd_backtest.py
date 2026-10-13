"""
Session 8, Step 3: Backtest the PD model (did its predictions come true?).

Reads data/pd_scored.parquet (from pd_logit.py) and data/pd_model_data.parquet: every loan-snapshot with its
predicted 12-month PD and what actually happened.

Checks (the core of a bank's model validation):
  1. Calibration by risk decile (test):  predicted vs actual bad rate per 10% bucket
  2. Calibration over time:              predicted vs actual for every snapshot
  3. Calibration by vintage (test)
  4. Ranking on the hardest loans:       AUC among loans that are current AND were
                                         never late in the last 12 months
                                         (no delinquency history to lean on)
  5. Recalibration (point-in-time fix):  scale the model's odds so its average
                                         matches the 2024-07 snapshot, then check it
                                         on 2025-03, which it has not seen

Outputs:
  outputs/pd_backtest_deciles.csv, outputs/pd_backtest_time.csv,
  outputs/pd_backtest_vintage.csv
  outputs/pd_calibration.png       predicted vs actual by decile (test)
  outputs/pd_backtest_time.png     predicted vs actual by snapshot

Run:  python pd_backtest.py
"""

from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

SCORED = Path("data") / "pd_scored.parquet"
FEATURES = Path("data") / "pd_model_data.parquet"
OUT = Path("outputs")
SURFACE, TEXT, TEXT_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
PRED, ACT, WARN = "#2a78d6", "#9a9993", "#e34948"


def style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=TEXT_2, length=0)
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.set_axisbelow(True)


def summary(g):
    return pd.Series({"loans": len(g), "bad": int(g.bad.sum()),
                      "actual_pct": 100 * g.bad.mean(),
                      "predicted_pct": 100 * g.pd_logit.mean()})


def tidy(t):
    t[["loans", "bad"]] = t[["loans", "bad"]].astype(int)
    t["ratio_actual_to_pred"] = t.actual_pct / t.predicted_pct
    return t


def recalibrate(p, factor):
    """Multiply the odds by a constant (= shift the logit intercept). Keeps the ranking."""
    odds = p / (1 - p) * factor
    return odds / (1 + odds)


def main():
    df = duckdb.sql(f"""
        SELECT s.*, (f.worst_dq_prev12 = 0)::INT AS never_late_12m
        FROM '{SCORED}' s
        JOIN '{FEATURES}' f USING (snap, loan_seq_no)
    """).df()
    df["snap"] = pd.to_datetime(df.snap)
    test = df[df.split == "test"].copy()
    print(f"Loaded {len(df):,} scored rows ({len(test):,} out-of-time test rows)")

    # 1. deciles ------------------------------------------------------------
    test["decile"] = pd.qcut(test.pd_logit.rank(method="first"), 10, labels=range(1, 11))
    dec = test.groupby("decile", observed=True).apply(summary, include_groups=False).reset_index()
    dec = tidy(dec)
    top = dec.iloc[-1]
    print("\n== 1. Calibration by risk decile (test, 1 = safest, 10 = riskiest) ==")
    print(dec.round(3).to_string(index=False))
    print(f"Riskiest 10% of loans hold {100 * top.bad / dec.bad.sum():.0f}% of all bad loans")

    # 2. over time ----------------------------------------------------------
    tim = df.groupby(["snap", "split"]).apply(summary, include_groups=False).reset_index()
    tim = tidy(tim)
    tim["snapshot"] = tim.snap.dt.strftime("%Y-%m")
    print("\n== 2. Predicted vs actual by snapshot ==")
    print(tim.drop(columns="snap").round(3).to_string(index=False))

    # 3. vintage ------------------------------------------------------------
    vin = test.groupby("vintage").apply(summary, include_groups=False).reset_index()
    vin = tidy(vin)
    print("\n== 3. Predicted vs actual by vintage (test) ==")
    print(vin.round(3).to_string(index=False))

    # 4. hardest loans ------------------------------------------------------
    print("\n== 4. Ranking power (AUC, test) ==")
    clean = test[(test.dq_now == 0) & (test.never_late_12m == 1)]
    for name, sub in [("All test loans", test), ("Current AND never late in last 12m", clean)]:
        print(f"  {name:<38} loans {len(sub):>8,}  bad {100 * sub.bad.mean():.2f}%  "
              f"AUC model {roc_auc_score(sub.bad, sub.pd_logit):.3f}  "
              f"AUC old segment PD {roc_auc_score(sub.bad, sub.pd_segment):.3f}")

    # 5. recalibration ------------------------------------------------------
    snaps = sorted(test.snap.unique())
    if len(snaps) >= 2:
        cal, val = test[test.snap == snaps[0]], test[test.snap == snaps[-1]]
        p_mean, a_mean = cal.pd_logit.mean(), cal.bad.mean()
        factor = (a_mean / (1 - a_mean)) / (p_mean / (1 - p_mean))
        val_p = recalibrate(val.pd_logit, factor)
        print(f"\n== 5. Recalibration: odds x {factor:.2f}, set on {pd.Timestamp(snaps[0]):%Y-%m}, "
              f"checked on {pd.Timestamp(snaps[-1]):%Y-%m} ==")
        print(f"  {pd.Timestamp(snaps[-1]):%Y-%m}: actual {100 * val.bad.mean():.3f}%  |  "
              f"model as trained {100 * val.pd_logit.mean():.3f}%  |  "
              f"recalibrated {100 * val_p.mean():.3f}%  "
              f"(AUC unchanged: {roc_auc_score(val.bad, val_p):.3f})")

    # charts ----------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(10, 5.6), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    style(ax)
    x = np.arange(len(dec))
    ax.bar(x - 0.2, dec.predicted_pct, 0.4, color=PRED, label="Predicted by model")
    ax.bar(x + 0.2, dec.actual_pct, 0.4, color=ACT, label="Actual")
    ax.set_xticks(x, [str(d) for d in dec.decile])
    ax.set_xlabel("Risk decile (1 = safest 10% of loans, 10 = riskiest)", color=TEXT_2)
    ax.set_ylabel("% going 90+ days late within 12 months", color=TEXT_2)
    ax.legend(frameon=False, labelcolor=TEXT_2)
    ax.set_title(f"The model ranks risk well: the riskiest 10% of loans hold "
                 f"{100 * top.bad / dec.bad.sum():.0f}% of bad loans",
                 loc="left", fontsize=12.5, fontweight="bold", color=TEXT, pad=26)
    ax.text(0, 1.02, "Out-of-time test: 2024-07 and 2025-03 snapshots, never seen in training  |  "
            "Logistic regression PD model", transform=ax.transAxes, fontsize=9, color=TEXT_2)
    fig.tight_layout()
    fig.savefig(OUT / "pd_calibration.png", facecolor=SURFACE)

    fig, ax = plt.subplots(figsize=(10, 5.6), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    style(ax)
    x = np.arange(len(tim))
    ax.plot(x, tim.predicted_pct, color=PRED, lw=2.4, marker="o", label="Predicted by model")
    ax.plot(x, tim.actual_pct, color=ACT, lw=2.4, marker="o", label="Actual")
    ax.set_ylim(0, max(tim.actual_pct.max(), tim.predicted_pct.max()) * 1.25)
    n_train = int((tim.split == "train").sum())
    ax.axvspan(n_train - 0.5, len(tim) - 0.5, color=WARN, alpha=0.06)
    ax.text(n_train - 0.35, ax.get_ylim()[1] * 0.97, "out-of-time test", color=WARN,
            fontsize=9, va="top")
    ax.set_xticks(x, tim.snapshot)
    ax.set_ylabel("% going 90+ days late within 12 months", color=TEXT_2)
    ax.legend(frameon=False, labelcolor=TEXT_2, loc="lower left")
    last = tim.iloc[-1]
    drift = "above" if last.actual_pct > last.predicted_pct else "below"
    ax.set_title(f"Out of time, actual defaults ran {drift} the model: {last.snapshot} actual "
                 f"{last.actual_pct:.2f}% vs {last.predicted_pct:.2f}% predicted",
                 loc="left", fontsize=12.5, fontweight="bold", color=TEXT, pad=26)
    ax.text(0, 1.02, "Model trained on 2016-2023 snapshots (COVID excluded)  |  "
            "a recalibration fixes the level without changing the ranking",
            transform=ax.transAxes, fontsize=9, color=TEXT_2)
    fig.tight_layout()
    fig.savefig(OUT / "pd_backtest_time.png", facecolor=SURFACE)

    dec.to_csv(OUT / "pd_backtest_deciles.csv", index=False)
    tim.drop(columns="snap").to_csv(OUT / "pd_backtest_time.csv", index=False)
    vin.to_csv(OUT / "pd_backtest_vintage.csv", index=False)
    print(f"\nSaved {OUT / 'pd_calibration.png'}, {OUT / 'pd_backtest_time.png'} and 3 CSVs")


if __name__ == "__main__":
    main()
