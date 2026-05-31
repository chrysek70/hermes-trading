# Alpha / Risk / Execution Audit (Issue #25, Phase 1)

Per-module classification of the current codebase against the
canonical quant-shop layering: **Alpha → Risk → Execution →
Diagnostics → Research infrastructure**.

Markov / HMM models sit in the **Risk** layer — they decide
*when a signal should be trusted* and *how much exposure to
allocate*, not what direction to trade. This audit reflects that.

Classification rule used:

- A module is **Alpha** if it produces or shapes the entry/exit
  signal itself.
- It is **Risk** if it sizes, gates, or terminates exposure
  *after* the alpha signal is identified.
- It is **Execution** if it operates the live trading loop, fills,
  position persistence, or order lifecycle.
- It is **Diagnostics** if its job is to make state observable to
  the operator (display, heartbeat, replay, decay monitor).
- It is **Research infrastructure** if it exists to test
  hypotheses offline (backtests, walk-forward, experiment runners).

A module that does more than one thing is listed under its
dominant role with a note.

---

## Alpha layer

| module | what it is | notes |
|---|---|---|
| `hermes_trading/signals.py` | Indicator computation + directional decision logic (`compute_indicators`, `long_entry`, `short_entry`, `long_exit`, `short_exit`, `initial_stop`, `initial_stop_short`, `supertrend`, `donchian_channels`, `three_bar_play`) | Pure alpha. Same function called from both backtest and live. Issue #19 added the SuperTrend short branch; Issue #24 did **not** touch this file. |
| `state/strategy_supertrend.yaml` | SuperTrend(10, 3) long-only adopted live config (Issue #11 / #14) | Alpha config — sets which setups fire and their parameters. |
| `state/strategy_supertrend_long_short.yaml` | SuperTrend(10, 3) long + symmetric short adopted research candidate (Issue #19) | Alpha config — same indicator, both sides on. |
| `state/strategy_supertrend_rs.yaml` | SuperTrend + BTC/ETH RS context (Issue #5 research only) | Alpha config — RS is conceptually a risk overlay, but its yaml lives next to the strategy. See `relative_strength.py` for the actual risk-layer code. |
| `state/strategy_supertrend_multiasset_rs.yaml` | Multi-asset RS overlay config (Issue #12) | Same situation as above. |
| `state/strategy_v2_long_short.yaml` | Legacy v2 pullback / breakout — current single-asset default | Alpha config. The 2024-era baseline. |
| `state/strategy_v2.yaml`, `state/strategy.yaml`, `state/strategy_donchian_markov.yaml`, `state/strategy_markov_routing_sizing.yaml` | Legacy / historical alpha configs from prior issues | Inert unless explicitly loaded. |

**What "SuperTrend strategy" means in code terms**: the SuperTrend
indicator math (`signals.supertrend`), plus the entry / exit
branches that route on `setup == "supertrend"` /
`"supertrend_short"`, plus the regime gate `_bullish_regime` /
`_bearish_regime`. All inside `signals.py`. The yaml just turns
the right setups on.

**What "RSI logic" means in code terms**: `signals.rsi` (the
indicator), plus the pullback-setup branches in `long_entry` /
`short_entry` that read `row["rsi"]` against a threshold. In the
adopted live config the pullback setup is `enabled: false`, so
RSI is **computed but never gates a decision**. Issue #17 demoted
RSI from the headline of the tick line for this reason.

**What "EMA50/EMA200 filter" is**: the `_bullish_regime` /
`_bearish_regime` helpers. They sit in the alpha layer because
the regime gate is part of the *signal definition* — a
SuperTrend flip in a bearish regime is not a long signal at all.

---

## Risk layer

| module | what it is | live or research only |
|---|---|---|
| `hermes_trading/funding.py` | Funding-rate loader + percentile / alignment helpers | **live** (consumed by `multi_loop.LiveFundingOverlay` when `funding_filter.enabled: true`) + **research** (consumed by Issue #7, #20 scripts) |
| `hermes_trading/hmm_regime.py` | Optional 2-state Gaussian HMM regime detector | **research only**. Module exists, optional `hmmlearn` dep, but `loop.py` / `multi_loop.py` never import it. Wired only in `scripts/run_hmm_regime.py` (Issue #6) and `scripts/run_long_short_overlays.py` (Issue #20). |
| `hermes_trading/markov_regime.py` | Legacy 6-state hand-defined Markov classifier | **research only**, mostly historical. `loop._init_markov_live` wires it but it's `enabled: false` by default and rejected per Issue #2 / #6. |
| `hermes_trading/relative_strength.py` | BTC/ETH cross-asset RS scoring + decisions_df builder | **research only**. Consumed by Issue #5, #12, #14, #20 scripts. Not wired into `multi_loop.py`. |
| `scripts/monitor_strategy_decay.py` | Live-trade-log decay alarm (rolling PF / DD / win-rate / consecutive losses vs locked thresholds) | **live-adjacent** — runs on-demand against `state/trades.jsonl`. Issue #15. |
| `state/hmm_regime.yaml` | HMM config (features, n_states, sizing thresholds) | research config |
| `state/markov_regime.yaml`, `state/markov_donchian.yaml` | Markov configs | research configs |
| `state/live_multiasset_long_short_funding.yaml`'s `funding_filter:` block | The risk overlay actually wired into the live worker | live |

**Why funding is "risk", not "alpha"**: funding does not produce
an entry direction. A SuperTrend flip is the *signal*; the
funding gate either lets it through, sizes it down, or blocks it.
That is exactly the role-definition of a risk overlay.

**Why HMM / Markov are "risk", not "alpha"**: same reason. The
HMM identifies "high realised vol — strategies in chop perform
poorly" — that is exposure control, not direction prediction.
Issue #6's report explicitly framed this as a sizing /
filtering overlay.

**Why RS is "risk", not "alpha"**: RS modulates *which asset and
how much* to put on a position when a trend signal fires. The
direction is decided by the SuperTrend flip; RS scales it.

**Position sizing**: this is split. The base size (`position_size_r`
in the strategy yaml) is read from the alpha config but consumed
in the risk / execution layer. The overlay multiplier (HMM,
funding) is pure risk. The per-asset size in multi-asset mode
(`size_per_asset` in the live config) is set at the execution
layer's config level but logically a risk concept.

---

## Execution layer

| module | what it is | live or research only |
|---|---|---|
| `hermes_trading/run.py` | CLI entrypoint — dispatches single-asset vs multi-asset (`--config`), reads `--utc-time`, `--verbose` | **live** |
| `hermes_trading/loop.py` | Single-asset paper worker (legacy single-strategy path) | **live** |
| `hermes_trading/multi_loop.py` | Multi-asset paper worker (Issue #16). Loads funding overlay (Issue #21), evaluates per-asset entries/exits on closed bars (Issue #24), maintains per-asset positions, writes heartbeat | **live** |
| `hermes_trading/positions.py` | Per-asset position state IO + legacy migration | **live** |
| `hermes_trading/backtest.py` | Paper-fill semantics: fee + slippage modeling, position state machine, equity curve / DD tracking | **research only** — also referenced by `loop.py` indirectly (it shares the `_run_state_machine` for paper-fill semantics in walk-forward). |
| `hermes_trading/adapters/` | Exchange / context fetch (price via ccxt with Kraken fallback, on-chain, news, macro stubs) | **live** (`price`) + diagnostics-best-effort (others) |
| `state/live_multiasset.yaml` | Default opt-in multi-asset live config (long-only fallback) | **live** |
| `state/live_multiasset_long_short_funding.yaml` | Opt-in adopted live config (long-short + funding filter, Issue #21) | **live** |
| `state/goal.yaml` | Single-asset entrypoint config (asset + timeframe defaults) | **live** for the legacy single-asset path |

**Paper fill model**: in backtest, `bt._run_state_machine` adds
slippage to entry / stop / exit fills (entry = `close * (1 +
slippage)` for longs, exit at stop = `stop * (1 - slippage)`,
etc.). Live mode doesn't simulate slippage at all — the entry
price is the bar's close as reported by ccxt. **This is a known
modeling asymmetry** (called out in the `ROADMAP.md` infrastructure
list since Issue #2 / #3: "Model fees and slippage in the live
worker (currently zero — paper PnL overstates net edge by ~25 bps
per round-trip)"). It belongs in the execution backlog (Phase 3).

**Position persistence**: `positions.py` writes one JSON file per
asset on every entry, deletes on exit. Restart safety is handled
by `load_positions()` at boot; corrupt files are skipped with a
warning, not unilaterally deleted.

**No order routing**: there is no broker / exchange WRITE path
anywhere in the codebase. Adapters are read-only. This is the
single largest "what is missing" item — the execution layer
exists for paper-mode only.

---

## Diagnostics layer

| module | what it is | what it observes |
|---|---|---|
| `hermes_trading/display.py` | Tick-line formatting (SuperTrend / RSI auto-switch, Issue #17), entry blocker diagnostic (Issue #18), heartbeat field helpers, `split_display_and_signal_rows` (Issue #24) | live worker output |
| `state/heartbeat.json` | Per-poll snapshot of price, RSI, position, SuperTrend direction / line / distance, bullish regime, funding state, portfolio summary | external dashboards, audits |
| `state/trades.jsonl` | Append-only closed-trade log; one JSON per closed trade with full diagnostics | downstream tooling, decay monitor, audit trail |
| `scripts/monitor_strategy_decay.py` | Reads `state/trades.jsonl`, computes rolling PF / DD / win-rate / consecutive losses over configurable windows | live behaviour vs research baseline drift |
| `scripts/replay_live.py` | Walks historical bars through the live engine at compressed speed | building intuition about live behaviour over months in 30 min |
| `scripts/test_multiasset_worker.py` | Self-test for the live worker (137/137 invariants as of Issue #24) | regression net |
| `state/reflect_state.json` | Reflection-loop counter (single-asset only) | informational |
| Research artifacts (`research/*.md`, `results/*.csv` / `*.md`) | Reports + comparison CSVs from every experiment | provenance |

`monitor_strategy_decay.py` is listed under Diagnostics because it
**reports** without taking action. The closely related "decay
alarm" idea — which would automatically lower exposure or pause —
would belong in the Risk layer; it is not implemented (it's on
the Risk backlog).

`replay_live.py` is single-strategy currently (Issue #26 will add
multi-asset support).

---

## Research infrastructure

| module | what it is |
|---|---|
| `hermes_trading/backtest.py` | Bar-by-bar replay with fee + slippage. `_run_state_machine` is shared between single-asset backtests and the walk-forward harness. |
| `hermes_trading/walk_forward.py` | Rolling train/test harness; per-fold regime refit; stitched OOS metrics. |
| `hermes_trading/data.py` | Binance Vision OHLCV loader + cache + timeframe resample. |
| `scripts/run_markov_research.py` | Issue #2 Markov six-mode sweep. |
| `scripts/run_supertrend_extended.py` | Issue #11 48mo SuperTrend extended-history runner. |
| `scripts/run_btc_eth_rs.py` | Issue #5 BTC/ETH RS experiment. |
| `scripts/run_multiasset_supertrend_rs.py` | Issue #12 multi-asset SuperTrend + RS. |
| `scripts/run_eth_vs_btc_analysis.py` | Issue #13 ETH-vs-BTC diagnostic. |
| `scripts/run_hmm_regime.py` | Issue #6 HMM overlay. |
| `scripts/run_top5_parallel.py` | Issue #14 top-5 parallel portfolio. |
| `scripts/run_funding_filter.py` | Issue #7 funding filter. |
| `scripts/run_supertrend_long_short.py` | Issue #19 SuperTrend long-short comparison. |
| `scripts/run_long_short_overlays.py` | Issue #20 overlay sweep. |
| `research/*.md`, `results/*.{csv,md}` | Frozen experimental record. |

These are not loaded by the live worker. Each runner produces a
report and (where relevant) recommends adoption / rejection per
the locked criteria in `ROADMAP.md`.

---

## Cross-cutting / supporting

| module | role |
|---|---|
| `hermes_trading/__init__.py` | `log()`, `now_iso()`, `set_display_time_mode()`, `format_display_time()` (Issue #22), STATE_DIR resolution, JSON / YAML helpers |
| `hermes_trading/reflect.py` | Parameter-tuning loop (used in single-asset mode only; disabled in multi-asset per Issue #16) |
| `hermes_trading/regime_hold.py`, `hermes_trading/score.py` | Older helpers used by historical experiments |

`reflect.py` straddles alpha and risk — it adjusts strategy
parameters between trades. Disabled by default in multi-asset
mode. If re-enabled in future, it belongs in the Risk layer
(it controls exposure / parameter envelope) but its allowlist
operates on the alpha config.

---

## Summary table

| layer | live in adopted config? | research-only? | missing components |
|---|---|---|---|
| Data | ✓ Kraken price; Binance Vision history + funding | — | tick-level data, depth, OI |
| Alpha | ✓ SuperTrend(10, 3) long + (opt-in) short | ✓ RS, donchian, pullback / breakout (research configs) | factor models, multi-TF score, cointegration |
| Risk | ✓ funding filter (opt-in via long-short yaml); regime gate (EMA50/200) inline in alpha | ✓ HMM (research only), Markov (rejected), RS sizing (research only), decay monitor (live-adjacent) | volatility-targeting, dynamic exposure cap, decay alarm |
| Execution | ✓ paper fills, position persistence, restart safety, portfolio cap, multi-asset | — | live slippage model, broker abstraction, market-hours abstraction |
| Diagnostics | ✓ display module, heartbeat, verbose blockers, decay monitor | replay mode (single-strategy only currently) | daily / weekly health reports, multi-asset replay |
| Research infra | n/a | ✓ backtests, walk-forward, 14+ experiment runners | — |
