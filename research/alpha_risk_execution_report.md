# Alpha / Risk / Execution Report (Issue #25, Phase 5)

Final write-up for Issue #25. Answers the 9 questions in the spec.
Companion documents:

- `ARCHITECTURE.md` — layered system map.
- `research/alpha_risk_execution_audit.md` — per-module classification.
- `state/examples/` — three example yamls showing each layer's config shape.

## 1. What parts of the bot are alpha?

The signal-producing layer. Specifically:

- **`hermes_trading/signals.py`** — every indicator (`supertrend`,
  `ema`, `rsi`, `atr`, `vwap`, `donchian_channels`, `three_bar_play`)
  and every directional decision function (`long_entry`,
  `short_entry`, `long_exit`, `short_exit`, `initial_stop`,
  `initial_stop_short`, `_bullish_regime`, `_bearish_regime`).
- The EMA50 / EMA200 regime gate sits in the alpha layer because the
  regime is part of the signal definition — a SuperTrend flip in
  the wrong regime is not a signal at all.
- The strategy yamls (`state/strategy_*.yaml`) — they configure
  *which* setup fires and *with what parameters*.

## 2. What parts are risk?

Anything that gates, sizes, or terminates exposure *after* the
alpha signal has been identified:

- **Funding filter** (`hermes_trading/funding.py` + the
  `funding_filter:` block consumed by `multi_loop.LiveFundingOverlay`).
  Direction-aware. Currently the only Risk overlay wired into live.
- **HMM regime overlay** (`hermes_trading/hmm_regime.py`,
  `state/hmm_regime.yaml`). Research only.
- **6-state Markov classifier** (`hermes_trading/markov_regime.py`).
  Research only; rejected at adoption.
- **RS overlay** (`hermes_trading/relative_strength.py`). Research only.
- **Per-position stops** (set in alpha but read in execution; the
  ratcheting + breach check is risk).
- **Portfolio cap** (`max_open_positions` in the live config).
- **Per-asset equal-weight sizing** (`size_per_asset`).
- **Decay monitor** (`scripts/monitor_strategy_decay.py`) — currently
  report-only; the alarm / action mode (auto-reduce exposure on
  drift) is on the roadmap.

## 3. What parts are execution?

Orchestration of the live tick loop, simulated fills, position
persistence, restart safety, portfolio-cap enforcement:

- **`hermes_trading/run.py`** — CLI entrypoint and dispatch.
- **`hermes_trading/multi_loop.py`** — multi-asset orchestration
  loop (the primary live path).
- **`hermes_trading/loop.py`** — single-asset orchestration (legacy
  path, preserved for backward compatibility).
- **`hermes_trading/positions.py`** — per-asset position state IO,
  legacy migration, corrupt-file tolerance.
- **`hermes_trading/adapters/price.py`** — ccxt-backed price fetch
  with multi-exchange fallback. **Read-only**; no order routing.
- **`hermes_trading/backtest.py`** — the offline paper-fill model
  (with slippage + fees); shared with walk-forward.
- The `state/live_*.yaml` configs — they configure the execution
  layer (which assets, what cadence, what cap, which strategy
  file).

## 4. Where do Markov / HMM models belong?

**Risk.**

HMM / Markov models do not decide direction. They estimate which
regime the market is in and answer "is this strategy expected to
work right now, and at what size?". That is the textbook definition
of an exposure-control overlay — the Risk layer.

In the current codebase:

- `hermes_trading/hmm_regime.py` is consumed by Risk-layer
  research scripts (`scripts/run_hmm_regime.py`,
  `scripts/run_long_short_overlays.py`).
- `hermes_trading/markov_regime.py` was the legacy 6-state
  attempt; rejected per Issue #2 / #6 but still importable.
- Neither is wired into `multi_loop.py` today. They would be
  consumed exactly the same way the funding overlay is —
  attached as a per-asset decisions DataFrame that gates or
  sizes the entry.

## 5. Where does funding belong?

**Risk.**

Same reason as HMM. The SuperTrend flip is the alpha signal;
funding is direction-aware permission to take it:

- At funding percentile ≥ 95 (Issue #20 threshold): long entries
  blocked. The signal still fires; the risk layer just refuses
  to act on it.
- At funding percentile ≤ 5: short entries blocked.

The funding loader (`hermes_trading/funding.py`) is in the Risk
layer; the `funding_filter:` config block sits in the live
multi-asset yaml because that is where the operator opts in.
`LiveFundingOverlay` in `multi_loop.py` is the runtime risk
controller that consumes both.

## 6. What is currently live?

The adopted research candidate (Issue #20), wired in Issue #21,
is opt-in via:

```
state/live_multiasset_long_short_funding.yaml
```

That config runs:

- **Alpha**: SuperTrend(10, 3) on BTC/USDT + ETH/USDT 4h, long-short
  (Issue #19), EMA50/200 regime-gated. Entries / SuperTrend flip
  exits evaluated on the last CLOSED bar (Issue #24). Stops still
  react intra-bar.
- **Risk**: per-asset stops = SuperTrend line at entry (ratchets
  with the indicator); funding filter direction-aware (block long
  at p ≥ 95, block short at p ≤ 5); portfolio cap = 2; per-asset
  weight = 0.5. Decay monitor available on-demand
  (`scripts/monitor_strategy_decay.py`).
- **Execution**: multi-asset paper worker with per-asset position
  files, portfolio heartbeat, restart safety, circuit breaker.
  **No real-money path.**
- **Diagnostics**: SuperTrend tick display (Issue #17), verbose
  "why no trade" blocker diagnostic (Issue #18), funding state
  in heartbeat (Issue #21), bar-timestamp display (Issue #24),
  local / UTC display toggle (Issue #22).

The default (no `--config` flag) still runs the legacy single-asset
path against `state/goal.yaml` + `state/strategy.yaml` (v2
long-short). The recommended invocation is one of the multi-asset
configs.

## 7. What is still research-only?

- **HMM regime overlay** (Issue #6). Mechanism strong; trade-count
  gate blocks adoption at the single-asset and long-short levels.
  Natural fit for a multi-asset / larger-universe re-test.
- **6-state Markov classifier** (Issue #2). Rejected.
- **BTC/ETH RS overlay** (Issue #5 / #12 / #20). Highest PF +
  lowest DD of any overlay tested at the long-short level but
  cut trade count below the 100 gate. Natural fit for the
  top-5 portfolio extension.
- **Donchian-20 trend-following** (Issue #3). Rejected.
- **Pullback / breakout setups** in the SuperTrend alpha file —
  `enabled: false` in the adopted live config. Still computed
  for diagnostic continuity.
- **Top-5 parallel portfolio** (Issue #14). 5-asset version
  failed PF gate by 0.05; BTC/ETH parallel reference passed and
  IS in the live config.
- **Replay mode for the multi-asset config** (Issue #26 follow-up).

## 8. What should be built next?

In priority order (ordered by data prior on payoff vs cost):

1. **Live paper-fill slippage / fee model** (Execution backlog).
   Largest single gap between live behaviour and research numbers.
   Cheap to implement; eliminates a known ~25 bps/round-trip
   over-statement.
2. **Multi-asset replay mode** (Issue #26 — already specced).
   Lets the operator validate the adopted candidate by visually
   replaying the same configuration over 24 months.
3. **Decay-monitor → exposure reduction alarm** (Risk backlog).
   Wires existing reporting infrastructure into a Risk overlay
   that auto-scales-down on drift.
4. **HMM + funding stacked redundancy test** (Risk research).
   Issue #20 noted both attack similar regimes; explicit
   stacked-vs-single comparison on the adopted long-short variant
   would resolve whether they are additive.
5. **Daily / weekly diagnostic report** (Diagnostics backlog).
   Decay monitor + trade summary + position state → readable
   email-style output.

Items further out:

- Multi-timeframe SuperTrend confidence score (Alpha backlog).
- Volatility-targeting (Risk backlog) — should reduce DD without
  hurting PF in principle.
- Top-5 parallel portfolio with overlays (a re-run of Issue #14
  with HMM and RS attached now that the count-base is large enough
  to absorb their selectivity).

## 9. What should NOT be built yet?

- **Real-money execution.** Live worker is paper-only by deliberate
  choice. No broker / exchange WRITE adapters anywhere in the
  codebase. Adding them requires explicit user authorisation and
  a separate issue.
- **HFT / sub-minute strategies.** v1 RSI on 1m was rejected because
  fees dominated. Listed under "out of scope" in `ROADMAP.md`.
- **Multi-asset portfolio optimisation beyond BTC/ETH rotation.**
  Top-5 was already tested; cleaner research direction is to add
  overlays to the existing BTC/ETH parallel, not to grow the
  universe.
- **LSTM / RL strategies.** Sample sizes don't justify the ML stack.
- **Aggressive alpha additions** before fixing the live paper-fill
  model. Adding more alpha into a misaligned execution layer
  compounds the live-vs-research drift instead of correcting it.

## Layering invariants (codified)

These must hold for the architecture to stay coherent. Not enforced
by tests today; enforced by code review and the existing module
boundaries.

1. **Alpha never reads from Risk overlays.** A SuperTrend flip
   decision is identical whether or not HMM / funding / RS are
   enabled.
2. **Risk never produces a direction.** Overlays only allow,
   size, or block what alpha already decided.
3. **Execution never reads strategy parameters except through
   the yaml.** No hardcoded thresholds in `multi_loop.py` or
   `loop.py`.
4. **Diagnostics never modify state.** The decay monitor reports;
   it does not pause trading. The heartbeat is read-only from
   the live worker's perspective.
5. **Research never touches `state/live_*.yaml`.** Adopted
   research candidates enter live only via a user-explicit config
   edit.
