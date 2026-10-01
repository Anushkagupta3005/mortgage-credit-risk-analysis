"""
Session 4, Step 3: Risk driver charts.

Reads outputs/risk_drivers.csv and outputs/risk_grid.csv (no database needed).

  outputs/risk_drivers_lift.png : small multiples, lift by bucket for 6 drivers
                                   (grey = normal, red = 2x+ portfolio risk)
  outputs/risk_grid_heatmap.png : FICO x LTV grid, D90+ within 24m

Run:  python plot_risk_drivers.py
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

OUT = Path("outputs")
MIN_LOANS = 500  # hide tiny buckets (e.g. DTI >50 has 17 loans); keeps FICO <620 (906)

SURFACE, TEXT, TEXT_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
BAR, WARN = "#9a9993", "#e34948"
BLUE_RAMP = ["#eef4fc", "#b9d3f3", "#6fa3e6", "#2a78d6", "#174f97"]

ORDER = {
    "FICO": ["<620", "620-679", "680-719", "720-759", "760-799", "800+"],
    "LTV": ["<=60", "61-70", "71-80", "81-90", "91-95", "96-100", ">100 (HARP)"],
    "DTI": ["<20", "20-29", "30-36", "37-43", "44-50", ">50"],
    "Borrowers": ["1 borrower", "2+ borrowers"],
    "First-time buyer": ["Yes", "No"],
    "Loan purpose": ["Purchase", "Cash-out refi", "No-cash-out refi", "Refi (unspecified)"],
}


def style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=TEXT_2, length=0)


def lift_chart(df):
    fig, axes = plt.subplots(2, 3, figsize=(13, 7.6), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    xmax = df[df.loans >= MIN_LOANS].lift.max() * 1.25

    for ax, (driver, order) in zip(axes.flat, ORDER.items()):
        d = df[(df.driver == driver) & (df.loans >= MIN_LOANS)]
        d = d.set_index("bucket").reindex([b for b in order if b in set(d.bucket)])
        y = np.arange(len(d))[::-1]
        colors = [WARN if v >= 2 else BAR for v in d.lift]
        ax.barh(y, d.lift, color=colors, height=0.62)
        for yi, (lift, pct) in zip(y, zip(d.lift, d.d90_24m_pct)):
            ax.text(lift + xmax * 0.015, yi, f"{lift:.1f}x  ({pct:.1f}%)",
                    va="center", fontsize=8.5, color=TEXT)
        ax.axvline(1, color=TEXT_2, lw=0.9, ls=(0, (3, 3)))
        ax.set_yticks(y, d.index, fontsize=9, color=TEXT)
        ax.set_xlim(0, xmax)
        ax.set_xticks([])
        ax.set_title(driver, loc="left", fontsize=11, fontweight="bold", color=TEXT)
        style(ax)

    fig.suptitle("Credit score, equity and debt load drive mortgage risk; "
                 "low-FICO loans are 4x+ riskier",
                 x=0.012, ha="left", fontsize=14, fontweight="bold", color=TEXT, y=0.995)
    fig.text(0.012, 0.935,
             "Lift = bucket's D90+ rate within 24 months / portfolio rate (dashed line = 1.0x). "
             "Red = 2x or more. Label shows lift and the bucket's D90+ rate.",
             fontsize=9.5, color=TEXT_2)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(OUT / "risk_drivers_lift.png", facecolor=SURFACE)
    print(f"Saved {OUT / 'risk_drivers_lift.png'}")


def grid_chart(g):
    p = g.pivot(index="fico_band", columns="ltv_band", values="d90_24m_pct")
    n = g.pivot(index="fico_band", columns="ltv_band", values="loans")
    worst, best = p.values.max(), p.values.min()

    fig, ax = plt.subplots(figsize=(9, 6.2), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    cmap = LinearSegmentedColormap.from_list("blues", BLUE_RAMP)
    ax.imshow(p.values, cmap=cmap, vmin=0, vmax=worst, aspect="auto")

    for i in range(p.shape[0]):
        for j in range(p.shape[1]):
            v = p.values[i, j]
            ink = "#ffffff" if v > worst * 0.55 else TEXT
            ax.text(j, i - 0.08, f"{v:.2f}%", ha="center", va="center",
                    fontsize=13, fontweight="bold", color=ink)
            ax.text(j, i + 0.22, f"{int(n.values[i, j]):,} loans", ha="center",
                    va="center", fontsize=8.5, color=ink)
    # thin surface-colored gaps between cells
    for k in range(1, p.shape[0]):
        ax.axhline(k - 0.5, color=SURFACE, lw=2)
    for k in range(1, p.shape[1]):
        ax.axvline(k - 0.5, color=SURFACE, lw=2)

    ax.set_xticks(range(p.shape[1]), [c.split(": ")[1] for c in p.columns], color=TEXT)
    ax.set_yticks(range(p.shape[0]), [r.split(": ")[1] for r in p.index], color=TEXT)
    ax.set_xlabel("Original LTV (%)  ->  less equity", color=TEXT_2)
    ax.set_ylabel("FICO score", color=TEXT_2)
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)

    ax.set_title(f"Low FICO + thin equity: {worst:.1f}% go 90+ days late, "
                 f"~{worst / best:.0f}x the safest cell",
                 loc="left", fontsize=13, fontweight="bold", color=TEXT, pad=24)
    ax.text(0, 1.02, "D90+ within 24 months by FICO band and LTV band  |  "
            "Freddie Mac SFLLD sample, 250K loans", transform=ax.transAxes,
            fontsize=9, color=TEXT_2)
    fig.tight_layout()
    fig.savefig(OUT / "risk_grid_heatmap.png", facecolor=SURFACE)
    print(f"Saved {OUT / 'risk_grid_heatmap.png'}")


def main():
    lift_chart(pd.read_csv(OUT / "risk_drivers.csv"))
    grid_chart(pd.read_csv(OUT / "risk_grid.csv"))


if __name__ == "__main__":
    main()
