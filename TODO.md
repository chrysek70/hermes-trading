# TODO

Actionable items, ordered by priority within each section. Items are
implementation-ready unless flagged otherwise.

## Recently shipped

- ✓ Online walk-forward adaptive learning simulator (Issue #32) —
  research-only. `scripts/run_online_walk_forward.py` replays
  every 4h closed bar as if it were live, with strict
  no-future-leakage closed-trade memory. Six adaptive sizing
  rules tested: `none` baseline (+29.36% / 16.23% DD over 24mo),
  `rolling_decay_size` (+6.77% / 16.23%), `consecutive_loss_size`
  (+14.27% / 14.11%), `stop_cluster_size` (+14.12% / 8.72%),
  **`vol_sizing` (+20.78% / 7.78%, PF 1.51, best balance)**, and
  `ensemble` (+13.60% / 5.63% DD, lowest DD). All 6 rules close
  exactly 75 trades; multipliers MIN out at 0.25 so signals are
  never gated. Reuses `signals.long_entry/exit`,
  `multi_loop.LiveFundingOverlay`, `evaluate_funding_gate`, and
  the Issue #29 fee/slippage constants verbatim. CLI:
  `--config <yaml> --months <N> --adaptive-rule <name>` plus
  `--compare-all-rules`. Outputs decision/trade CSVs per rule
  and a per-window comparison CSV (3 / 6 / 12 / 24mo). Phase 1
  audit: `research/current_adaptation_audit.md`. Final report:
  `research/online_walk_forward_report.md`. 21 new self-test
  checks in Section 15 of `scripts/test_multiasset_worker.py`
  (260/260 pass). `signals.py` / `multi_loop.py` / `loop.py` /
  `run.py` and all `state/*.yaml` unchanged. Recommended next
  implementation: switch the adopted yaml to vol_sizing after
  the Issue #33 forward paper test passes.
- ✓ Recent-window decay investigation (May 2026) — research.
  Six-phase autonomous investigation triggered by a visibly bad
  recent 3-month live-replay window. Headline: the adopted live
  candidate is NOT decayed; the recent window sits at ~p21 of the
  historical 90-day distribution and walk-forward OOS on the same
  48 months still returns Issue #20's adoption numbers (123 trades,
  +139.71%, DD 4.64%, PF 3.35). New research scripts:
  `scripts/diagnose_recent_regime.py`, `scripts/run_rolling_decay.py`,
  `scripts/run_timeframe_comparison.py`,
  `scripts/run_multitimeframe_confirmation.py`,
  `scripts/run_adaptive_regime_response.py`. Six reports under
  `research/recent_*` and `research/rolling_decay_report.md`,
  `research/timeframe_comparison_report.md`,
  `research/multitimeframe_confirmation_report.md`,
  `research/adaptive_regime_response_report.md`,
  `research/recent_adaptation_final_report.md`. Phase 5 reconfirmed
  vol-quartile sizing (Issue #27 → #33 wiring) as the strongest
  adaptation: 48mo DD 4.64% → 2.10%, recent 3mo DD 4.40% → 1.44%.
  Recommended live action: operator switch from
  `state/live_multiasset_long_short_funding.yaml` to
  `state/live_multiasset_long_short_funding_vol.yaml` (already
  shipped under Issue #33; no code change required).
  `signals.py` unchanged. Live configs unchanged. `py_compile`
  clean; multi-asset worker + decay monitor self-tests pass.
- ✓ Vol-sizing overlay wired opt-in to live (Issue #33).
  `LiveVolSizingOverlay` in `multi_loop.py` mirrors the existing
  funding overlay; new yaml `state/live_multiasset_long_short_funding_vol.yaml`
  is the opt-in path. Existing live config unchanged. Trade rows +
  heartbeat carry vol context. Verbose tick lines display
  `vol: rv24=… bucket=Q… mult=… q=[…]`. 240/240 multi-asset
  self-tests pass (was 194). `signals.py` unchanged.
  No automatic default switchover.
- ✓ Adaptive regime-based position sizing — research (Issue #27).
  `scripts/run_adaptive_sizing.py` tested HMM-sizing, vol-quartile-sizing,
  and stacked HMM+vol on top of the adopted long-short + funding
  candidate over 48mo. **All three sizing variants pass the adoption
  criterion** (reduce DD or improve PF without materially reducing
  trade count — trade count is identical 123 across all four
  variants because sizing is multiplicative). Strongest: `vol_sizing`
  (PF 3.35 → 4.63, DD 4.64% → 2.10%, return-per-exposure preserved
  at +136.54%). Detailed report:
  `research/adaptive_sizing_report.md`. **Not wired to live** —
  prerequisite is the live paper-fill slippage fix (item #1 on the
  Execution-layer roadmap). User approval required before adoption.
- ✓ Replay multi-asset config support (Issue #26) — `scripts/replay_live.py
  --config state/live_multiasset_long_short_funding.yaml` replays the
  exact adopted live candidate over historical data (BTC + ETH together,
  portfolio cap, funding overlay reused from `multi_loop.LiveFundingOverlay`,
  closed-bar entry semantics per Issue #24). Optional `--trades-out` CSV
  with `asset, direction, entry_time, exit_time, entry_price, exit_price,
  return_pct, net_return_pct, setup, exit_reason, bars_held,
  funding_decision`. Legacy `--strategy` path preserved byte-for-byte.
  21 new self-test checks (158/158 total); decay monitor unaffected;
  py_compile clean. No live worker behaviour changed.
- ✓ Alpha / Risk / Execution architecture map (Issue #25) —
  `ARCHITECTURE.md` codifies the layered design;
  `research/alpha_risk_execution_audit.md` classifies every
  existing module; backlogs in `ROADMAP.md` organised by layer;
  `state/examples/` holds non-loadable templates. Documentation
  only; no code changes.
- ✓ Live signal parity fix (Issue #24) — entries and SuperTrend flip
  exits now evaluate on the most recent CLOSED candle. Display and
  intra-bar stop monitoring keep using the current in-progress
  candle for reactivity. `signals.py` byte-for-byte unchanged.
  137/137 self-test invariants pass; decay monitor unaffected. The
  H1/H2 drift identified by the Issue #23 audit is closed.
- ✓ Live wiring of long-short + funding filter (Issue #21) — opt-in
  via `state/live_multiasset_long_short_funding.yaml`. Long-only
  fallback config untouched. Direction-aware funding gate (block long
  ≥ p95, block short ≤ p5). 100/100 self-test invariants pass.
- ✓ Live tick display auto-switch (Issue #17) — SuperTrend mode now
  shows SuperTrend direction / line / distance instead of RSI; legacy
  v2 display preserved byte-for-byte; `--verbose` adds RSI back for
  debugging; heartbeat gains `supertrend_direction` /
  `supertrend_line` / `supertrend_distance_pct`. 49/49 self-test
  invariants pass; decay monitor unaffected.
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
