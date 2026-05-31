# Architecture

Layered structure of the paper-trading bot, mapped onto the canonical
quant-shop separation:

```
       ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
DATA →  │  ALPHA   │ → │   RISK   │ → │EXECUTION │ → │DIAGNOST. │ → │ RESEARCH │
       └──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
                                                                          │
                                                                          ▼
                                                                  feedback loop:
                                                                  experiments tested,
                                                                  some adopted into
                                                                  alpha/risk above
```

**Markov / HMM regime models belong in the Risk layer** — they
estimate when a strategy should be trusted and how much exposure
to allocate. They do not decide direction.

**Funding filter belongs in the Risk layer** for the same reason.
The SuperTrend flip decides direction; funding decides whether
that direction is permitted at the current funding extreme.

---

## Data

External feeds the bot consumes.

| component | source | purpose |
|---|---|---|
| OHLCV (live) | Kraken via ccxt (Binance geo-blocked) | live worker price stream, 4h decision bars |
| OHLCV (history) | Binance Vision public CDN, cached locally | backtest, walk-forward, replay |
| Funding rates | Binance Vision futures/um/monthly/fundingRate, cached locally | research + live (when funding overlay is on) |
| Context (on-chain, news, macro) | adapter best-effort fetches in `loop.py` | informational only; not used in decisions |

Not yet integrated: tick data, order-book depth, open interest,
liquidation events.

## Alpha layer

**What it does**: produce a direction (long / short / flat) for
each asset on each closed bar.

**Currently live (in `state/live_multiasset_long_short_funding.yaml`):**

- SuperTrend(10, 3) on 4h decision bars.
- Long entry on SuperTrend flip DOWN → UP, gated by EMA50 > EMA200.
- Short entry on SuperTrend flip UP → DOWN, gated by EMA50 < EMA200
  (only enabled in the long-short config; the long-only fallback
  config keeps `shorts.enabled: false`).
- Exits: SuperTrend flip back, stop (the SuperTrend line ratchets),
  optional max-hold-bars.

**Code locations:**

- `hermes_trading/signals.py` — `supertrend()`, `compute_indicators()`,
  `long_entry`, `short_entry`, `long_exit`, `short_exit`,
  `initial_stop`, `initial_stop_short`, regime gates.
- Strategy yamls in `state/`.

**Research-only:**

- Pullback / breakout setups (legacy v2 long-short).
- Donchian-20 trend-following (rejected per Issue #3).
- RS context (cross-asset BTC/ETH return diff and ratio EMA, used
  in research as both an alpha modifier and a risk overlay — Issue
  #5, #12, #20).

**What is missing:**

- Multi-timeframe SuperTrend confidence score.
- Factor-style features (momentum, carry).
- Cointegration / pairs research if the universe grows.

## Risk layer

**What it does**: take the alpha signal and decide *whether* and
*how much* to put on. Sit between alpha and execution.

**Currently live:**

- Funding filter (opt-in via the long-short funding config, Issue
  #21): direction-aware. Block long entries at funding percentile
  ≥ 95; block short entries at percentile ≤ 5. Fail-open on
  missing data.
- Portfolio cap (`max_open_positions: 2` in the live config).
- Per-asset equal weight (`size_per_asset: 0.5`).
- Per-position stop = SuperTrend line at entry; ratchets with the
  indicator.

**Research-only:**

- HMM 2-state regime overlay (`hermes_trading/hmm_regime.py`,
  Issue #6). Strong PF / DD effect but cut trade count below the
  30-trade gate on single-asset. Issue #20 also tested it on the
  long-short variant and the HMM cut return too far for the
  primary adoption gate.
- 6-state hand-defined Markov (`hermes_trading/markov_regime.py`,
  Issue #2). Rejected.
- BTC/ETH RS overlay (`hermes_trading/relative_strength.py`,
  Issue #5 / #12 / #20). Adopted in research at the long-short
  level (Issue #20: highest PF and lowest DD of any overlay) but
  cut trade count below the 100 gate.
- Decay monitor (`scripts/monitor_strategy_decay.py`, Issue #15).
  Currently report-only; the "alarm" mode that would auto-lower
  exposure is on the roadmap.

**Where future work goes:**

- Volatility targeting (resize per asset by realised vol).
- Dynamic exposure caps.
- Decay-monitor → exposure-reduction feedback.
- Funding + HMM redundancy test (Issue #20 noted both attack the
  same regimes; redundancy is the open question).

## Execution layer

**What it does**: orchestrate the live tick loop, place simulated
fills, persist position state, recover from restart.

**Currently live:**

- `hermes_trading/run.py` — CLI entrypoint.
- `hermes_trading/multi_loop.py` — multi-asset orchestration loop.
  Polls every 10 s, evaluates entry / exit on the **most recently
  closed candle** (Issue #24), writes per-asset position state and
  heartbeat.
- `hermes_trading/loop.py` — single-asset orchestration (legacy
  path, kept for backward compat).
- `hermes_trading/positions.py` — per-asset position state IO
  with restart safety + corrupt-file tolerance + legacy migration.
- `hermes_trading/adapters/price.py` — multi-exchange fallback
  (Kraken primary; ccxt-driven).
- Trade row schema in `state/trades.jsonl` — kept stable so
  downstream consumers like the decay monitor don't break.

**Modeling asymmetry (closed in Issue #29):**

- Backtest models entry / stop / exit fill slippage (10 bps fee /
  side + 5 bps slippage).
- Replay matches the backtest convention (Issue #26).
- Live worker now ALSO matches: entries fill at `close × (1 ± slip)`,
  stops at `stop × (1 ∓ slip)`, non-stop exits at `close × (1 ∓
  slip)`, and `net_return_pct` deducts `2 × fee × size` in return
  space. Constants are configurable via `fee_per_side` and
  `slippage` in the multi-asset yaml; defaults
  (`RESEARCH_FEE_PER_SIDE = 0.001`, `RESEARCH_SLIPPAGE = 0.0005`)
  match the research backtest exactly.
- **Backtest + replay + live now produce matching entry price,
  exit price, and net_return_pct accounting within rounding
  tolerance.** Issue #29 added parity tests (Section 14 of
  `scripts/test_multiasset_worker.py`) verifying this directly.

**No real-money execution path.** Adapters are read-only. There
is no broker / exchange WRITE code anywhere.

**What goes here later:**

- Live paper-fill slippage / fee model so live PnL matches
  research numbers.
- Exchange / broker abstraction if real-money trading is ever
  considered.
- Market-hours abstraction (24/7 for crypto, but NYSE / NASDAQ
  hours for any future stock strategies).
- Smart-order routing, VWAP / TWAP execution algorithms — for any
  real-money future.

## Diagnostics layer

**What it does**: make state observable. Never modifies decisions.

**Currently live:**

- `hermes_trading/display.py` — tick line formatter that
  auto-switches between SuperTrend (Issue #17) and legacy RSI
  displays based on the active strategy. Plus the "why no trade"
  blocker diagnostic (Issue #18) and the
  `split_display_and_signal_rows` helper (Issue #24).
- `state/heartbeat.json` — per-poll snapshot. Schema
  `multiasset-v2` includes per-asset close, RSI, SuperTrend
  direction / line / distance, bullish regime flag, funding
  state, position fields; portfolio realised / unrealised PnL,
  open positions, cap.
- `state/trades.jsonl` — append-only closed-trade log.
- `scripts/monitor_strategy_decay.py` — rolling-window PF / DD /
  win-rate / consecutive-losses alarm vs research baselines.
- `scripts/replay_live.py` — historical replay through the live
  engine at compressed speed.

**What goes here later:**

- Daily / weekly health report (running decay monitor +
  formatting into a readable summary).
- Multi-asset replay (Issue #26 / follow-up).
- Persistent "no-trade explanation" summary so the user can answer
  "why didn't the bot trade today?" without re-running the worker
  in verbose mode.

## Research infrastructure

**What it does**: validate hypotheses offline before they are
allowed into live.

**Currently in place:**

- `hermes_trading/backtest.py`, `hermes_trading/walk_forward.py` —
  bar-by-bar replay + rolling train/test harness.
- 14+ experiment runner scripts in `scripts/run_*.py`.
- Locked adoption criteria (see `ROADMAP.md`).
- Frozen reports in `research/` and comparison CSVs / MDs in
  `results/`.

This layer never executes live decisions. Its output is a yes/no
adoption recommendation that the user manually applies (or
rejects) by editing the live config.

---

## Where things go when added

| if you want to add … | … it goes in the … layer |
|---|---|
| a new indicator (Donchian variant, multi-TF SuperTrend) | Alpha |
| a new setup (volatility-compression breakout) | Alpha |
| a cross-asset relative-strength score that flips entries | Alpha (the direction logic itself) |
| an HMM that scales position size | Risk |
| a funding-derived gate | Risk |
| a volatility-targeting weight | Risk |
| a decay-monitor-driven exposure cut | Risk |
| an alternative paper-fill model | Execution |
| a broker WRITE adapter | Execution |
| market-hours awareness for NYSE | Execution |
| a daily summary email | Diagnostics |
| a new backtest comparison framework | Research |

---

## Layering invariants

These rules must hold for the architecture to stay coherent:

1. **Alpha never reads from Risk overlays.** The SuperTrend flip
   decision is the same whether or not HMM / funding / RS are
   enabled. Overlays only modulate what happens after the alpha
   says "long" / "short".
2. **Risk never produces a direction.** HMM does not say "go long";
   it says "this regime is safe to be in" or "size to 0.5×".
3. **Execution never reads strategy parameters except through the
   yaml config.** No hardcoded thresholds in the orchestration
   layer.
4. **Diagnostics never modify state.** The decay monitor reports;
   it does not (yet) pause trading. The heartbeat is read-only
   from the live worker's perspective.
5. **Research never touches `state/live_*.yaml`.** Adopted
   research candidates only enter live via a user-explicit config
   edit.

These invariants are not enforced by tests today. They are
enforced by code review and by the existing module boundaries.

---

## What is currently live vs research-only — at a glance

| component | live | research-only |
|---|:---:|:---:|
| SuperTrend(10, 3) | ✓ | |
| Long entry / exit | ✓ | |
| Short entry / exit (long-short config) | ✓ (opt-in) | |
| EMA50 / EMA200 regime gate | ✓ | |
| Funding filter (long-short config) | ✓ (opt-in) | |
| Per-asset stops (SuperTrend line) | ✓ | |
| Portfolio cap (2 positions) | ✓ | |
| HMM regime overlay | | ✓ |
| 6-state Markov | | ✓ (rejected) |
| BTC/ETH RS overlay | | ✓ |
| Decay monitor (report mode) | ✓ | |
| Decay monitor (alarm / action mode) | | not built |
| Live paper-fill slippage model | | not built |
| Broker / exchange WRITE | | not built |
| Replay mode (single-strategy) | ✓ (informational) | |
| Replay mode (multi-asset config) | | planned (Issue #26) |
