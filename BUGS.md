# Known Issues

Catalogue of code and configuration issues found during the trading
audit + multi-angle code review. Listed in rough order of severity.
Each item names the file(s) and a concrete failure scenario. Fix work
is tracked in `TODO.md`.

## Live worker

### 1. Live PnL does not model fees or slippage

- **Where**: `hermes_trading/loop.py` — tick log and `state/trades.jsonl`
  emission paths compute `(last − entry_price) / entry_price × size`
  with no cost adjustment.
- **Impact**: Live paper PnL overstates net edge by roughly **25 bps per
  round-trip** vs the backtester (10 bps fee/side + 5 bps slippage).
- **Symptom**: When a live tick reports a +0.50% trade, the same trade
  through `backtest.py` would record ~+0.25% net. Reflection thresholds
  and any future migration to live execution will be biased optimistically.

### 2. `_init_markov_live` reads v1 yaml keys only

- **Where**: `hermes_trading/loop.py:_init_markov_live` reads
  `cfg["allowed_long_states"]` and `cfg["min_prob_same_or_up"]`.
- **Impact**: With the migrated v2 yaml shape (`sizing.favorable_states`,
  no top-level `allowed_long_states`), these resolve to an empty
  set / 0.5. The live regime check then evaluates
  `regime_allowed_long = bool(cs and cs in allowed_set and …)` as
  False on every tick → no longs ever open with
  `HERMES_MARKOV_ENABLE=1`, with no warning.
- **Symptom**: User sets `mode: soft_sizing` in the yaml, expects the
  research soft-sizing behavior live, gets silent inaction.

### 3. Live and backtest use the same engine but report different metrics

- **Where**: `loop.py` heartbeat omits `position_size_effective`,
  `entry_regime_score`, and per-trade fees; backtest trade records
  carry all of these.
- **Impact**: Cannot directly compare live tick log against backtested
  expectation per trade.

## Multi-timeframe regime layer

### 4. `multi_timeframe_score` fillna(True) inflates regime PF

- **Where**: `hermes_trading/markov_regime.py:multi_timeframe_score`
  forward-fills higher-TF `long_allowed` columns onto the decision-TF
  index and then `.fillna(True)` on residual NaN.
- **Impact**: At fold boundaries, the first ~14 4h bars of every test
  fold execute as if the regime filter were off. Combined with the
  matching `.fillna(1.0)` on `size_multiplier`, the early-fold bars
  ignore both the gate and the sizing. The previously-reported
  `strategy_routing` OOS PF of **2.17 dropped to 1.54** with the
  multi-TF path disabled. The honest baseline for new variants is the
  recalibrated number, not the inflated one.
- **Symptom**: Two configs with identical math produce different OOS
  numbers depending on whether `multi_timeframe.enabled` is set.

### 5. Multi-TF lookahead from left-labeled resample

- **Where**: `multi_timeframe_score` reindexes higher-TF decisions with
  `method="ffill"`. `data.resample` uses pandas defaults
  (`label="left"`, `closed="left"`).
- **Impact**: A 1d bar timestamped `YYYY-MM-DD 00:00` represents OHLC of
  the entire day, whose close is observable only at the following
  midnight. Forward-filling it onto the 4h bar at `04:00` of the same
  day uses information not yet available.
- **Symptom**: Multi-TF variants test as more profitable than they
  would in live execution.

### 6. Multi-TF upsampling silently drops weights

- **Where**: `walk_forward._build_multi_tf_decisions` re-resamples a
  decision-TF dataframe to each weight TF. Asking for a lower TF than
  the base (yaml-default 1h with `--timeframe 4h`) produces all-NaN
  rows, `dropna()` empties them, and the per-TF Markov fit raises.
  The exception is logged and swallowed.
- **Impact**: Weights are silently re-normalised across only the
  surviving TFs. A yaml that says `{1h: 0.5, 4h: 0.35, 1d: 0.15}`
  effectively becomes `{4h: 0.7, 1d: 0.3}` without notice.

## Backtester / decision integration

### 7. `_attach_decisions_df` carries NaN via `.values`

- **Where**: `hermes_trading/backtest.py:_attach_decisions_df` assigns
  `out["markov_state"] = aligned["raw_state"].values`.
- **Impact**: NaN floats land in records dicts and propagate to trade
  tags. Downstream `t.get("markov_stable_state") or … or "unknown"`
  treats `np.nan` as truthy because `bool(np.nan) == True`. CSV gets
  rows keyed `"nan"`; `identify_bad_states_from_train` ends up with a
  bogus `"nan"` bucket.

### 8. Short entry ignores `markov_long_allowed`

- **Where**: `backtest._run_state_machine`. The long branch checks
  `if long_ok and size_mult > 0.0`; the short branch checks only
  `if size_mult > 0.0`.
- **Impact**: In `hard_filter` mode, longs get gated but shorts trade
  unfiltered. The "Markov on/off" comparison is asymmetric and any
  conclusion drawn from it overstates filtering benefit.

### 9. Routing yaml admits shorts when designed for longs

- **Where**: `backtest._run_state_machine` strips `_short` from setup
  names before checking `markov_allowed_setups`.
- **Impact**: A yaml route with `allowed_setups: [breakout, pullback]`
  (clearly designed for longs in an up-state) silently permits the
  matching short setups too. Routes need direction-aware admission.

### 10. `run_backtest` exception swallow hides mode bugs as baseline

- **Where**: `backtest.run_backtest` wraps `compute_decisions` in
  `try/except Exception: ind = _attach_neutral_markov_columns(…)`.
- **Impact**: A KeyError in `soft_sizing` from a missing
  `cfg["sizing"]["current_state_weight"]` silently degrades the
  backtest to baseline-no-markov while the report header still says
  the configured mode.

## YAML schema mismatches

### 11. Unread yaml keys

- **Where**: `state/markov_regime.yaml`:
  - `model.type`, `model.order`, `model.smoothing_alpha`
  - `state.method` (`hysteresis_discrete`)
  - `use_as_filter`
  - `validation.walk_forward_only`, `validation.train_months`,
    `validation.test_months`, `validation.embargo_bars`
- **Impact**: A user editing `validation.embargo_bars: 48` expecting
  it to take effect runs at the argparse default of 6. Silent config
  drift between yaml and reported results.

### 12. `strategy_routing.enabled` is a no-op

- **Where**: `markov_regime.compute_decisions` never consults
  `cfg["strategy_routing"]["enabled"]`. Routes apply whenever
  `mode == "strategy_routing"`.
- **Impact**: Two flags that look linked are not. A user who flips
  `enabled: false` while leaving `mode: strategy_routing` still has
  active routes.

### 13. `setups.*.exit.trail_ema` is informational only

- **Where**: `state/strategy_v2_long_short.yaml` has
  `setups.breakout.exit.trail_ema: 21`. The exit code in
  `signals.long_exit` trails on `ema_pull` regardless of this value.
- **Impact**: Editing the yaml key has no effect on behavior.

### 14. Soft sizing branch crashes on missing nested keys

- **Where**: `markov_regime.compute_decisions` in `soft_sizing` and
  `regime_features_only` branches accesses
  `cfg["sizing"]["current_state_weight"]` and `["transition_weight"]`
  directly (no `.get()` fallback).
- **Impact**: Missing or partial `sizing` block raises `KeyError`
  mid-loop. The exception is swallowed by `run_backtest` (see #10)
  and the run silently falls through to baseline.

### 15. Soft sizing min_score floors block intent

- **Where**: Same branch. After zeroing `raw_score` for an
  unfavorable state, the clamp `max(min_s, min(max_s, raw_score))`
  with `min_s = 0.25` restores size to 25%.
- **Impact**: A state listed in `sizing.unfavorable_states` still
  trades at 25% size despite the explicit "unfavorable" label. The
  yaml comment tries to explain this; the behavior surprises most
  readers regardless.

## Reproducibility

### 16. Duplicated metric reducers

- **Where**: `backtest._run_state_machine`, `walk_forward._stitch_metrics`,
  `markov_regime.identify_bad_states_from_train`, `regime_hold._metrics`.
- **Impact**: Each implements its own Sharpe and profit-factor
  formulas. They have already drifted —
  `regime_hold` reports annualised Sharpe while everything else
  reports per-trade. Comparisons across modules are not apples-to-apples.

### 17. `_apply_overrides` dead-code second-level merge

- **Where**: `scripts/run_markov_research.py:_apply_overrides`.
- **Impact**: The "second-level merge" loop performs
  `{**a, **a}` on a sub-dict that was already replaced wholesale by
  the level-1 merge. Looks like deep-merge, behaves like shallow.
  Future deeper overrides will silently lose data.
