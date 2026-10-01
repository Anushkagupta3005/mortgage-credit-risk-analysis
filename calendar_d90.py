"""
Session 3, Step 3: Calendar view of delinquency (vintage curves, turned sideways).

Vintage curves put LOAN AGE on the x-axis. Here we put the CALENDAR MONTH on
the x-axis instead. If COVID was a calendar shock (not a weakness of one
vintage), every vintage's line should jump at the same time: mid-2020.

Metric per (vintage, month):
  d90_rate_pct  = loans currently 90+ days delinquent (or REO) / loans reported
                  that month * 100       -> "how sick is the book right now"
  forb_rate_pct = loans in forbearance that month / loans reported * 100

Outputs:
  table  calendar_d90
  file   outputs/calendar_d90.csv   (for Tableau)
  file   outputs/calendar_d90.png   (for README / memo)

Run:  python calendar_d90.py
"""

from pathlib import Path

import duckdb
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import pandas as pd

DB = "mortgage.duckdb"
OUT = Path("outputs")
MIN_LOANS = 5000  # ignore months where a vintage has too few loans (noisy %)

BUILD = """
CREATE OR REPLACE TABLE calendar_d90 AS
SELECT
    vintage,
    reporting_period                                                  AS month,
    COUNT(*)                                                          AS loans_reported,
    SUM(CASE WHEN dq_months >= 3 OR is_reo_acquisition THEN 1 ELSE 0 END) AS d90_loans,
    SUM(CASE WHEN borrower_assistance_status = 'F' THEN 1 ELSE 0 END) AS forb_loans,
    ROUND(100.0 * SUM(CASE WHEN dq_months >= 3 OR is_reo_acquisition THEN 1 ELSE 0 END)
          / COUNT(*), 3)                                              AS d90_rate_pct,
    ROUND(100.0 * SUM(CASE WHEN borrower_assistance_status = 'F' THEN 1 ELSE 0 END)
          / COUNT(*), 3)                                              AS forb_rate_pct
FROM perf_clean
GROUP BY 1, 2
ORDER BY 1, 2
"""

SURFACE, TEXT, TEXT_2, GRID, BAND = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df", "#f0efeb"
STYLE = {
    2019: ("#2a78d6", 2.4, 5),
    2023: ("#eb6834", 2.4, 4),
    2012: ("#6f6e69", 1.5, 2),
    2016: ("#8f8e88", 1.5, 2),
    2021: ("#b0afa8", 1.5, 2),
}


def show(con, title, sql):
    print(f"\n== {title} ==")
    print(con.execute(sql).fetchdf().to_string(index=False))


def plot(df):
    df = df[df.loans_reported >= MIN_LOANS].copy()
    df["month"] = pd.to_datetime(df["month"])

    fig, ax = plt.subplots(figsize=(11, 6.2), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    ax.axvspan(pd.Timestamp("2020-04-01"), pd.Timestamp("2021-12-01"),
               color=BAND, zorder=0)
    ax.text(pd.Timestamp("2020-05-01"), 0.99, "COVID forbearance period",
            transform=ax.get_xaxis_transform(), va="top",
            fontsize=9.5, color=TEXT_2)

    ends = []
    for v in sorted(df.vintage.unique()):
        d = df[df.vintage == v]
        color, lw, z = STYLE.get(v, ("#8f8e88", 1.5, 2))
        ax.plot(d.month, d.d90_rate_pct, color=color, lw=lw, zorder=z, label=str(v))
        ends.append((v, d.iloc[-1].month, d.iloc[-1].d90_rate_pct))

    # end labels: all vintages finish at similar values, so spread them
    # vertically (min gap) and draw a short leader to the line end
    gap = df.d90_rate_pct.max() * 1.12 * 0.045
    ends.sort(key=lambda e: e[2])
    placed = []
    for v, m, y in ends:
        ly = max(y, placed[-1] + gap) if placed else y
        placed.append(ly)
        ax.annotate(f"{v}  {y:.2f}%", xy=(m, y), xytext=(m + pd.Timedelta(days=120), ly),
                    va="center", fontsize=10, color=TEXT,
                    fontweight="bold" if v in (2019, 2023) else "normal",
                    arrowprops=dict(arrowstyle="-", color=GRID, lw=0.8, relpos=(0, 0.5)))

    ax.set_title("COVID hit every vintage on the books in the same months, "
                 "whatever the loan's age",
                 loc="left", fontsize=14, fontweight="bold", color=TEXT, pad=26)
    ax.text(0, 1.02,
            "% of loans currently 90+ days delinquent, by calendar month  |  "
            "Freddie Mac SFLLD sample, 50K loans per vintage",
            transform=ax.transAxes, fontsize=9.5, color=TEXT_2)

    ax.yaxis.set_major_formatter(mtick.PercentFormatter(decimals=1))
    ax.xaxis.set_major_locator(mdates.YearLocator(2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.set_xlim(df.month.min(), df.month.max() + pd.Timedelta(days=900))
    ax.set_ylim(0, df.d90_rate_pct.max() * 1.12)
    last_tick = mdates.date2num(df.month.max())
    ax.set_xticks([t for t in ax.get_xticks() if t <= last_tick])
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.tick_params(colors=TEXT_2, length=0)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.legend(title="Vintage", frameon=False, loc="upper left",
              bbox_to_anchor=(0, 0.92), fontsize=9, title_fontsize=9,
              labelcolor=TEXT_2)

    fig.tight_layout()
    fig.savefig(OUT / "calendar_d90.png", facecolor=SURFACE)
    print(f"Saved {OUT / 'calendar_d90.png'}")


def main():
    OUT.mkdir(exist_ok=True)
    con = duckdb.connect(DB)
    con.execute("SET preserve_insertion_order = false")
    con.execute(BUILD)
    print("Built table: calendar_d90")

    show(con, f"Peak month per vintage (months with >= {MIN_LOANS} loans)", f"""
        SELECT vintage,
               STRFTIME(ARG_MAX(month, d90_rate_pct), '%Y-%m') AS peak_month,
               MAX(d90_rate_pct)                                AS peak_d90_pct,
               STRFTIME(ARG_MAX(month, forb_rate_pct), '%Y-%m') AS forb_peak_month,
               MAX(forb_rate_pct)                               AS peak_forb_pct
        FROM calendar_d90 WHERE loans_reported >= {MIN_LOANS}
        GROUP BY 1 ORDER BY 1
    """)

    df_all = con.execute("SELECT * FROM calendar_d90").fetchdf()

    print("\n== Current D90+ rate (%) at key dates ==")
    keys = ["2019-12-01", "2020-06-01", "2020-09-01", "2021-06-01",
            "2022-06-01", "2024-06-01", "2026-03-01"]
    k = df_all[(df_all.loans_reported >= MIN_LOANS)
               & (df_all.month.astype(str).isin(keys))]
    pivot = k.pivot(index="month", columns="vintage", values="d90_rate_pct")
    print(pivot.to_string(na_rep=""))

    show(con, "Whole portfolio: current D90+ rate at key dates", """
        SELECT STRFTIME(month, '%Y-%m') AS month,
               SUM(loans_reported)::BIGINT AS loans,
               ROUND(100.0 * SUM(d90_loans) / SUM(loans_reported), 3) AS d90_pct,
               ROUND(100.0 * SUM(forb_loans) / SUM(loans_reported), 3) AS forb_pct
        FROM calendar_d90
        WHERE month IN ('2019-12-01','2020-06-01','2020-09-01','2021-06-01',
                        '2022-06-01','2024-06-01','2026-03-01')
        GROUP BY 1 ORDER BY 1
    """)

    con.execute(f"COPY calendar_d90 TO '{OUT / 'calendar_d90.csv'}' (HEADER)")
    print(f"\nSaved {OUT / 'calendar_d90.csv'}")
    con.close()

    plot(df_all)


if __name__ == "__main__":
    main()
