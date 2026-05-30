# Phase 3 — Research plan

Direct answers to the seven plan questions, plus the implementation order.

## 1. Why did our first Markov model fail?

Eight specific reasons, listed in `markov_current_findings.md`. In one line:
**knife-edge hand-defined states + first-order Markov assumption + binary
hard gate + applied to a strategy with no proven OOS edge**.

The model was *not* fundamentally broken — it was used in the worst possible
way (binary filter on a zero-edge strategy with no continuous information).

## 2. What do hedge funds / quants do differently?

(Confirmed by Perplexity + the GitHub corpus.)

Quants use regime models for:
- **Exposure control** — scale position size by regime probability.
- **Position sizing** — Kelly-style continuous, not on/off.
- **Bad-regime avoidance** — reduce or skip exposure when historical
  per-regime expectancy was negative.
- **Strategy routing** — pick *which* sub-strategy is active.
- **Volatility/risk forecasting** — feed regime into a vol model
  (Markov-switching GARCH).

They do **not** use regime models as:
- Next-candle predictors.
- Hard binary buy/sell signals.
- Standalone alpha engines (regime models manage exposure to alpha that
  already exists elsewhere in the system).

## 3. Which parts can we realistically copy?

| Pattern | Implementation here |
|---|---|
| Soft probability → continuous size | `soft_sizing` mode, `size_multiplier` clamped to `[min_score, max_score]` |
| Tiered sizing by regime | `bad_regime_avoidance` mode (train-set-only PF check per state) |
| Multi-timeframe confirmation | weighted combiner over 1h/4h/1d state classifications |
| Strategy routing | `strategy_routing` mode with per-state allowed_setups + size mult |
| Regime as a feature | `regime_features_only` mode — model classifies but never sizes/gates; for diagnostic use |
| Hysteresis to stabilise discrete states | `hysteresis_bars` in state config |
| Optional HMM with latent states | Phase 11 — `hmm_regime.py`, `hmmlearn` as optional dep |
| Walk-forward only | already in `walk_forward.py`; bad-regime detection fits on train slice |

## 4. Which parts are unrealistic for a single local BTC bot?

- Multi-asset portfolio optimisation across crypto + equities + macro.
- Markov-switching GARCH at production scale (we can afford an experiment).
- LSTM / RL regime classifiers (the ML infrastructure dwarfs the rest of
  the project; not warranted by sample size).
- Tick-level / order-book regime detection (we don't have the data feed).
- Continuous online retraining of HMMs on every bar.

## 5. Five practical Markov / regime experiments worth implementing

In phase order, all using walk-forward:

1. **Discrete Markov + hysteresis + soft sizing** (Phases 4–6). The minimal
   honest fix to v1: stop knife-edge flipping, stop binary gating.
2. **Bad-regime avoidance from train data only** (Phase 8). The pattern
   quants actually use — leakage-free per-regime expectancy check.
3. **Multi-timeframe regime score** (Phase 9). 1h/4h/1d weighted, gates only
   when multiple timeframes agree.
4. **Strategy routing** (Phase 10 schema). Per-regime allowed setups +
   size multiplier; v1 routing rules are simple but the framework supports
   later experiments.
5. **Optional 2-state HMM with multi-feature emissions** (Phase 11). Drop
   hand thresholds entirely; let EM learn the states. Compare against
   discrete Markov on the same walk-forward folds.

## 6. How will we avoid in-sample overfitting?

Hard rules in `walk_forward.py` and `run_markov_research.py`:

- **Walk-forward only.** Every reported number is the stitched OOS test
  result across folds.
- **Embargo bars** between train and test (configurable; default 24) so
  rolling features (e.g. RSI(14)) don't leak future info via overlap.
- **Bad-state set fit on TRAIN slice only.** The "states to avoid in test"
  list is derived from per-state PF on the *training* trades of each fold,
  never from test outcomes.
- **Multi-timeframe regime models fit per fold** on each timeframe's TRAIN
  slice independently.
- **No state-threshold tuning on test data.** Thresholds are YAML config,
  not learned per fold.
- **`reflect.py` is forbidden from touching Markov YAML keys** until OOS
  improvement is demonstrated (Phase 12 hard rule).
- **State alphabet frozen** during this research so cross-mode results are
  comparable.

## 7. What metrics decide success?

Success is **not** "beats HODL". It is a multi-axis read:

| Metric | What it tells us |
|---|---|
| Total OOS return | Headline; meaningless alone |
| Max drawdown | Did the filter actually reduce risk? |
| Profit factor | Edge per dollar lost |
| Sharpe (per-trade) | Risk-adjusted edge |
| Trade count | Did we just stop trading? |
| Exposure % | Fraction of bars in position — denominator for return |
| Return / exposure | "When we were in the market, how did we do?" |
| Drawdown reduction vs baseline | Direct test of the risk-management thesis |
| PF / win rate **by state** | Where the regime model actually adds value |
| Walk-forward consistency (sign of per-fold OOS PF) | Did it work in 6/8 folds or 1/8? |

A variant can "help" without beating baseline net return — e.g., halve the
drawdown while sacrificing 20% of return is a real risk-adjusted win.
Final report will be explicit about which axes each variant helped and
hurt.

## Implementation order

1. **Phase 4**: extend `state/markov_regime.yaml` schema (modes, hysteresis,
   sizing, bad_regime_avoidance, multi_timeframe, strategy_routing,
   validation).
2. **Phase 5–6**: extend `markov_regime.py` — add `hysteresis_apply`,
   `regime_score`, `size_multiplier`, `decision` API. Keep all existing
   methods for compat.
3. **Phase 7**: extend `backtest.py` — accept per-bar decision (sizing +
   setup permission). Produce per-state metrics + CSV.
4. **Phase 8**: extend `walk_forward.py` — bad_regime_avoidance uses train
   trades only; embargo.
5. **Phase 9**: multi-timeframe combiner — same `MarkovRegimeModel` fit
   independently on resampled views.
6. **Phase 10**: `scripts/run_markov_research.py` — runs five variants,
   writes CSV + MD summary.
7. **Phase 11**: optional `hmm_regime.py` (deferred until discrete framework
   complete; `hmmlearn` import inside a try/except).
8. **Phase 12**: no live wiring.
9. **Phase 13**: `research/markov_final_report.md` — honest results.
