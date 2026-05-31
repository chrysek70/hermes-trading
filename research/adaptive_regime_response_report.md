# Adaptive risk-layer rules — Phase 5

Author: research agent (autonomous run, 2026-05-31)
Source: `scripts/run_adaptive_regime_response.py`
Window: 48 months (2022-05-01 → 2026-04-30) of BTC/USDT + ETH/USDT, 4h decision
Outputs: `results/adaptive_regime_response_20260531_073505.{csv,md}`
Per-trade CSV: `results/adaptive_regime_response_trades_20260531_073505.csv`

## Six rules tested

All rules are **risk-layer only** — they modify exposure (size or
pause) but never change a trade's direction. The alpha signal
(SuperTrend(10, 3)) and the funding gate (block long ≥ p95, block
short ≤ p5) are unchanged.

| code | rule | trigger | action |
|---|---|---|---|
| R1 | rolling 10-trade PF window  | PF < 1.0       | pause (size = 0)         |
| R2 | trailing consecutive losses | CL ≥ 3         | half-size (0.5×)          |
| R3 | rolling 30-day return       | ret < -3%      | half-size                |
| R4 | trailing 5-trade stop freq  | stop% > 80%    | half-size                |
| R5 | per-bar HMM adverse prob    | P(adverse)≥0.7 | half-size                |
| R6 | per-bar realised vol quartile (Issue #27) | high vol → 0.25; low vol → 1.0; mid → 0.5 | sizing |

R1 / R2 / R3 / R4 are trade-history-based (causal: only past closed trades
feed the trigger). R5 / R6 are bar-conditional (causal: trained on
fold-train, applied to fold-test). All thresholds are locked from the
spec — no tuning.

## 48mo walk-forward OOS

| variant | n | ret | DD | PF | folds+ | triggers | recent_modified |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline         | 123 | +139.71% | 4.64% | 3.35 | 16/20 |    0 |  0 |
| R1_pf_pause      |  47 |  +29.96% | 1.84% | 3.32 |  7/20 | 4388 |  0 |
| R2_consec_loss   | 123 | +135.70% | 4.64% | 3.33 | 16/20 |  265 |  1 |
| R3_30d_return    | 123 | +139.71% | 4.64% | 3.35 | 16/20 |   84 |  0 |
| R4_stop_freq     | 123 |  +73.52% | 4.09% | 3.10 | 16/20 | 4867 |  7 |
| R5_hmm_adverse   | 123 |  +97.15% | 3.14% | 3.60 | 16/20 |   57 |  9 |
| R6_vol_quartile  | 123 |  +72.71% | 2.10% | 4.63 | 16/20 |   42 | 11 |

Columns: `triggers` = number of fold-bars where the rule was active;
`recent_modified` = number of trades in the last 90 days that the
rule actually downsized.

## Trailing windows

| variant | scope | n | ret | DD | PF | win |
|---|---|---:|---:|---:|---:|---:|
| baseline         | last_3mo  | 13 |  +8.56% | 4.40% | 2.36 | 53.8% |
| R1_pf_pause      | last_3mo  |  7 |  -1.26% | 1.82% | 0.63 | 28.6% |
| R2_consec_loss   | last_3mo  | 13 |  +8.95% | 4.06% | 2.50 | 53.8% |
| R3_30d_return    | last_3mo  | 13 |  +8.56% | 4.40% | 2.36 | 53.8% |
| R4_stop_freq     | last_3mo  | 13 |  +3.78% | 3.42% | 1.77 | 53.8% |
| R5_hmm_adverse   | last_3mo  | 13 |  +4.18% | 2.87% | 2.01 | 53.8% |
| R6_vol_quartile  | last_3mo  | 13 |  +5.21% | 1.44% | 3.70 | 53.8% |
| baseline         | last_12mo | 41 | +49.65% | 4.40% | 4.67 | 68.3% |
| R6_vol_quartile  | last_12mo | 41 | +25.42% | 1.44% | 7.42 | 68.3% |

## Per-rule analysis

### R1 — Pause on 10-trade PF < 1.0

- 4388 fold-bars of pause means roughly half the time the strategy
  was forced flat. Total trades drops 62% (47 vs 123).
- 48mo return collapses from +139.71% to +29.96%. DD does fall
  dramatically (4.64% → 1.84%), but at unacceptable return cost.
- **Critically, on the recent 3mo the rule made things WORSE**:
  -1.26% vs the unmodified +8.56%. R1 paused trading right after
  the early-Feb pair of winning shorts (the 10-trade PF dropped
  below 1.0), then kept the bot flat through the early March
  recovery, then turned back on just in time for the late-March
  / April losses.
- Verdict: **REJECTED.** Trade-history-based pause rules over-react
  to historical noise and miss the recovery.

### R2 — Half-size after 3 consecutive losses

- 265 trigger-bars, but only 1 of the recent 13 trades was
  affected. The trailing-3-CL signal is sparse.
- 48mo return loss: 4 pp (+139.71% → +135.70%). DD unchanged.
- Last 3mo: +8.95% vs +8.56% (marginal). DD 4.06% vs 4.40% (small win).
- Verdict: **safe but small effect.** R2 acts as a soft circuit
  breaker without materially changing equity. Not a primary
  recommendation but a no-cost addition.

### R3 — Half-size on 30-day return < -3%

- 84 fold-bars active, but ZERO recent trades modified — because
  the 30-day window lags the chop streak. By the time the 30-day
  return crosses -3%, the chop has already happened.
- 48mo / 3mo metrics identical to baseline. No effect.
- Verdict: **REJECTED — too lagged.**

### R4 — Half-size on >80% stop exits over last 5 trades

- 4867 trigger-bars (most of the dataset!). 7 recent trades
  downsized.
- 48mo: return drops 47% (+139.71% → +73.52%). DD only slightly
  better (4.64% → 4.09%).
- Last 3mo: +3.78% vs +8.56%. R4 cuts size during nearly every
  recent trade because the strategy IS a high-stop-rate strategy
  by construction (the SuperTrend line IS the stop).
- Verdict: **REJECTED — false-positive rate too high; the trigger
  is structurally inconsistent with how the strategy exits.**

### R5 — Half-size on HMM adverse > 0.7

- 57 trigger-bars, 9 of 13 recent trades downsized.
- 48mo: PF improves (3.35 → 3.60), DD improves (4.64% → 3.14%),
  return drops 30%.
- Last 3mo: +4.18% vs +8.56%. DD 2.87% vs 4.40%. Solid DD
  reduction; non-trivial return loss.
- Verdict: **runner-up candidate**. Strong DD reduction with
  better PF, but R6 dominates on DD per unit of return given up.

### R6 — Volatility-quartile sizing (Issue #27 spec)

- 42 trigger-bars per fold (causal — vol quartile bands fit on
  TRAIN, applied to TEST). 11 of 13 recent trades downsized
  (mostly to 0.25× during the high-vol chop streak).
- 48mo: PF jumps (3.35 → 4.63, +38% improvement). DD halves
  (4.64% → 2.10%). Return drops 48% but return-per-exposure is
  preserved (Issue #27 confirmation).
- Last 3mo: +5.21% vs +8.56%. DD 1.44% vs 4.40% — DD drops 67%.
  PF 3.70 vs 2.36 — best of any variant.
- Verdict: **PRIMARY RECOMMENDATION.** Best return-per-DD on the
  full 48mo window AND on the recent 3mo. Already validated in
  Issue #27 as the strongest sizing candidate. Causal, simple,
  deterministic.

## Rule comparison summary

If the objective is **minimise damage in chop without destroying
return in trends**:

1. **R6 (vol-quartile sizing) — primary recommendation.** DD drops 55%
   (4.64% → 2.10%) on 48mo, 67% (4.40% → 1.44%) on recent 3mo. PF
   improves on both axes. Return drops 48% on 48mo, 39% on recent.
2. **R5 (HMM-adverse half-size) — runner-up.** DD drops 32% on
   48mo, 35% on recent. PF improves. Return drops 30% on 48mo,
   51% on recent. More complex than R6 (requires HMM fitting per
   fold per asset).
3. **R2 (consec-loss half-size) — soft circuit breaker.**
   Essentially free (1-4% return cost, no DD improvement). Useful
   as a defensive addon to R6.

R1, R3, R4 are rejected: R1 misses the recovery, R3 fires too late,
R4 fires too often.

## Did any rule fire during the recent 3mo bad window?

Yes, all of R2 / R4 / R5 / R6 fired at least once on the recent 13
trades. R1 paused trading completely for parts of the window
(taking only 7 trades). R3 did not fire (the 30-day window had not
yet crossed the threshold at any of the 13 entry timestamps —
illustrating the lag problem).

R6 modified 11 of 13 recent trades (the most). R5 modified 9. R4
modified 7. R2 modified 1.

## Is vol_sizing still the best?

Yes. R6 = vol_quartile sizing reproduces exactly the Issue #27
result and remains the strongest sizing candidate across the
adopted long-short + funding baseline.

The recent window does not change this verdict; it strengthens it.
R6 was specifically designed for the kind of chop the recent 3mo
exhibited and would have cut the recent DD to 1.44%.

## Wiring implications

R6 wiring touches only the **risk layer**. The alpha layer
(SuperTrend signals) is unchanged. The execution layer needs a
per-bar size-multiplier hook — exactly the same hook the live
worker would need for any sizing rule. The decay monitor stays
report-only. Architecturally clean, layered exactly as
`ARCHITECTURE.md` prescribes.

**Status: ALREADY SHIPPED.** Issue #33 wired vol_sizing to the live
paper worker after Issue #27 closed the research and Issue #29
closed the live-fill parity prerequisite. The infrastructure is:

- `LiveVolSizingOverlay` class in `hermes_trading/multi_loop.py`.
- Opt-in yaml at `state/live_multiasset_long_short_funding_vol.yaml`
  (parameters locked: 24-bar window, 12-month rolling refit,
  multipliers 1.00 / 0.50 / 0.25 mapped to Q1 / Q2-Q3 / Q4).
- Section 16 self-tests in `scripts/test_multiasset_worker.py`
  cover causal correctness, bucket assignment, fail-open behaviour.

So the action implied by this Phase 5 finding is:

**Switch the live worker config from
`state/live_multiasset_long_short_funding.yaml` to
`state/live_multiasset_long_short_funding_vol.yaml`**, no code
change required. The Phase-5 R6 numbers in this report are the
research equivalent of what that switch would deliver.
