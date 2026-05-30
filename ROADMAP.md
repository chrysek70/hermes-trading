# Roadmap

Snapshot of where the research stands, what's been tried, and what's
queued next. Updated as experiments complete.

## Current baseline

Walk-forward OOS on BTC/USDT 4h, 24 months, fees 10 bps/side + 5 bps
slippage, 8 folds (train 1440 / test 360 / embargo 6 bars):

| metric | value |
|---|---:|
| OOS return | +8.97% |
| max drawdown | 4.43% |
| profit factor | 1.69 |
| Sharpe (per-trade) | 0.137 |
| trade count | 33 |
| folds positive | 3/8 |

This is the floor every new variant must beat.

## Adoption criteria (locked)

A variant is adopted only when **both** hold against the same walk-forward
folds as the baseline:

- OOS profit factor > **1.69**
- OOS trade count ≥ **30**

No partial credit, no in-sample wins reported as success, no parameter
tuning to make a variant cross the bar after the fact.

## Completed experiments

| experiment | OOS PF | OOS return | verdict |
|---|---:|---:|---|
| v1 RSI mean-reversion (1m) | — | -75% (in-sample) | decommissioned: fees dominate at 1m |
| v2 long-short (4h) | 1.69 | +8.97% | adopted as live; current baseline |
| Markov hard_filter | 1.68 | +7.70% | rejected: indistinguishable from baseline |
| Markov soft_sizing (no MTF) | 1.86 | +7.48% | partial win: risk-adjusted only; lower return |
| Markov bad_regime_avoidance | 1.69 | +8.97% | rejected: inert — per-fold sample too small |
| Markov multi_timeframe_soft_sizing | 1.86 | +7.48% | rejected: degenerates to single-TF in our 24mo window |
| Markov strategy_routing (MTF off) | 1.54 | +3.94% | rejected after MTF recalibration; earlier 2.17 was inflated |
| Markov routing + soft_sizing combined | 1.37 | +2.55% | rejected: combination worse than either component alone |
| Regime-based HODL sizing | — | -44% to +9% | rejected: every variant underperformed naive HODL |
| Donchian-20 trend-following | 0.90 | -2.16% | rejected: trend mechanism partial (16+ bars: PF 15.84) but short-holds destroy it |
| Donchian + strategy_routing | 0.84 | -2.97% | rejected: routing slightly hurt Donchian |

## Rejected experiment patterns

- **Hard binary gating** on a continuous regime signal (information loss).
- **Combined regime modes** (routing + sizing) — interactions degrade both.
- **Position-sizing-as-HODL-overlay** — backward-looking classifier lags
  bull trends and cuts size at the wrong moments.
- **Single-condition trend entries** (Donchian-20 alone) on 4h BTC —
  fire too often in chop relative to multi-condition confluence.

## Queued experiments (in execution order)

Run one at a time. After each, walk-forward against the current baseline.
Adopt if and only if the criteria above are met. If not, move to the next
without tuning the failed one.

1. **SuperTrend(10, 3) trend-following.**
   Hypothesis: ATR-based directional flips fire less often than Donchian-20
   in chop and capture the same trend-continuation bucket. Two
   hyperparameters, both TA-conventional defaults.

2. **BTC/ETH relative-strength rotation.**
   Hypothesis: cross-asset relative strength gates entry direction;
   doubles sample by adding ETH on the same engine.

3. **HMM 2-state regime overlay (optional `hmmlearn` dep).**
   Hypothesis: latent states found by EM are cleaner than the hand-defined
   6-state Markov alphabet; soft probabilities feed exposure scaling.

4. **Funding-rate stress filter.**
   Hypothesis: extreme perpetuals funding precedes squeezes; gate
   direction by sign of funding. Requires a new data adapter.

5. **(Conditional) volatility-compression breakout.**
   Hypothesis: only fire breakouts after a low-ATR-quartile compression.
   Phase-3 audit showed the `med-low` ATR bucket had PF 2.84.

## Infrastructure improvements (orthogonal to strategy)

- Model fees and slippage in the live worker (currently zero — paper PnL
  overstates net edge by ~25 bps per round-trip).
- Migrate `_init_markov_live` to read v2 yaml keys so live regime layer
  can match research configurations.
- Fix multi-timeframe fillna behaviour in `markov_regime.multi_timeframe_score`
  and `backtest._attach_decisions_df` (see `BUGS.md`).
- Centralise duplicated metric reducers (Sharpe, profit factor,
  drawdown) into a shared helper.
- Config validation for strategy + regime YAML (catch missing/unused keys
  at load time instead of mid-loop crashes).

## Out of scope

- Real-money execution.
- Live order routing or exchange API writes.
- HFT / sub-minute strategies (fees dominate; rejected experimentally).
- Multi-asset portfolio optimisation beyond simple BTC/ETH rotation.
- LSTM / RL strategies (sample sizes don't justify the ML stack).
