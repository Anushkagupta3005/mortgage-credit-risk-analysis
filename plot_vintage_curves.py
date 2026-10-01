"""
Session 3, Step 2: Plot vintage curves -> outputs/vintage_curves.png

Reads outputs/vintage_curves.csv (made by vintage_curves.py), so it does not
touch the database. Design choice ("focus + context"):
  - 2019 (COVID) and 2023 (high-rate era) are the story -> strong colors
  - other vintages are context -> greys
  - every line is labelled at its end, so color is never the only identifier

Run:  python plot_vintage_curves.py
"""

from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import pandas as pd

CSV = Path("outputs/vintage_curves.csv")
PNG = Path("outputs/vintage_curves.png")

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e4e3df"

STYLE = {  # vintage: (color, linewidth, zorder)
    2019: ("#2a78d6", 2.6, 5),   # focus: COVID
    2023: ("#eb6834", 2.6, 4),   # focus: high-rate era
    2012: ("#6f6e69", 1.6, 2),   # context
    2016: ("#8f8e88", 1.6, 2),
    2021: ("#b0afa8", 1.6, 2),
}


def main():
    df = pd.read_csv(CSV)
    at24 = df[df.loan_age == 24].set_index("vintage").cum_d90_pct
    others = at24.drop(2019, errors="ignore")
    ratio = at24.get(2019, float("nan")) / others.median()

    fig, ax = plt.subplots(figsize=(11, 6.2), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    for v in sorted(df.vintage.unique()):
        d = df[df.vintage == v]
        color, lw, z = STYLE.get(v, ("#8f8e88", 1.6, 2))
        ax.plot(d.loan_age, d.cum_d90_pct, color=color, lw=lw, zorder=z,
                solid_capstyle="round", label=str(v))
        last = d.iloc[-1]
        ax.annotate(f"{v}  {last.cum_d90_pct:.1f}%",
                    xy=(last.loan_age, last.cum_d90_pct),
                    xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=10, color=TEXT,
                    fontweight="bold" if v in (2019, 2023) else "normal")

    # call out the COVID jump on the 2019 curve
    d19 = df[df.vintage == 2019]
    if not d19.empty:
        row = d19.iloc[(d19.loan_age - 14).abs().argmin()]
        ax.annotate("COVID-19 (2020):\n2019 loans were only ~1 year old",
                    xy=(row.loan_age, row.cum_d90_pct),
                    xytext=(max(row.loan_age - 8, 2), df.cum_d90_pct.max() * 1.10),
                    fontsize=9.5, color=TEXT_2, va="top",
                    arrowprops=dict(arrowstyle="-", color=TEXT_2, lw=0.8, relpos=(0, 0)))

    ax.set_title(
        f"At the same age, 2019 loans went 90+ days late ~{ratio:.0f}x more often "
        "than other vintages",
        loc="left", fontsize=14, fontweight="bold", color=TEXT, pad=26)
    ax.text(0, 1.02,
            "Cumulative % of loans ever 90+ days delinquent, by months since origination  "
            "|  Freddie Mac SFLLD sample, 50K loans per vintage",
            transform=ax.transAxes, fontsize=9.5, color=TEXT_2)

    ax.set_xlabel("Loan age (months)", color=TEXT_2)
    ax.yaxis.set_major_formatter(mtick.PercentFormatter(decimals=0))
    ax.set_xlim(0, df.loan_age.max() + 22)
    ax.set_ylim(0, df.cum_d90_pct.max() * 1.15)
    ax.set_xticks(range(0, int(df.loan_age.max()) + 1, 12))
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.tick_params(colors=TEXT_2, length=0)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.legend(title="Vintage", frameon=False, loc="lower right",
              fontsize=9, title_fontsize=9, labelcolor=TEXT_2)

    fig.tight_layout()
    PNG.parent.mkdir(exist_ok=True)
    fig.savefig(PNG, facecolor=SURFACE)
    print(f"Saved {PNG}")
    print(f"2019 vs median of other vintages at age 24: {ratio:.1f}x")


if __name__ == "__main__":
    main()
