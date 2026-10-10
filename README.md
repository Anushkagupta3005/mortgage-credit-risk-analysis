# Credit Card Fraud Detection — Cost-Sensitive Threshold Optimization

A production-minded fraud detector that goes beyond model accuracy to optimize the
**business decision**: which transactions to block. Built on 284,807 real European card
transactions (0.17% fraud rate), the project pairs a tuned LightGBM ranker with a
dollar-cost-optimized decision threshold — cutting expected fraud cost by **~20%** versus a
naive 0.50 cutoff, validated on a locked holdout set opened exactly once.

**Interactive dashboard:** open `fraud-dashboard.html` (or the GitHub Pages link) to explore
the cost/threshold trade-off live — drag the threshold and watch recall, false alarms, and
dollar cost respond in real time.

## Results at a glance

| Metric | Value |
|---|---|
| Baseline CV PR-AUC (Logistic Regression) | 0.7615 |
| Tuned LightGBM CV PR-AUC | 0.8468 |
| Final test PR-AUC (holdout, opened once) | 0.8745 |
| Cost-optimal threshold | 0.044 |
| Test recall (frauds caught) | 84.7% (83/98) |
| Test precision | 78.3% |
| Expected cost — optimized threshold | $1,730 |
| Expected cost — default 0.50 threshold | $2,150 |
| **Cost reduction** | **~20%** |

Choosing the cost-optimal threshold caught **6 more frauds** and saved **~$420 (~20%)** on the
holdout set versus the default cutoff.

## Dataset

| Property | Detail |
|---|---|
| Source | [Kaggle / ULB — Credit Card Fraud Detection](https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud) |
| Transactions | 284,807 over 48 hours (European cardholders, Sept 2013) |
| Frauds | 492 (0.17%) — extreme class imbalance |
| Features | V1-V28 (PCA-anonymized), Time, Amount |

## Approach

### Evaluation design
- **Metric:** PR-AUC (area under the precision-recall curve) — the meaningful metric when
  99.83% of the data is one class. Accuracy is useless here: a model that predicts "legit" on
  every transaction scores 99.83% and catches zero fraud.
- **Leak-safe harness:** an 80/20 stratified split is locked at the start. All model
  selection, tuning, and threshold optimization happen via stratified cross-validation on the
  training set only. The test set is sealed and opened exactly once, at the end, for final
  reporting.

### Resampling — what didn't work (and why it matters)
- SMOTE and random undersampling were tested inside the CV folds against a class-weighted
  baseline. None beat the plain baseline on PR-AUC (SMOTE 0.7528, undersample 0.6623,
  class_weight 0.7571, vs baseline 0.7615). Resampling changes the training distribution but
  doesn't improve a well-regularized model's ability to *rank* fraud above legitimate
  transactions. The real lever is the decision threshold, not the class balance.

### Model selection and tuning
- **Baseline:** Logistic Regression with balanced class weights — CV PR-AUC **0.7615**.
- **LightGBM:** tuned via `RandomizedSearchCV` (20 iterations, 3-fold stratified CV, PR-AUC
  scoring) — CV PR-AUC **0.8468**, test PR-AUC **0.8745**.
- **Deliberately did not use `scale_pos_weight`:** at this imbalance level (~577:1) it
  degrades ranking quality rather than improving it. Imbalance is handled where it belongs —
  through the PR-AUC objective and the decision threshold — not by reweighting the loss.

### Cost-sensitive threshold optimization
- The standard 0.50 cutoff assumes false negatives and false positives cost the same — in
  fraud detection they don't. A missed fraud costs real money; a false alarm costs a review.
- Business costs are made explicit: **$100 per missed fraud** (FN) and **$10 per false alarm**
  (FP) — a 10:1 asymmetry. (Illustrative assumptions; the method generalizes to any cost ratio.)
- Thresholds are swept over out-of-fold training scores; the cost-minimizing cutoff
  (**0.044**) is selected and locked *before* the test set is touched.

### Interpretability (SHAP)
- TreeSHAP on the final model gives exact Shapley-value explanations.
- **Global:** a handful of PCA components (V14, V4, V12, V10, V17) dominate fraud predictions;
  Time ranks near the bottom (a logging artifact unlikely to generalize); Amount lands
  mid-pack — modest predictive importance but central to the cost function, illustrating that
  importance-to-prediction and importance-to-business are distinct.
- **Local:** single-transaction waterfall decompositions show which features pushed a specific
  prediction past the decision threshold — the kind of explanation a fraud analyst or regulator
  needs for a blocked card.

## Honest limitations
- **Anonymized features cap interpretability.** V1-V28 are PCA components, so SHAP reveals
  *which* component drives a decision but not *what* real-world signal it represents. No domain
  feature engineering is possible.
- **Single time window.** The data is one 48-hour slice from 2013. Fraud patterns drift, so
  this model would need regular retraining to stay useful in production.
- **Random split, not temporal.** Train/test were split randomly. A production setup should
  validate on a *later* period than it trains on, since future transactions can't be used to
  catch past ones — random splitting slightly flatters the reported numbers.
- **Costs are assumptions.** The $100/$10 figures are illustrative. The optimal threshold
  depends on them; real deployment would use the business's actual cost of a missed fraud vs. a
  false decline.

## Reproducibility
- `random_state=42` used throughout all splits, CV folds, and model fits.
- Test set constructed once and opened once — no iterative tuning on the holdout.
- Full code in the accompanying notebook.

## Repository structure

```
.
├── credit-card-fraud.ipynb   # end-to-end notebook (EDA -> model -> threshold -> SHAP)
├── fraud-dashboard.html      # self-contained interactive dashboard (open in any browser)
├── README.md
└── requirements.txt
```

## How to run

1. Download the dataset from
   [Kaggle](https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud) and place `creditcard.csv`
   next to the notebook (or point the load cell at your path).
2. Install dependencies: `pip install -r requirements.txt`
3. Open `credit-card-fraud.ipynb` and run all cells top to bottom. The LightGBM search takes a
   few minutes; seeds are fixed, so results are reproducible.
4. To explore the results interactively, open `fraud-dashboard.html` in any browser — no
   install or server needed.

## Tools
Python · scikit-learn · LightGBM · imbalanced-learn · SHAP · pandas · NumPy · Matplotlib · seaborn
