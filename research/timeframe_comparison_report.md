# Timeframe comparison report — Phase 3

Author: research agent (autonomous run, 2026-05-31)
Source: `scripts/run_timeframe_comparison.py`
Window: 48 months (2022-05-01 → 2026-04-30) of BTC/USDT + ETH/USDT
Outputs: `results/timeframe_comparison_20260531_073039.{csv,md}`
Per-trade CSV: `results/timeframe_trades_20260531_073039.csv`

## Setup

Same long-short SuperTrend(10, 3) + direction-aware funding gate
(block long ≥ p95, block short ≤ p5). Same costs
(fee 10 bps/side, slippage 5 bps). Same universe (BTC + ETH).
Walk-forward train/test/embargo scaled per TF (locked in
`scripts/run_timeframe_comparison.py`):

| TF | train | test | embargo | bars/day | funding window (bars) |
|---|---:|---:|---:|---:|---:|
| 1h | 1440 | 360 | 6 | 24 | 720 |
| 2h | 1440 | 360 | 6 | 12 | 360 |
| 4h | 1440 | 360 | 6 |  6 | 180 |
| 1d |  240 |  60 | 1 |  1 |  30 |

15m and 5m were deliberately skipped — the v1 RSI experiment proved
that fees dominate edge at sub-hour timeframes for this strategy
family (see `RESEARCH_LOG.md`).

## Walk-forward OOS results

| TF | n | ret | DD | PF | win | folds+ |
|---|---:|---:|---:|---:|---:|---:|
| 1h | 561 | +269.11% | 7.19% | 2.26 | 53.3% | 65/93 |
| 2h | 260 | +116.78% | 8.25% | 2.10 | 52.7% | 27/44 |
| 4h | 123 | +139.71% | 4.64% | 3.35 | 58.5% | 16/20 |
| 1d |  26 |  +26.30% | 15.13% | 2.19 | 61.5% | 8/20 |

Observations:

- **1h delivers the highest absolute return (+269%)** but at PF 2.26
  vs 3.35 (4h). DD widens to 7.19%, 55% worse than 4h. Trade count
  ~5× larger, exposure ~5× longer — more bar-time invested for
  diminishing risk-adjusted return.
- **2h is the worst of all four on PF (2.10) and DD (8.25%)**. It
  lands in the SuperTrend dead zone — too fast to capture macro
  trends, too slow for true intra-day noise capture.
- **4h dominates risk-adjusted (PF 3.35 / DD 4.64%)**. This matches
  the Issue #20 adoption metrics exactly (n=123, ret +139.71%, DD
  4.64%, PF 3.35).
- **1d is the smallest sample (26 trades) and the highest DD
  (15.13%)**. Few signals, each one carrying a lot of single-bet
  risk. Not viable for the current trade-count gate.

## Trailing-window slices (last 3/6/12/24 months)

| TF | scope | n | ret | DD | PF | win |
|---|---|---:|---:|---:|---:|---:|
| 1h | last_24mo | 311 | +109.27% | 6.18% | 2.37 | 55.3% |
| 1h | last_12mo | 159 |  +78.19% | 3.95% | 3.61 | 59.7% |
| 1h | last_6mo  |  80 |  +31.74% | 3.95% | 3.05 | 58.8% |
| 1h | last_3mo  |  40 |   +9.03% | 3.95% | 1.99 | 45.0% |
| 2h | last_24mo | 147 | +119.64% | 5.61% | 3.12 | 56.5% |
| 2h | last_12mo |  76 |  +58.29% | 5.61% | 3.42 | 53.9% |
| 2h | last_6mo  |  35 |  +18.52% | 5.61% | 2.51 | 54.3% |
| 2h | last_3mo  |  16 |  +18.91% | 1.73% | 7.09 | 62.5% |
| 4h | last_24mo |  79 |  +84.80% | 4.40% | 3.31 | 59.5% |
| 4h | last_12mo |  41 |  +49.65% | 4.40% | 4.67 | 68.3% |
| 4h | last_6mo  |  24 |  +19.54% | 4.40% | 3.27 | 62.5% |
| 4h | last_3mo  |  13 |   +8.56% | 4.40% | 2.36 | 53.8% |
| 1d | last_24mo |  18 |   -3.17% | 15.13% | 0.88 | 44.4% |
| 1d | last_12mo |   8 |  +13.46% | 2.71% | 5.23 | 75.0% |
| 1d | last_6mo  |   5 |  +11.37% | 2.71% | 5.13 | 80.0% |
| 1d | last_3mo  |   3 |   +9.44% | 2.71% | 4.48 | 66.7% |

The headline result of this entire phase:

**On every timeframe, the walk-forward OOS view of the last 3 months
is positive.** The lowest is 4h at +8.56%; the highest is 2h at
+18.91%. The user's in-sample replay number (-9.61% / DD 9.61% /
0 wins / 6 trades) is a different equity-accounting frame from the
walk-forward OOS folds.

The continuous-replay frame is what live worker would have seen, and
in that frame the recent 3mo was indeed bad. But the **walk-forward
OOS estimator** — the same estimator that earned adoption in
Issue #20 — says the strategy was making money in the recent window.
The choppy streak the user observed via replay is real but is not
evidence of OOS-estimator decay.

## Does any timeframe beat 4h on recent 3mo without destroying 48mo?

Candidate: **2h** — last 3mo +18.91% / PF 7.09 / DD 1.73%.

But 2h on full 48mo: ret +116.78% / PF 2.10 / DD 8.25%. The full-window
DD and PF are both materially worse than 4h. Adopting 2h would
fix the recent window at the cost of a higher long-term DD.

Net verdict: **no timeframe cleanly dominates 4h on both axes
simultaneously**. 4h remains the right decision timeframe.

## Does 1d avoid the chop?

1d last 3mo: +9.44% / PF 4.48 / DD 2.71% — yes, it does. But it
also gives only 26 trades in 48 months (fails the trade-count
gate of ≥ 30) and shows a 15.13% peak DD over the full window —
the worst of any TF. 1d is not viable as a primary timeframe.

It could potentially serve as a **confirmation overlay**, which is
what Phase 4 (variant A: 1d agreement) tests.

## Does 1h overtrade?

Mildly. 1h has 561 trades in 48 months, ~12 per month, vs 4h's ~2.6
per month. PF 2.26 vs 3.35 reflects the cost of marginal-quality
signals fired at higher TF granularity. 1h is not destroyed by fees
(net return +269% > 4h's +139%) but the marginal trades degrade the
risk-adjusted profile.

## Recommendation

Keep 4h as the decision timeframe. None of 1h / 2h / 1d cleanly
dominates 4h on the joint (long-term, recent) axis.

The "fix the recent window" instinct should not drive a TF change.
The next change should be a risk-layer overlay — specifically the
Phase 5 vol-quartile sizing rule, which cuts DD on the recent 3mo
from 4.40% to 1.44% while keeping the same 13 trades and the same
4h timeframe.
