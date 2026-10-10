# Risk Memo: Mortgage Portfolio Credit Risk & Stress Test

**To:** Head of Mortgage Credit Risk
**From:** Anushka Gupta
**Date:** October 2026
**Re:** Current credit quality, expected loss and severe-recession exposure of a $25.8B mortgage book

---

## Bottom line

1. **Credit quality is deteriorating, from a low base.** 0.75% of active loans are 90+ days late, **2.1x** the level two years ago (0.36%). This is still far below the COVID peak (2.95%), but the trend is up.
2. **The 2023 vintage drives the risk.** It is 40% of the balance but **54% of 12-month expected loss** and **62% of stressed loss**. Four independent views flag it.
3. **Baseline expected loss is small: $11.7M (4.5 bps).** Half of it sits in just **861 loans already 90+ days late**.
4. **A Fed-style severe recession raises 12-month expected loss 11x to $128.6M (49.8 bps).** Half of the increase comes from late loans **ending in foreclosure** rather than curing. It does not come from more loans going late.

## 1. Portfolio today

| | |
|---|---|
| Active balance (Mar 2026) | $25.8B, 114,860 loans |
| Vintages | 2012, 2016, 2019, 2021, 2023 |
| 90+ days late | 0.75% (2 years ago: 0.36%) |
| 2023 vintage, 90+ days late | **1.24%**, highest of all vintages (next: 0.94%), up from 0.87% a year ago |

The 2021 and 2023 vintages together are **79% of the balance**. Older vintages have mostly prepaid.

## 2. Where the risk comes from

**Vintage.** At the same loan age, the 2023 vintage goes 90+ days late faster than every vintage except 2019. 2019 was hit by COVID in its second year: at 24 months it ran at ~5x the other vintages. The calendar view shows COVID hit **every vintage in the same months**. That was a systemic shock, not an underwriting failure. 2023's weakness shows up in a benign economy, which makes it more concerning.

**Borrower profile.** Risk layers. Lift is a segment's 90+ late rate relative to the whole book:

| Driver | Lift |
|---|---|
| FICO < 620 | 4.45x |
| LTV 96–100 | 2.45x |
| DTI 44–50 | 1.65x |

The combined effect is larger than any single driver. 90+ late rates run from **0.26%** (FICO 780+, LTV ≤ 70) to **7.42%** (FICO < 680, LTV > 90), a **~29x** spread.

**Delinquency dynamics.** In normal times, 15.5% of 30-day-late loans roll to 60 days late, and 37.1% of 60-day-late loans roll to 90+. In COVID both roughly doubled (37.7% and 64.1%). Yet only 0.09% of 90+ loans moved to default per month, versus 1.06% normally, because **forbearance and the foreclosure moratorium stopped the final step**. Without those policies, COVID losses would have been far higher.

## 3. Loss given default

| Segment | Loss severity |
|---|---|
| All loss events (485) | 23.7% |
| Judicial-foreclosure states | 30.8% |
| Non-judicial states | 19.6% |
| With mortgage insurance | 15.9% |
| Without mortgage insurance | 28.8% |

Only **11.8%** of resolved 90+ late loans end in a loss. Most cure, refinance or sell.

## 4. Expected loss (12 months)

EL = PD × LGD × EAD, loan by loan.

- **PD:** 12-month PD by FICO × LTV segment. It averaged **0.70%** through the cycle and **3.54% in the COVID window** (~5x). The latest snapshots run above the through-the-cycle average, so the estimate uses **point-in-time PD**.
- **Result:** **$11.7M (4.5 bps)**, versus 4.0 bps on a through-the-cycle basis.
- **Concentration:** 861 loans already 90+ days late carry **50%** of the EL. The 2023 vintage carries **54%** at 6.0 bps, double the 2021 vintage's 3.1 bps.

## 5. Stress test: Fed 2026 severely adverse scenario

The Fed's scenario has unemployment rising from 4.5% to **10%** and house prices falling **30%**. Each shock was applied in turn:

| Step | 12-month EL | Added |
|---|---|---|
| Baseline | $11.7M | |
| + Unemployment (PD × COVID-observed multiplier per segment) | $34.6M | +$23.0M |
| + Negative equity (underwater loans: PD × 2) | $45.8M | +$11.2M |
| + More late loans end in foreclosure (11.8% → 26.7%) | $104.2M | +$58.3M |
| + Lower recovery on sale (house prices −30%) | **$128.6M** | +$24.5M |

- **2-year cumulative stressed loss:** $227.6M (0.88% of balance).
- **Underwater loans** rise from 369 to **22,927** (20% of the book). In the 2023 vintage the figure is **52%**.
- **Sensitivity:** varying the two softest assumptions gives **$63M–$221M**. Those assumptions are the negative-equity multiplier (1–3x) and the unemployment shock (0.5–1.5x COVID).

**Interpretation.** The biggest lever is not how many borrowers fall behind. It is whether late borrowers can **cure or sell their way out**. When house prices fall, underwater borrowers lose that exit, and late loans turn into foreclosures. COVID did not test this, because house prices rose and forbearance held.

## 6. Recommendations

1. **Put the 2023 vintage on a watchlist.** Track its 90+ late rate monthly against the other vintages at the same loan age. Escalate if it keeps rising while the economy is stable.
2. **Review the high-LTV, lower-FICO origination cell.** FICO < 680 with LTV > 90 runs at 7.4%, ~29x the safest cell. Consider pricing or overlay changes for new loans with both traits.
3. **Prioritise loss mitigation capacity for the 861 loans already 90+ late.** They hold half of baseline EL, and cure-vs-foreclose outcomes are the largest stress driver.
4. **Hold reserves against the point-in-time view, not through-the-cycle.** Current conditions run above the long-run average.
5. **Treat mortgage insurance and judicial-state exposure as severity drivers** in limit setting. Each moves loss severity by 11–13 points.

## 7. Limitations

- Freddie Mac **sample** files (50K loans per vintage), not the full portfolio.
- Stress multipliers come from COVID, when forbearance suppressed defaults. A recession without forbearance would likely be worse than modelled.
- 485 loss events: severity estimates for small segments are noisy.
- No macro regression model. The stress test applies observed multipliers and stated assumptions, all listed in `stress_test.py`.

*Data: Freddie Mac Single-Family Loan-Level Dataset (sample). Scenario: Federal Reserve 2026 supervisory stress test. Code, charts and dashboard: github.com/Anushkagupta3005/mortgage-credit-risk-analysis*
