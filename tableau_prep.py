"""
Session 6, Step 1: Prepare one Tableau-ready Excel workbook.

Tableau Public cannot read DuckDB, so we hand it one Excel file with one tab
per chart. Each tab is tidy (one row per mark) so Tableau needs no joins.

  outputs/tableau/tableau_data.xlsx
    kpi              5 KPI rows for the dark left column (value + context line)
    trend            monthly portfolio trend, long format (for KPI sparklines)
    vintage_curves   cumulative D90+ by loan age per vintage
    calendar_d90     current D90+ rate by calendar month per vintage
    risk_grid        FICO x LTV: D90+ (24m), PD (12m) and EL (bps) per cell
    el_vintage       expected loss by vintage
    stress_waterfall baseline -> stressed EL steps
    stress_vintage   share of book vs share of stressed loss
    roll_rate        transition matrix, normal vs COVID

Run:  python tableau_prep.py   (after Sessions 2-5 scripts have run)
"""

from pathlib import Path

import duckdb
import pandas as pd

DB = "mortgage.duckdb"
OUT = Path("outputs")
TAB = OUT / "tableau"


def main():
    TAB.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(DB)
    con.execute("SET preserve_insertion_order = false")
    con.execute("SET memory_limit = '4GB'")

    # ---- monthly portfolio trend (one pass over perf) ---------------------------
    print("Building monthly portfolio trend...")
    trend = con.execute("""
        SELECT reporting_period                                          AS month,
               COUNT(*)                                                  AS active_loans,
               SUM(current_upb) / 1e9                                    AS balance_bn,
               100.0 * AVG((dq_months >= 3 OR COALESCE(is_reo_acquisition, FALSE))::INT)
                                                                         AS d90_rate_pct,
               100.0 * AVG((dq_months BETWEEN 1 AND 2)::INT)            AS d30_60_rate_pct,
               100.0 * AVG((borrower_assistance_status = 'F')::INT)     AS forbearance_pct
        FROM perf_clean
        WHERE zero_balance_code IS NULL
        GROUP BY 1 ORDER BY 1
    """).fetchdf()
    trend_long = trend.melt(id_vars="month", var_name="metric", value_name="value")

    cal = pd.read_csv(OUT / "calendar_d90.csv", parse_dates=["month"])
    v23 = cal[(cal.vintage == 2023) & (cal.loans_reported >= 5000)][["month", "d90_rate_pct"]]
    trend_long = pd.concat([trend_long,
                            v23.assign(metric="d90_rate_2023_vintage_pct")
                               .rename(columns={"d90_rate_pct": "value"})])

    # ---- KPI values ---------------------------------------------------------------
    last = trend.iloc[-1]
    prior = trend[trend.month <= last.month - pd.DateOffset(months=24)].iloc[-1]
    el = con.execute("""
        SELECT SUM(el_pit) AS el, SUM(ead) AS ead,
               SUM(el_pit) FILTER (WHERE already_d90) / SUM(el_pit) AS late_share,
               SUM(already_d90::INT) AS late_loans
        FROM el_loan
    """).fetchdf().iloc[0]
    wf = pd.read_csv(OUT / "stress_waterfall.csv")
    sv = pd.read_csv(OUT / "stress_vintage.csv")
    stressed = wf.el_mn.iloc[-1]
    share23 = 100 * sv.loc[sv.vintage == 2023, "el_stress_mn"].iloc[0] / sv.el_stress_mn.sum()
    uw23 = sv.loc[sv.vintage == 2023, "pct_underwater"].iloc[0]
    v23_now = v23.d90_rate_pct.iloc[-1]
    v23_1y = v23[v23.month <= v23.month.max() - pd.DateOffset(months=12)].d90_rate_pct.iloc[-1]
    others = cal[(cal.month == cal.month.max()) & (cal.vintage != 2023)].d90_rate_pct.max()
    asof = last.month.strftime("%b %Y")

    kpi = pd.DataFrame([
        (1, "Active book", f"${last.balance_bn:.1f}B",
         f"{int(last.active_loans):,} loans as of {asof}", 0, "balance_bn"),
        (2, "Loans 90+ days late", f"{last.d90_rate_pct:.2f}%",
         f"{last.d90_rate_pct / prior.d90_rate_pct:.1f}x two years ago "
         f"({prior.d90_rate_pct:.2f}%)", int(last.d90_rate_pct > prior.d90_rate_pct),
         "d90_rate_pct"),
        (3, "2023 vintage, 90+ late", f"{v23_now:.2f}%",
         f"{'Highest of all vintages' if v23_now > others else 'Vintage max'} "
         f"(next: {others:.2f}%); {'up' if v23_now > v23_1y else 'down'} from "
         f"{v23_1y:.2f}% a year ago", int(v23_now > others),
         "d90_rate_2023_vintage_pct"),
        (4, "Expected loss, next 12m", f"${el.el / 1e6:.1f}M",
         f"{1e4 * el.el / el.ead:.1f} bps; {100 * el.late_share:.0f}% from "
         f"{int(el.late_loans):,} loans already 90+ late", 0, ""),
        (5, "Loss in a Fed severe recession", f"${stressed:.1f}M",
         f"{stressed / wf.el_mn.iloc[0]:.0f}x baseline; 2023 loans = {share23:.0f}% "
         f"({uw23:.0f}% go underwater)", 1, ""),
    ], columns=["kpi_order", "kpi", "value", "context", "is_warning", "sparkline_metric"])

    # ---- risk grid: D90 (24m), PD (12m, normal) and EL (bps) in one tab -------------
    grid = pd.read_csv(OUT / "risk_grid.csv")
    pdseg = pd.read_csv(OUT / "pd_segment.csv")
    pdseg = pdseg[pdseg.regime == "Normal"][["fico_band", "ltv_band", "pd_12m_pct"]]
    elseg = pd.read_csv(OUT / "el_segment.csv")[["fico_band", "ltv_band", "ead_mn",
                                                  "pct_of_ead", "pct_of_el", "el_pit_bps"]]
    risk_grid = grid.merge(pdseg, on=["fico_band", "ltv_band"], how="left") \
                    .merge(elseg, on=["fico_band", "ltv_band"], how="left")
    for c in ("fico_band", "ltv_band"):          # '1: <680' -> sort key + clean label
        risk_grid[c + "_order"] = risk_grid[c].str.split(": ").str[0].astype(int)
        risk_grid[c] = risk_grid[c].str.split(": ").str[1]

    wf_t = wf.copy()
    wf_t["step_order"] = range(len(wf_t))
    wf_t["label"] = ["Baseline", "Unemployment 4.5% -> 10%", "Negative equity",
                     "More late loans end in foreclosure",
                     "Lower recovery (house prices -30%)"][:len(wf_t)]
    wf_t["bar_start_mn"] = wf_t.el_mn - wf_t.added_mn
    wf_t = pd.concat([wf_t, pd.DataFrame([{
        "step": "5. Stressed total", "el_mn": stressed, "added_mn": stressed,
        "el_bps": wf.el_bps.iloc[-1], "x_baseline": wf.x_baseline.iloc[-1],
        "step_order": len(wf_t), "label": "Stressed total", "bar_start_mn": 0}])])

    sv_t = sv.copy()
    sv_t["share_of_book_pct"] = 100 * sv_t.ead_mn / sv_t.ead_mn.sum()
    sv_t["share_of_stressed_loss_pct"] = 100 * sv_t.el_stress_mn / sv_t.el_stress_mn.sum()

    tabs = {
        "kpi": kpi,
        "trend": trend_long,
        "vintage_curves": pd.read_csv(OUT / "vintage_curves.csv"),
        "calendar_d90": cal,
        "risk_grid": risk_grid,
        "el_vintage": pd.read_csv(OUT / "el_vintage.csv"),
        "stress_waterfall": wf_t,
        "stress_vintage": sv_t,
        "roll_rate": pd.read_csv(OUT / "roll_rate.csv"),
    }
    path = TAB / "tableau_data.xlsx"
    with pd.ExcelWriter(path) as xw:
        for name, df in tabs.items():
            df.to_excel(xw, sheet_name=name, index=False)
    con.close()

    print(f"\nSaved {path}")
    for name, df in tabs.items():
        print(f"  {name:<17} {len(df):>6} rows   {', '.join(df.columns[:6])}"
              f"{' ...' if len(df.columns) > 6 else ''}")
    print("\n== KPI column ==")
    print(kpi[["kpi", "value", "context"]].to_string(index=False))


if __name__ == "__main__":
    main()
