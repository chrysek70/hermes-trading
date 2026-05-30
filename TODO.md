# TODO

Actionable items, ordered by priority within each section. Items are
implementation-ready unless flagged otherwise.

## Recently shipped

- ✓ Multi-asset live paper worker (Issue #16) — `--config state/live_multiasset.yaml`
  runs BTC/USDT + ETH/USDT in parallel. Per-asset position state,
  portfolio heartbeat, extended trade rows, legacy migration. 27/27
  self-test checks pass.
- ✓ Live strategy decay monitor (Issue #15).
- ✓ Funding-rate filter experiment (Issue #7).
- ✓ HMM regime overlay (Issue #6).
- ✓ Top-5 parallel portfolio (Issue #14).
- ✓ ETH-vs-BTC SuperTrend diagnostic (Issue #13).
- ✓ Multi-asset SuperTrend + RS (Issue #12).
- ✓ SuperTrend extended history (Issue #11).
- ✓ BTC/ETH RS context (Issue #5).
- ✓ SuperTrend(10, 3) trend-following (Issue #4).

## Strategy research (next)

- [ ] **Implement SuperTrend(10, 3) trend-following setup** in `signals.py`
      (`supertrend(df, period=10, multiplier=3)`; entry on bullish flip,
      exit on opposite flip). Add to a new `state/strategy_supertrend.yaml`,
      walk-forward against baseline. Hard rule: do not tune parameters.
- [ ] **Implement ETH 4h walk-forward path.** `data.load_klines("ETHUSDT", …)`
      already works; need to confirm Binance Vision availability and add a
      `--symbol` arg path through `scripts/run_markov_research.py`.
- [ ] **Implement BTC/ETH relative-strength filter** as an optional entry
      gate (`pct_change(eth/btc, 20)` sign agrees with direction).
- [ ] **Implement HMM 2-state regime model** as optional `hmm_regime.py`
      with `hmmlearn` as optional dependency. Features per Phase-4 plan:
      log_return, rolling_vol, volume_zscore, atr_pct, dist-from-EMA50/200.
- [ ] **Implement funding-rate adapter** (`adapters/funding.py`) and a
      `funding_filter` gate at entry. Needs Binance perpetuals funding API.

## Backtest + walk-forward infrastructure

- [ ] **Centralise metric reducers** (Sharpe, profit factor, drawdown,
      win rate, expectancy) into a single `metrics.py` module. Currently
      duplicated across `backtest._run_state_machine`,
      `walk_forward._stitch_metrics`, `markov_regime.identify_bad_states_from_train`,
      `regime_hold._metrics`.
- [ ] **Fix multi-timeframe `fillna(True)` leak** in
      `markov_regime.multi_timeframe_score` (line ~388) and
      `backtest._attach_decisions_df` (line ~252). NaN long_allowed at
      fold boundaries should default to False, not True.
- [ ] **Shift higher-TF decisions by one bar** before reindex/ffill in
      `multi_timeframe_score` to remove the same-day-state lookahead.
- [ ] **Replace `.values` assignments** in `_attach_decisions_df` with
      index-aligned assignment so silent positional misalignment becomes
      observable NaN.
- [ ] **Embargo bars** — make default match the longest rolling window in
      use (currently 6 bars vs RSI(14) needs ≥14). Add a runtime check.
- [ ] **Trades-detail CSV writer**: emit a fixed column order even when
      individual trades omit some keys (currently relies on
      `csv.DictWriter` field-superset).

## Live worker (research-side improvements only — do not deploy yet)

- [ ] **Model fees and slippage** in the live PnL display so live tick
      log shows net edge consistent with backtest.
- [ ] **Migrate `_init_markov_live` to v2 yaml keys** (mode, sizing,
      strategy_routing). Currently reads v1 keys only, so a v2 yaml with
      `HERMES_MARKOV_ENABLE=1` silently blocks all longs.
- [ ] **Wire the v2 decision API** (`mr.compute_decisions`) into the live
      worker so live behavior mirrors the backtester.
- [ ] **Persist `markov_regime_score`** in the heartbeat for diagnostic
      consistency with backtest trade tags.

## Configuration hygiene

- [ ] **Drop or wire unused yaml keys** in `state/markov_regime.yaml`:
      `model.type`, `model.order`, `state.method`, `use_as_filter`, and
      the entire `validation.*` block (currently documentation-only).
- [ ] **Fix `strategy_routing.enabled` no-op** —
      `markov_regime.compute_decisions` should respect the flag, not just
      check `mode`.
- [ ] **Add config validation** at YAML load (`load_yaml` wrapper) that
      checks required keys present and unknown keys flagged.
- [ ] **Separate live and research configs** — `state/strategy.yaml`
      should be the live-only file; experimental yamls should live in
      `state/experiments/` or similar.

## Reporting + diagnostics

- [ ] **Add a `make report` target** that runs the standard
      walk-forward sweep and regenerates `research/` summaries.
- [ ] **Add per-fold equity-curve plots** (matplotlib, optional dep) for
      walk-forward report.
- [ ] **Trade-level histograms** of entry RSI, ATR%, holding bars per
      variant, written to the `results/` directory alongside CSVs.
- [ ] **Track exposure-time-weighted return** as a first-class metric
      next to total return; already computed as `exposure_pct`, just
      surface it in summaries.

## Documentation

- [ ] Add a short HOW-TO for adding a new setup (entry + exit + yaml +
      walk-forward integration) — currently scattered across phase
      reports.
- [ ] Add a glossary linking strategy yaml keys to code locations.
