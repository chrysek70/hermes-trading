# SuperTrend(10, 3) on 48-month history — Report

Follow-up to Issue #4 per Issue #11. Same SuperTrend(10, 3) trend-following
code, same long-only EMA50/200 regime gate, same fees/slippage, same
walk-forward parameters (train 1440 / test 360 / embargo 6). Only change:
data window extended from 24 to 48 months.

**Adoption criteria (unchanged from Issue #4):**

- OOS profit factor > **1.69**
- AND OOS trade count ≥ **30**

**Result: `supertrend_only` PASSES BOTH GATES. Adopted as a research
candidate (not live).** The 24-month finding generalises: doubling the
window roughly quadrupled trade count (9 → 35) without collapsing the
edge (PF 9.02 → 2.24, +13% → +38.66%).

---

## Walk-forward results (48 mo BTC 4h, 20 folds, embargo 6)

| variant | folds | n | OOS return | max DD | PF | Sharpe | win % | folds+ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline` | 20 | 103 | +3.28% | 12.74% | 1.09 | 0.027 | 25.2% | 8/20 |
| **`supertrend_only`** | 20 | **35** | **+38.66%** | 9.63% | **2.24** | **0.266** | **45.7%** | **10/20** |
| `supertrend_plus_routing` | 20 | 20 | +29.56% | **5.95%** | 3.16 | 0.337 | 50.0% | 8/20 |

Costs and resampling identical to all prior experiments in this repo
(fee 10 bps/side, slippage 5 bps, decision TF 4h).

## Adoption decision

- `supertrend_only`: PF 2.24 > 1.69 ✓ and 35 ≥ 30 ✓ → **ADOPTED as
  research candidate**.
- `supertrend_plus_routing`: PF 3.16 > 1.69 ✓ but 20 < 30 ✗ → not
  adopted. Routing materially improves risk profile (5.95% DD, 50% win
  rate, +0.337 Sharpe) but cuts sample below the gate. Worth tracking
  as a sizing-overlay candidate later, but not the headline.
- `baseline` (v2 long-short) on 48mo: PF 1.09, +3.28% return.
  Substantially worse than on the 24mo window (PF 1.69, +8.97%). The
  pullback/breakout setups were partly fitted to the 2024–2026 regime.
  Honest reading: the original 24-month baseline was a favorable
  window for those setups.

## Why this is not a parameter tune

Per Issue #11 hard rules, nothing was changed: SuperTrend stays at
period=10, multiplier=3.0; long-only; same EMA50/200 regime gate; same
fees and slippage; same walk-forward train/test/embargo; same adoption
thresholds (1.69, 30). Only the data window was extended. The strategy
yaml `state/strategy_supertrend.yaml` is byte-for-byte unchanged from
Issue #4.

## What the signal looks like at 48 months

- 35 OOS trades over 20 folds = ~1.75 trades/fold, ~8.75/year — still
  rare, but no longer marginal.
- Fold positivity 10/20 — same as a coin flip on individual folds, but
  the average winning fold outweighs the average losing fold by enough
  to compound +38.66% over the full stitched OOS.
- Average win +3.98%, average loss -1.50%. R-multiple ≈ 2.65 — the
  same favorable asymmetry seen at 24mo (was 2.58 then).
- Max drawdown 9.63% — higher than the 24mo 1.49% reading (which was
  the lowest of any experiment in this repo) but still meaningfully
  below baseline's 12.74% on the same period.
- 12 of 20 folds are SuperTrend-positive or flat (zero-trade); only 5
  folds are net negative.
- Exit reasons: 32 of 35 trades exit on the SuperTrend bearish flip
  (`stop`), 3 on end-of-fold. The exit mechanism is doing its job.

## Comparison to 24-month result

| metric | 24mo | 48mo | direction |
|---|---:|---:|---|
| trades | 9 | 35 | +289% |
| OOS return | +13.00% | +38.66% | +197% |
| max DD | 1.49% | 9.63% | -547% (worse) |
| PF | 9.02 | 2.24 | -75% (still 1.3× over gate) |
| Sharpe | 0.666 | 0.266 | -60% |
| win % | 77.8% | 45.7% | -32 pp |

The 24-month result was clearly under-sampled noise around the true
edge. PF compressed toward something believable; win rate did the same.
The strategy still beats baseline by every metric except absolute draw,
and on 48mo baseline itself is a worse strategy (PF 1.09).

## Why `supertrend_plus_routing` looks tempting but is not adopted

It has the best PF (3.16), best DD (5.95%), best Sharpe (0.337), and
best win rate (50.0%) of the three. It fails the trade-count gate by
10 trades. The locked criteria exist exactly to prevent the move
"adopt the variant that filters to its best subset". If the routing
overlay survives the 30-trade gate after adding ETH (Issue #5) or
further extended history, it becomes a clean sizing-overlay candidate.
Until then it is a side note.

## Adoption scope — what "adopted as research candidate" means

- `state/strategy_supertrend.yaml` is now a tracked research strategy,
  not just a one-off experiment.
- README and ROADMAP record SuperTrend(10, 3) as the first variant
  since v2 to clear both gates.
- **The live worker (`hermes_trading/loop.py`) is NOT modified.** It
  still runs v2 long-short. The user's hard rule was no live wiring.
- Before any consideration of live wiring, SuperTrend should be
  validated on:
  - BTC/ETH combined (Issue #5) — adds independent signal,
    automatically doubles trade count.
  - A subsequent 24-month forward window once available, to check the
    edge survives out-of-window.
- The `routing` variant remains tracked as a candidate sizing overlay
  on top of SuperTrend, conditional on it surviving the 30-trade gate
  in a future, larger sample.

## Artifacts

- `results/supertrend_48mo_comparison_20260530_092716.csv`
- `results/supertrend_48mo_comparison_20260530_092716.md`
- `results/trades_supertrend_48mo_detailed_20260530_092716.csv` (55
  rows — 35 `supertrend_only` + 20 `supertrend_plus_routing`)

## Closing-the-loop summary

- **Issue #11 status:** closed, both criteria met by `supertrend_only`.
- **Headline:** SuperTrend(10, 3) on 48mo BTC 4h, no parameter changes,
  produced 35 OOS trades, PF 2.24, +38.66% return, 9.63% max DD —
  passing both locked gates (PF > 1.69, trades ≥ 30). Baseline on the
  same 48mo period degrades to PF 1.09.
- **Next per ROADMAP:** Issue #5 — BTC/ETH relative-strength rotation.
  Independent benefit (cross-asset signal) plus a clean way to double
  the SuperTrend sample. The routing overlay (Issue #11 third variant)
  is the obvious sizing experiment after that.
