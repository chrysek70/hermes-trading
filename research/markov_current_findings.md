# Phase 1 — Current Markov implementation: findings

## Current implementation summary

**Location**: `hermes_trading/markov_regime.py` (~180 LOC)

**Model**: first-order discrete Markov chain over a fixed alphabet of 6 states.

**State definition** (`classify_states`):
- `direction ∈ {up, sideways, down}` from rolling `pct_change(return_window=12)` vs hard
  thresholds `up_return_threshold=0.003`, `down_return_threshold=-0.003`.
- `volatility ∈ {low_vol, high_vol}` from rolling std of 1-bar log returns over
  `vol_window=14` vs `high_vol_threshold=0.008`.
- State label = `f"{direction}_{vol_class}"`. 6 states total.
- States are NaN until both rolling windows are valid.

**Fit** (`fit`):
- Counts transitions `(s_t, s_{t+1})` over an optional `lookback_bars` window of
  the most recent classified states.
- Fixed 6-state alphabet (never data-driven), so the matrix is always 6×6.
- Rows with zero observations fall back to uniform prior `1/6` per cell.
- Refuses to fit on fewer than `min_training_bars` (default 500).

**Use as a filter** (`long_permission_score`):
- Hard binary: `long_allowed = (current_state ∈ allowed_long_states) AND
  (Σ next-state probs into allowed states ≥ min_prob_same_or_up)`.
- Default allowed set: `{up_low_vol, up_high_vol, sideways_low_vol}`.
- Default `min_prob_same_or_up = 0.55`.
- Disabled by default in YAML; the live loop respects `HERMES_MARKOV_ENABLE=1`.

**Integration in backtest** (`backtest.py:run_backtest`):
- Optional `markov_model` arg; if set and `enabled=true`, the per-bar mask is
  precomputed from the fitted matrix and attached to the records as
  `markov_long_allowed`.
- `_run_state_machine` skips long entries when the mask is False.
- Per-trade `markov_state` tagged for breakdowns.

## What was tested

| Setup | Result | Verdict |
|---|---|---|
| Backtest 1h IS (markov off / on) | PF 0.66 → 0.86 | filter looks great in-sample |
| Walk-forward 1h OOS (markov off / on) | PF 0.65 → **0.60** | filter HURTS OOS |
| Backtest 4h IS (markov on) | PF 1.08 | first IS PF > 1 |
| Walk-forward 4h OOS (markov on) | PF 0.89 vs baseline 1.25 | filter HURTS OOS |
| `regime_hold` size-mapping (5 variants on 1h, 4h, 1d) | every variant lost to HODL | sizing as designed lags badly in trends |

The in-sample → OOS gap is the canonical overfitting signature: the
transition matrix saw every bar of every fold, so the "filter" was tuned
implicitly to data it couldn't have known at decision time.

## Why it likely failed (mechanistic reasons)

1. **Knife-edge hard buckets.** A return of 0.31% is "up", 0.29% is
   "sideways". One noisy print flips the state.
2. **Arbitrary thresholds.** Why 0.3% return / 0.8% vol over 12 / 14 bars?
   Hand-picked; not data-driven.
3. **Backward-looking lag.** The classification labels the past 12 hours.
   By the time the label crystallises, the regime is partway done.
4. **Sparse-row variance.** `sideways_high_vol` was 0.4% of bars (~70
   observations) → 6 transition probabilities estimated off ~12 samples
   each → huge variance, replaced with uniform priors → useless rows.
5. **First-order Markov assumption is wrong for markets.** A 100-bar
   trend has different break probability than a 1-bar one; first-order
   chains say they're identical.
6. **Heterogeneous "same-name" regimes.** `down_low_vol` in 2022 (post-
   LUNA) is structurally different from `down_low_vol` in 2024 (post-
   rally chop). Lumping them averages information away.
7. **Hard binary gate** instead of continuous probability. We threw away
   most of the model's information when collapsing to a yes/no.
8. **Filter cannot rescue zero-edge strategy.** Walk-forward shows the
   underlying strategy has weak/no edge on 1h; filtering noise reduces
   trade count without creating expectancy.

## What parts ARE still useful

- The `classify_states` function as a *featurizer* (raw state labels per bar)
  — even if the classification is imperfect, the labels are causal and
  cheap. Useful for diagnostics and per-state performance breakdowns.
- The transition-matrix fitter — fine as the *plumbing* layer; the question
  is what we *do* with the matrix, not how we estimate it.
- The walk-forward harness in `walk_forward.py` correctly refits the matrix
  per fold and validates on unseen test bars — that pattern stays.
- The `markov_long_allowed` column injection pattern in `run_backtest` is
  the right integration shape; we'll generalise it to a *score* not a
  boolean.

## What should NOT be changed yet

- The state alphabet's six labels (don't redefine them while we're trying
  to compare variants — would invalidate cross-fold comparisons).
- The yaml's existing keys (extend, don't rename).
- `signals.py` — the strategy itself stays fixed during regime experiments.
  Mixing regime changes with strategy changes destroys attribution.
- The live worker (`loop.py`) — explicitly out of scope until OOS proves
  improvement (Phase 12).

## Strict guardrails for this research

1. **No optimisation on the full dataset.** Every reported metric is OOS.
2. **No state-definition tuning on test data.** Thresholds are config; their
   discovery, if any, will itself be walk-forward.
3. **Train-only "bad state" detection.** The set of states-to-reduce-size-in
   is computed from each fold's TRAIN slice — never from test results.
4. **Embargo** between train and test (configurable bars) to kill spillover
   from look-ahead windows like RSI(14).
5. **Honest reporting.** If a mode doesn't help OOS, the log says so. We may
   still surface partial wins (e.g. lower DD, better PF in specific
   regimes) but the headline is the OOS stitched metric.
