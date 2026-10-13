"""
Session 8, Step 2: Statistical PD model (logistic regression scorecard).

Reads data/pd_model_data.parquet (from pd_features.py).

Model design (scorecard style, the way bank PD models are usually built):
  - every feature is put into bands (e.g. FICO <620 / 620-659 / ...), with
    "Missing" as its own band, so the effect of each band is directly readable
  - logistic regression (statsmodels) -> coefficients, odds ratios, p-values
  - trained on 2016-2023 snapshots, tested OUT-OF-TIME on 2024-25 snapshots

Compared against:
  - benchmark  : the old segment PD (average bad rate per FICO x LTV cell)
  - challenger : gradient boosting (sklearn) on the same raw features,
                 to check how much accuracy the simple, explainable model gives up

Metrics:
  AUC  : chance the model ranks a random bad loan above a random good loan
  Gini : 2 x AUC - 1 (the number banks usually quote)
  KS   : biggest gap between the score distributions of bad and good loans

Outputs:
  outputs/pd_model_coefficients.csv   bands, coefficients, odds ratios, p-values
  outputs/pd_model_metrics.csv        AUC / Gini / KS, train vs test, all models
  data/pd_scored.parquet              every row with its predicted PD (for Step 3)

Run:  python pd_logit.py
"""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import statsmodels.api as sm
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, roc_curve

DATA = Path("data") / "pd_model_data.parquet"
SCORED = Path("data") / "pd_scored.parquet"
OUT = Path("outputs")


# ---------------------------------------------------------------- banding ----
def band(x, edges, labels, missing="Missing"):
    """Cut a numeric column into labelled bands; NaN -> 'Missing'."""
    out = pd.cut(x, bins=edges, labels=labels, right=True).astype(object)
    return out.where(x.notna(), missing)


def make_bands(df):
    b = pd.DataFrame(index=df.index)
    inf = np.inf
    # <620 merged into <660: only ~2.5K rows, and on its own it came out LESS risky than
    # 620-659 (not monotonic). Scorecard practice: merge thin bands until risk is monotonic.
    b["fico"] = band(df.credit_score, [-inf, 659, 699, 739, 779, inf],
                     ["<660", "660-699", "700-739", "740-779", "780+"])
    b["fico"] = b.fico.replace("Missing", "700-739")            # 85 rows: put in the middle
    b["ltv"] = band(df.orig_ltv, [-inf, 60, 70, 80, 90, 95, 100, inf],
                    ["<=60", "61-70", "71-80", "81-90", "91-95", "96-100", ">100"])
    b["ltv"] = b.ltv.replace("Missing", "71-80")                 # 7 rows
    # >50 merged into 44+: only 31 rows and zero bad loans, which made the coefficient blow
    # up (-19.6) and stopped the model from converging ("perfect separation").
    b["dti"] = band(df.orig_dti, [-inf, 19, 29, 36, 43, inf],
                    ["<20", "20-29", "30-36", "37-43", "44+"])
    b["eltv"] = band(df.eltv, [-inf, 50, 60, 70, 80, 90, inf],
                     ["<=50", "51-60", "61-70", "71-80", "81-90", ">90"])
    b["loan_age"] = band(df.loan_age, [-inf, 12, 24, 48, 84, inf],
                         ["0-12m", "13-24m", "25-48m", "49-84m", "85m+"])
    b["late_now"] = df.dq_now.clip(upper=2).astype(int).map({0: "Current", 1: "30 days", 2: "60 days"})
    b["worst_prev12"] = df.worst_dq_prev12.clip(upper=3).astype(int).map(
        {0: "Never late", 1: "30 days", 2: "60 days", 3: "90+ days"})
    b["purpose"] = df.loan_purpose.fillna("P").map(
        {"P": "Purchase", "C": "Cash-out refi", "N": "No-cash-out refi"}).fillna("Purchase")
    b["first_time"] = df.first_time_homebuyer.fillna("N").map({"Y": "Yes", "N": "No"}).fillna("No")
    b["borrowers"] = np.where(df.num_borrowers.fillna(1) >= 2, "2+", "1")
    b["occupancy"] = df.occupancy_status.fillna("P").map(
        {"P": "Owner", "I": "Investor", "S": "Second home"}).fillna("Owner")
    b["mi"] = np.where(df.mi_pct > 0, "Has MI", "No MI")
    return b


# Reference band for each feature = a common, middle-risk band.
# Every coefficient is then "risk of this band vs the reference band".
REFERENCE = {
    "fico": "740-779", "ltv": "71-80", "dti": "30-36", "eltv": "61-70",
    "loan_age": "25-48m", "late_now": "Current", "worst_prev12": "Never late",
    "purpose": "Purchase", "first_time": "No", "borrowers": "2+",
    "occupancy": "Owner", "mi": "No MI",
}


def design_matrix(bands):
    X = pd.get_dummies(bands, prefix_sep=": ", dtype=float)
    X = X.drop(columns=[f"{k}: {v}" for k, v in REFERENCE.items() if f"{k}: {v}" in X])
    return sm.add_constant(X, has_constant="add")


# ---------------------------------------------------------------- metrics ----
def metrics(y, p):
    auc = roc_auc_score(y, p)
    fpr, tpr, _ = roc_curve(y, p)
    return {"auc": round(auc, 4), "gini": round(2 * auc - 1, 4),
            "ks": round(float(np.max(tpr - fpr)), 4),
            "actual_bad_pct": round(100 * y.mean(), 3),
            "predicted_bad_pct": round(100 * p.mean(), 3)}


def main():
    df = duckdb.sql(f"SELECT * FROM '{DATA}'").df()   # duckdb reads parquet, no pyarrow needed
    print(f"Loaded {len(df):,} rows  |  train {(df.split == 'train').sum():,}  "
          f"test {(df.split == 'test').sum():,}")
    tr, te = df.split == "train", df.split == "test"
    y = df.bad.astype(int)

    bands = make_bands(df)
    X = design_matrix(bands)
    X_tr, X_te = X[tr], X[te].reindex(columns=X.columns, fill_value=0.0)

    # ---------------- 1. logistic regression (the model we will use) --------
    print("\nFitting logistic regression...")
    logit = sm.Logit(y[tr], X_tr).fit(disp=0, maxiter=200)
    df["pd_logit"] = logit.predict(X)

    coef = pd.DataFrame({
        "feature_band": logit.params.index,
        "coefficient": logit.params.values.round(3),
        "odds_ratio": np.exp(logit.params.values).round(2),
        "p_value": logit.pvalues.values.round(4),
    })
    coef["train_rows_in_band"] = [int(X_tr[c].sum()) if c != "const" else len(X_tr)
                                  for c in coef.feature_band]

    # ---------------- 2. benchmark: old segment PD (FICO x LTV cell average) --
    fico4 = pd.cut(df.credit_score, [-np.inf, 679, 739, 779, np.inf],
                   labels=["<680", "680-739", "740-779", "780+"]).astype(str)
    ltv4 = pd.cut(df.orig_ltv, [-np.inf, 70, 80, 90, np.inf],
                  labels=["<=70", "71-80", "81-90", ">90"]).astype(str)
    cell = fico4 + " | " + ltv4
    cell_rate = y[tr].groupby(cell[tr]).mean()
    df["pd_segment"] = cell.map(cell_rate).fillna(y[tr].mean())

    # ---------------- 3. challenger: gradient boosting on raw features -------
    print("Fitting challenger (gradient boosting)...")
    raw = df[["credit_score", "orig_ltv", "orig_dti", "eltv", "loan_age", "dq_now",
              "worst_dq_prev12", "mi_pct", "num_borrowers"]].copy()
    for col in ["loan_purpose", "first_time_homebuyer", "occupancy_status"]:
        raw[col] = df[col].astype("category").cat.codes
    gbm = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05,
                                         max_leaf_nodes=31, random_state=42)
    gbm.fit(raw[tr], y[tr])
    df["pd_gbm"] = gbm.predict_proba(raw)[:, 1]

    # ---------------- metrics ------------------------------------------------
    rows = []
    for name, col in [("Logistic regression (scorecard)", "pd_logit"),
                      ("Benchmark: FICO x LTV segment PD", "pd_segment"),
                      ("Challenger: gradient boosting", "pd_gbm")]:
        for split, mask in [("train", tr), ("test (out-of-time)", te)]:
            rows.append({"model": name, "sample": split, **metrics(y[mask], df.loc[mask, col])})
    met = pd.DataFrame(rows)

    print("\n== Model performance ==")
    print(met.to_string(index=False))

    show = coef[coef.feature_band != "const"].sort_values("odds_ratio", ascending=False)
    print("\n== Logistic regression: odds ratio vs reference band (top 12 riskiest) ==")
    print(show.head(12).to_string(index=False))
    print("\n== ... and the 8 safest ==")
    print(show.tail(8).to_string(index=False))
    print(f"\nReference bands (odds ratio = 1): {REFERENCE}")
    print(f"Pseudo R-squared: {logit.prsquared:.3f}")

    OUT.mkdir(exist_ok=True)
    coef.to_csv(OUT / "pd_model_coefficients.csv", index=False)
    met.to_csv(OUT / "pd_model_metrics.csv", index=False)
    keep = ["snap", "split", "loan_seq_no", "vintage", "bad", "credit_score", "orig_ltv",
            "dq_now", "pd_logit", "pd_segment", "pd_gbm"]
    scored = df[keep]
    duckdb.sql(f"COPY scored TO '{SCORED}' (FORMAT PARQUET)")
    print(f"\nSaved {OUT / 'pd_model_coefficients.csv'}, {OUT / 'pd_model_metrics.csv'}, {SCORED}")


if __name__ == "__main__":
    main()
