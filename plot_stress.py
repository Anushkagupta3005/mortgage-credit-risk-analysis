"""
Session 5, Step 5: Stress test charts.

Reads outputs/stress_waterfall.csv and outputs/stress_vintage.csv (no database).

  outputs/stress_waterfall.png : baseline -> stressed EL, one bar per shock
  outputs/stress_vintage.png   : share of book vs share of stressed loss by vintage

Run:  python plot_stress.py
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

OUT = Path("outputs")
SURFACE, TEXT, TEXT_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
BASE, STEP, TOTAL, WARN, BOOK = "#9a9993", "#2a78d6", "#174f97", "#e34948", "#c9c8c2"


def style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=TEXT_2, length=0)


def waterfall(wf, book_bn):
    labels = ["Baseline\n(today)", "Unemployment\n4.5% -> 10%", "Negative\nequity",
              "More late loans\nend in foreclosure", "Lower recovery\n(house prices -30%)",
              "Stressed\ntotal"]
    added = list(wf.added_mn)
    total = wf.el_mn.iloc[-1]
    bottoms = [0] + list(np.cumsum(added)[:-1]) + [0]
    heights = added + [total]
    colors = [BASE] + [STEP] * (len(added) - 1) + [TOTAL]

    fig, ax = plt.subplots(figsize=(11, 6.2), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    style(ax)
    x = np.arange(len(heights))
    ax.bar(x, heights, bottom=bottoms, color=colors, width=0.62)
    for i, (b, h) in enumerate(zip(bottoms, heights)):
        txt = f"${h:,.1f}M" if i in (0, len(heights) - 1) else f"+${h:,.1f}M"
        ax.text(i, b + h + total * 0.015, txt, ha="center", va="bottom",
                fontsize=10.5, fontweight="bold", color=TEXT)
    for i in range(len(heights) - 2):           # thin connectors between bars
        top = bottoms[i] + heights[i]
        ax.plot([i + 0.31, i + 0.69], [top, top], color=TEXT_2, lw=0.8)

    ax.set_xticks(x, labels, fontsize=9.5, color=TEXT)
    ax.set_yticks([])
    ax.set_ylim(0, total * 1.12)
    mult = total / wf.el_mn.iloc[0]
    biggest = int(np.argmax(added[1:])) + 1
    share = 100 * added[biggest] / (total - added[0])
    ax.set_title(f"A Fed-style severe recession raises 12-month expected loss {mult:.0f}x; "
                 f"{share:.0f}% of the increase\ncomes from late loans ending in foreclosure, "
                 "not from more loans going late",
                 loc="left", fontsize=13, fontweight="bold", color=TEXT, pad=30)
    ax.text(0, 1.015, f"Expected loss (\\$M) on a \\${book_bn:.1f}B active mortgage book  |  "
            "Fed 2026 severely adverse scenario  |  Freddie Mac SFLLD sample",
            transform=ax.transAxes, fontsize=9, color=TEXT_2)
    fig.tight_layout()
    fig.savefig(OUT / "stress_waterfall.png", facecolor=SURFACE)
    print(f"Saved {OUT / 'stress_waterfall.png'}")


def vintage(v):
    v = v.sort_values("vintage")
    v["book_pct"] = 100 * v.ead_mn / v.ead_mn.sum()
    v["loss_pct"] = 100 * v.el_stress_mn / v.el_stress_mn.sum()
    worst = v.loc[v.loss_pct.idxmax()]

    fig, ax = plt.subplots(figsize=(11, 6.2), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    style(ax)
    y = np.arange(len(v))[::-1]
    h = 0.34
    ax.barh(y + h / 2 + 0.01, v.book_pct, height=h, color=BOOK, label="Share of book (balance)")
    loss_colors = [WARN if vt == worst.vintage else STEP for vt in v.vintage]
    ax.barh(y - h / 2 - 0.01, v.loss_pct, height=h, color=loss_colors,
            label="Share of stressed expected loss")
    for yi, (b, l, uw) in zip(y, zip(v.book_pct, v.loss_pct, v.pct_underwater)):
        ax.text(b + 0.8, yi + h / 2, f"{b:.0f}%", va="center", fontsize=9, color=TEXT_2)
        ax.text(l + 0.8, yi - h / 2, f"{l:.0f}%   ({uw:.0f}% of loans underwater)",
                va="center", fontsize=9.5, color=TEXT, fontweight="bold")
    ax.set_yticks(y, [str(int(t)) for t in v.vintage], fontsize=11, color=TEXT)
    ax.set_xticks([])
    ax.set_xlim(0, max(v.loss_pct.max(), v.book_pct.max()) * 1.55)
    ax.legend(frameon=False, loc="upper right", fontsize=9, labelcolor=TEXT_2)

    ax.set_title(f"{int(worst.vintage)} loans are {worst.book_pct:.0f}% of the book but "
                 f"{worst.loss_pct:.0f}% of stressed losses: {worst.pct_underwater:.0f}% "
                 "would be underwater\nif house prices fell 30%",
                 loc="left", fontsize=13, fontweight="bold", color=TEXT, pad=30)
    ax.text(0, 1.015, "By origination year (vintage)  |  Fed 2026 severely adverse scenario  |  "
            "Freddie Mac SFLLD sample", transform=ax.transAxes, fontsize=9, color=TEXT_2)
    fig.tight_layout()
    fig.savefig(OUT / "stress_vintage.png", facecolor=SURFACE)
    print(f"Saved {OUT / 'stress_vintage.png'}")


def main():
    v = pd.read_csv(OUT / "stress_vintage.csv")
    waterfall(pd.read_csv(OUT / "stress_waterfall.csv"), v.ead_mn.sum() / 1000)
    vintage(v)


if __name__ == "__main__":
    main()
