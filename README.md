# hermes-trading

A personal research framework for crypto paper-trading on BTC/USDT.

> **Architecture:** the bot is layered as **Alpha → Risk → Execution →
> Diagnostics → Research feedback**. See [`ARCHITECTURE.md`](ARCHITECTURE.md)
> for the layer-by-layer map of every component, and the
> "Alpha / Risk / Execution Roadmap" section in
> [`ROADMAP.md`](ROADMAP.md) for backlogs grouped by layer.

Paper-mode only — no real funds, no exchange writes. The repo is a sandbox
for evaluating multi-signal strategies, regime-aware exposure control, and
walk-forward validation discipline on a single asset.

## What the bot does

- Polls OHLCV from a multi-exchange-fallback ccxt adapter (Kraken primary).
- Evaluates a v2 long-short signal engine on 4h decision bars:
  - Long pullback (RSI < 32, at 21-EMA, in EMA50/200 uptrend)
  - Long breakout (3-bar play above session VWAP, in uptrend)
  - Short mirrors of the above for EMA50/200 downtrends
- Manages a single open position at a time with ATR stops, trailing exits,
  regime-flip and time-stop guards.
- Logs every closed trade to `state/trades.jsonl`.
- A reflection loop (deterministic + optional LLM) proposes one parameter
  change per N closed trades; allowlist-gated; all prior versions archived
  to `state/history/`.
- Paper-mode only. No real exchange writes, no real keys, no real money.

## Current architecture

```
hermes_trading/
  run.py                 entrypoint
  loop.py                live worker — async tick loop
  signals.py             indicator + entry/exit engine (RSI, EMA, ATR, VWAP,
                         3-bar play, Donchian; shared by live + backtest)
  backtest.py            bar-by-bar replay with fees + slippage + by-state CSV
  walk_forward.py        rolling train/test harness; per-fold Markov refit
  markov_regime.py       discrete first-order Markov over 6 hand-defined states
                         + mode-aware decision API (hard_filter / soft_sizing /
                         bad_regime_avoidance / regime_features_only /
                         strategy_routing / routing_sizing)
  regime_hold.py         buy-and-hold with regime-based size scaling
  reflect.py             one-variable-per-cycle parameter update loop
  score.py               composite goal-scoring helper
  data.py                Binance Vision historical klines loader + resampler
  adapters/              price / on-chain / news / macro best-effort feeds
state/                   strategy + goal + regime YAML; trade and reflection log
results/                 walk-forward summaries (CSV + MD)
research/                research notes, audit reports, plans
scripts/                 standalone validators and research runners
```

## How to run

### Backtest a single strategy

```bash
uv run python -m hermes_trading.backtest \
    --n-months 24 --timeframe 4h --warmup 210 \
    --strategy state/strategy_v2_long_short.yaml \
    --trades-csv results/trades_detailed_<ts>.csv
```

### Walk-forward (true OOS)

```bash
uv run python -m hermes_trading.walk_forward \
    --n-months 24 --timeframe 4h \
    --train-bars 1440 --test-bars 360 --embargo-bars 6 \
    --strategy state/strategy_v2_long_short.yaml
```

Add `--markov state/markov_regime.yaml --markov-enable --mode soft_sizing`
to walk-forward with a regime layer.

### Markov research sweep (six modes)

```bash
uv run python scripts/run_markov_research.py \
    --n-months 24 --timeframe 4h \
    --train-bars 1440 --test-bars 360 --embargo-bars 6
```

### Live paper worker — single asset (default)

```bash
cd ~/hermes-trading && export PATH="$HOME/.local/bin:$PATH"
uv run python -m hermes_trading.run
# override the asset from goal.yaml:
uv run python -m hermes_trading.run --asset ETH/USDT
```

Polls every 10 s by default; configurable via `HERMES_POLL_SECONDS`.
Persists open position (`state/position.json`) and reflection counter
(`state/reflect_state.json`) across restarts.

### Live paper worker — multi-asset (Issue #16)

```bash
# Long-only fallback (Issue #16 — still the default if you don't
# explicitly choose the long-short config):
uv run python -m hermes_trading.run --config state/live_multiasset.yaml

# Adopted long-short + funding-filter candidate (Issue #21, wired from
# the Issue #20 research adoption):
uv run python -m hermes_trading.run \
    --config state/live_multiasset_long_short_funding.yaml --verbose
```

#### Display timezone (Issue #22)

By default the worker prints tick lines in the **host OS local timezone** with a tz abbreviation (e.g. `22:51:44 EDT`). Pass any of `--utc-time` / `--bot-time` / `--transaction-time` to force UTC instead (e.g. `02:51:44`).

```bash
# Default — host local time:
uv run python -m hermes_trading.run --config state/live_multiasset.yaml

# Force UTC display (useful for cross-machine debugging):
uv run python -m hermes_trading.run \
    --config state/live_multiasset.yaml --utc-time
# (--bot-time and --transaction-time are aliases for the same thing.)

# Combined with verbose:
uv run python -m hermes_trading.run \
    --config state/live_multiasset_long_short_funding.yaml --verbose
```

**Persisted artifacts always remain UTC** regardless of the display
flag — `state/heartbeat.json`, `state/trades.jsonl`, `state/positions/`,
research reports, decay-monitor output, every backtest result. The
display setting is presentation only.

#### Live signal parity (Issue #24)

The live worker evaluates entries and SuperTrend flip / time / regime
exits on the **most recently closed candle** so live behaviour matches
what every backtest measured. The current in-progress candle is still
used for the tick display, the heartbeat live-price field, and
intra-bar stop monitoring so paper stops remain responsive within the
running bar. `signals.py` is unchanged; the split is in the worker
orchestration only (`hermes_trading/multi_loop.py` and
`hermes_trading/loop.py`, via the small
`hermes_trading.display.split_display_and_signal_rows` helper).

Reads `state/live_multiasset.yaml` (assets list, timeframe,
`max_open_positions`, shared strategy yaml). Maintains one position
per asset in `state/positions/<KEY>.json`, writes a portfolio-level
heartbeat at `state/heartbeat.json`, and appends extended trade rows
to `state/trades.jsonl` (asset, setup, entry_time / exit_time,
return_pct, net_return_pct, position_size, holding_bars, …). Reflection
is intentionally disabled in multi-asset mode.

If `state/position.json` exists when multi-asset mode starts, it is
migrated into the new per-asset layout once and the original is backed
up as `state/position.json.bak.<UTC-iso>`.

```bash
# Verify both modes wire up correctly (no exchange / network required):
uv run python scripts/test_multiasset_worker.py
```

### Tick display — auto-switches with the strategy (Issue #17)

The per-tick log line auto-selects fields based on the active strategy:

- **SuperTrend mode** (`setups.supertrend.enabled: true`) — one line per asset showing the SuperTrend direction, line, and distance from price. RSI is hidden from the tick line but stays in the heartbeat and the closed-trade rows.
- **Legacy v2 mode** (pullback / breakout) — preserved byte-for-byte; same `tick {asset} {price} rsi=… v{ver} pos=… regime=…` line as before.

Sample SuperTrend ticks:

```
tick BTC/USDT close=73890.80 st=UP line=72150.22 dist=+2.41% v=v3-supertrend-01 pos=flat
tick ETH/USDT close=3840.15 st=DOWN line=3922.40 dist=-2.10% v=v3-supertrend-01 pos=flat
tick ETH/USDT close=3901.20 st=UP line=3720.00 dist=+4.87% v=v3-supertrend-01 pos=long setup=supertrend uPnL=+1.20%
portfolio open=1/2  realized=+0.000%  unrealized=+0.601%
```

Add `--verbose` to append RSI back into the SuperTrend tick line **and** print "why no trade" diagnostics per asset (Issue #18):

```bash
uv run python -m hermes_trading.run --config state/live_multiasset.yaml --verbose
```

Sample verbose output:

```
tick BTC/USDT close=74097.90 st=DOWN line=75559.66 dist=-1.93% v=v3-supertrend-01 pos=flat rsi=47.0
  waiting_for: SuperTrend flip UP + EMA50 > EMA200
  blockers: supertrend_direction=DOWN, close below supertrend_line by 1.93%, ema50_below_ema200
tick ETH/USDT close=2030.23 st=DOWN line=2076.91 dist=-2.25% v=v3-supertrend-01 pos=flat rsi=48.5
  waiting_for: SuperTrend flip UP + EMA50 > EMA200
  blockers: supertrend_direction=DOWN, close below supertrend_line by 2.25%
```

When the close is within ~1% of the SuperTrend line, a `near_entry:` line shows the exact gap. When all entry conditions are satisfied but the portfolio cap is full, a `blocked_by: portfolio max_open_positions reached` line appears instead. Default (non-verbose) output is unchanged.

## Current best out-of-sample result

24-month walk-forward on BTC/USDT 4h, fees 10 bps/side + slippage 5 bps,
8 folds (train 1440 / test 360 / embargo 6 bars):

| variant | trades | OOS return | max DD | PF |
|---|---:|---:|---:|---:|
| baseline (no Markov) | 33 | **+8.97%** | 4.43% | **1.69** |
| Markov `strategy_routing` (MTF off) | 19 | +3.94% | 3.44% | 1.54 |
| Markov `soft_sizing` (MTF off) | 33 | +7.48% | 2.38% | 1.86 |
| Donchian-20 trend-following | 33 | -2.16% | 6.79% | 0.90 |
| Donchian + Markov routing | 25 | -2.97% | 6.54% | 0.84 |

The earlier widely-quoted `strategy_routing` PF of 2.17 came from a
multi-timeframe code path that filled missing `long_allowed` rows as True
(see `BUGS.md`). With that path disabled for a clean comparison, routing
PF lands at 1.54 and the baseline is the honest current best.

Buy-and-hold BTC over the same window returned ~+27% at ~50% drawdown.
The strategies above are risk-managed underperformers in absolute terms,
defensible only on a risk-adjusted basis.

## Extended-history validation (48mo)

48-month walk-forward on BTC/USDT 4h, identical fees / slippage / fold
size (train 1440 / test 360 / embargo 6), 20 folds, run for Issue #11:

| variant | trades | OOS return | max DD | PF |
|---|---:|---:|---:|---:|
| baseline (no Markov) | 103 | +3.28% | 12.74% | 1.09 |
| **SuperTrend(10, 3) only** | **35** | **+38.66%** | 9.63% | **2.24** |
| SuperTrend + Markov routing | 20 | +29.56% | **5.95%** | 3.16 |

**SuperTrend(10, 3) — first variant since v2 to clear both adoption
gates (PF > 1.69, trades ≥ 30) on an OOS walk-forward.** Adopted as a
research candidate. The live worker is unchanged and continues to run
v2 long-short — SuperTrend is queued for BTC/ETH cross-asset validation
(Issue #5) before any further consideration. See
`research/supertrend_48mo_report.md`.

The v2 baseline degrades on extended history (PF 1.69 → 1.09), which
is itself useful evidence that the original 24mo window was a
favorable regime for the pullback/breakout setups.

## BTC/ETH relative-strength context (Issue #5)

ETH used as **market context only** (not traded). Same 48mo data, fees
and fold geometry as above. RS windows fixed at conventional defaults
(`lookback=30`, `ratio_ema=30`); no parameter sweeps.

| variant | trades | OOS return | max DD | PF | Sharpe |
|---|---:|---:|---:|---:|---:|
| `supertrend_only` | 35 | +38.66% | 9.63% | 2.24 | 0.266 |
| `supertrend_with_btc_eth_rs_filter` | 20 | +35.43% | **7.07%** | **3.33** | **0.384** |
| `supertrend_with_btc_eth_rs_sizing` | 27 | +38.03% | **6.29%** | 3.01 | 0.338 |

**Not adopted** — both RS variants beat supertrend_only on PF and DD
(thesis materially supported: PF +34–49%, DD -27–35%) but neither
clears the 30-trade discipline gate. The mechanism is validated;
sample size is the blocker. The natural next experiment is the
multi-asset extension (SuperTrend + RS applied to ETH as a traded
asset on the same engine), which roughly doubles the sample. See
`research/btc_eth_relative_strength_report.md`.

## Multi-asset SuperTrend + RS portfolio (Issue #12)

ETH added as a tradeable asset on the same engine; one position open
at a time across the BTC + ETH universe. Same SuperTrend(10, 3), same
RS config from Issue #5, same fees / fold geometry.

| variant | trades | OOS return | max DD | PF | win % |
|---|---:|---:|---:|---:|---:|
| `btc_supertrend_only` (reference) | 35 | +38.66% | 9.63% | 2.24 | 45.7% |
| **`eth_supertrend_only`** | 30 | +37.86% | **5.30%** | **2.92** | **63.3%** |
| `eth_supertrend_rs_sizing` | 21 | +17.33% | 3.86% | 3.05 | 66.7% |
| **`multiasset_supertrend_rs_one_position`** | **39** | **+40.99%** | 9.61% | **2.48** | 48.7% |

**Two variants adopted as research candidates** — first since v2 to
clear all four gates (trades ≥ 30, PF > 2.24, DD ≤ 9.63%, fold
consistency not worse):

- The multi-asset portfolio (the spec result): 39 trades, PF 2.48,
  DD 9.61%, 12/20 folds positive.
- ETH solo (surprise side finding): 30 trades, PF 2.92, DD 5.30%,
  63.3% win rate. Better risk-adjusted than either BTC variant.

SuperTrend(10, 3) appears to be a cleaner signal on ETH 4h than on
BTC 4h over this window. The RS sizing overlay helps BTC but *hurts*
ETH (the symmetric ETH overlay only allows ETH long when ETH is
stronger than BTC, which is the minority condition over this period).

Live worker unchanged. See `research/multiasset_supertrend_rs_report.md`.

## HMM regime overlay (Issue #6)

Optional EM-fit 2-state Gaussian HMM on causal market features
(log-return, realised vol, ATR%, EMA50 slope, SuperTrend distance).
Per-fold fit on train only; volatility-based state mapping
(favorable = lower vol). Decisions plumbed through the existing
`decisions_df` overlay.

| variant | trades | OOS return | max DD | PF | win % |
|---|---:|---:|---:|---:|---:|
| `supertrend_only_btc` (reference) | 35 | +38.66% | 9.63% | 2.24 | 45.7% |
| `supertrend_hmm_filter_btc` | **24** | +49.98% | **3.79%** | **4.01** | 54.2% |
| `supertrend_only_eth` (reference) | 30 | +37.86% | 5.30% | 2.92 | 63.3% |
| `supertrend_hmm_filter_eth` | **17** | +27.80% | **4.13%** | **4.27** | 70.6% |

**Not adopted** — fails the 30-trade gate on both assets, even
though PF lifts +79% (BTC) / +46% (ETH) and DD drops -61% (BTC)
/ -22% (ETH). Third regime mechanism in this repo to clear PF / DD
and fail trade count (after RS filter and routing). Pattern argues
for a parallel multi-asset extension as the natural next step.
Optional dependency — `hmmlearn` not required by the live worker.
See `research/hmm_regime_report.md`.

## Top-5 parallel portfolio (Issue #14)

Fixed universe (BTC, ETH, SOL, BNB, XRP — all with 48mo data) traded
in parallel. Equal risk per asset (1/N), no rotation, up to 5
concurrent positions, no per-bar selection.

| variant | trades | OOS return | max DD | PF | win % |
|---|---:|---:|---:|---:|---:|
| `top5_supertrend_parallel` | 155 | +40.70% | **2.49%** | 2.19 | 51.6% |
| `top5_hmm_filter_parallel` | 95 | +26.74% | 1.86% | 2.49 | 51.6% |
| **`btc_eth_reference_parallel`** | **65** | **+39.72%** | **5.54%** | **2.50** | **53.8%** |

The spec-headline top-5 variant **fails PF gate by 0.05** (XRP drag,
SOL/BNB at lower per-asset PF dilute the BTC/ETH edge). But the
2-asset reference `btc_eth_reference_parallel` **clears all five
locked gates** (trades ≥ 60, PF > 2.24, DD ≤ 9.63%, return > 38.66%,
max single-asset profit share ≤ 60%) and is a **strict upgrade over
the Issue #12 one-position variant**: more trades (65 vs 39), much
lower DD (5.54% vs 9.61%), equal PF.

**Parallel form is the new canonical multi-asset framework** —
adopted as research candidate. Live worker unchanged. See
`research/top5_parallel_portfolio_report.md`.

## Funding-rate filter (Issue #7)

Three-phase research: data audit (Binance Vision funding archives
from 2020-01 onwards, fully cover the 48mo window), diagnostics
(funding has near-zero linear predictive value, U-shaped bucket
pattern), and locked-spec filter test (block at p95 / half-size at
p90).

| variant | trades | OOS return | max DD | PF |
|---|---:|---:|---:|---:|
| `btc_eth_parallel_baseline` (reference) | 65 | +39.72% | 5.54% | 2.50 |
| **`btc_eth_parallel_funding_filter`** | 63 | +40.01% | **4.68%** | **2.57** |
| `btc_eth_parallel_funding_sizing` | 63 | +39.28% | 4.68% | 2.55 |
| `eth_supertrend_funding_filter` | 28 | +38.46% | 5.30% | 3.17 |

**Marginal pass — adopted as small research candidate, with caveat
that the effect is within fold noise.** Only 2 of 65 trades affected
by the filter (SuperTrend entries rarely coincide with extreme funding
on this universe). PF and DD both improve marginally on the parallel
portfolio. Not a primary strategy. See
`research/funding_rate_filter_report.md`.

## Volume confirmation overlay — opt-in live (Issue #38)

After Issue #35 (research: PF 4.53 → 5.79, DD −34%, trade count
preserved) and Issue #29 (live fill parity), the volume confirmation
filter is wired into live paper mode as an **opt-in additive** on a
new yaml. The existing vol_sizing yaml remains unchanged as a
control.

Run the **current vol_sizing candidate** (no volume gate):

```bash
uv run python -m hermes_trading.run \
    --config state/live_multiasset_long_short_funding_vol.yaml --verbose
```

Run the **new volume-confirmed candidate** (opt-in):

```bash
uv run python -m hermes_trading.run \
    --config state/live_multiasset_long_short_funding_vol_volconf.yaml --verbose
```

Replay the same config:

```bash
uv run python scripts/replay_live.py \
    --config state/live_multiasset_long_short_funding_vol_volconf.yaml \
    --n-months 24 --bars-per-second 50 --quiet-flat
```

The volume gate composes with the existing layers:

```
entry_allowed = SuperTrend_signal AND funding_allow AND volume_allow
final_size    = base_size × vol_multiplier
```

Volume confirmation is a hard entry gate (block low-volume flips);
vol_sizing remains the only sizing modulator. The rule (locked from
Issue #35): require signal-bar volume to be at or above the 20-bar
rolling mean. Fail-open during indicator warmup.

Verbose adds:

```
  volume: signal=12345.67 mean20=10000.00 ratio=1.23 decision=allow
```

When blocked:

```
  blocked_by: volume_confirmation low_volume_flip (...)
```

Closed-trade rows on `state/trades.jsonl` gain
`volume_confirmation_enabled`, `signal_volume`, `volume_mean_20`,
`volume_ratio`, `volume_confirmation_decision`. Heartbeat carries the
same per-asset block. Decay-monitor schema preserved.

## Vol-sizing overlay — opt-in live (Issue #33)

After Issues #27 (research) and #29 (live fill parity) closed, the
`vol_sizing` overlay is wired into live paper mode as an **opt-in
additive** on a NEW config. The existing default config remains
unchanged as a control.

Run the **current adopted candidate** (no vol sizing):

```bash
uv run python -m hermes_trading.run \
    --config state/live_multiasset_long_short_funding.yaml --verbose
```

Run the **new vol_sizing candidate** (opt-in):

```bash
uv run python -m hermes_trading.run \
    --config state/live_multiasset_long_short_funding_vol.yaml --verbose
```

The vol overlay sizes each entry by the realised-volatility quartile
at the signal bar:

- Q1 (low vol)  → 1.00 × base size
- Q2 / Q3 (mid) → 0.50 × base size
- Q4 (high vol) → 0.25 × base size

Quartile thresholds are computed from a rolling 12-month window of
trailing 24-bar realised volatility, strictly before the current bar
(no future leakage). Funding remains a hard entry gate; vol sizing
only ever reduces exposure. The locked parameters (window, thresholds,
multipliers) come from Issue #27 and are NOT tunable in the live
config.

Verbose mode adds per-asset lines like:

```
  vol: rv24=1.23% bucket=Q4 mult=0.25 q=[0.60%,0.90%,1.40%]
```

Closed-trade rows on `state/trades.jsonl` carry `base_size`,
`vol_multiplier`, `final_size`, `realized_vol_24`, `vol_bucket`,
`vol_q1`, `vol_q2`, `vol_q3` for decay-monitor / audit consumption.
Heartbeat (`state/heartbeat.json`) carries the same per-asset block
plus `vol_sizing_enabled: true`.

**Replay matches** (Issue #34): `scripts/replay_live.py --config
state/live_multiasset_long_short_funding_vol.yaml …` applies the
exact same `LiveVolSizingOverlay` and produces the same vol-context
fields on the trade CSV, the same `vol BTC/USDT rv24=… bucket=Q…
mult=…` verbose line, and the same `size=… vol_mult=…` ENTER line
the live worker emits.

## Replay mode (Issue #26)

`scripts/replay_live.py` walks historical bars through the same
`signals` module the live worker uses, at any speed:

```bash
# Legacy single-asset path (unchanged):
uv run python scripts/replay_live.py \
    --strategy state/strategy_supertrend_long_short.yaml \
    --n-months 24 --bars-per-second 20 --quiet-flat

# Multi-asset config path (Issue #26) — replays the same config the
# live worker reads, including BTC + ETH, the funding filter, and
# the portfolio cap:
uv run python scripts/replay_live.py \
    --config state/live_multiasset_long_short_funding.yaml \
    --n-months 24 --bars-per-second 20 --quiet-flat \
    --trades-out results/replay_trades_$(date +%Y%m%d_%H%M%S).csv
```

In both modes, entry / SuperTrend flip / time exits evaluate on the
most recently CLOSED bar (matches Issue #24); stops can still fire
intra-bar on the current bar's low / high. In `--config` mode the
script also enforces `max_open_positions`, looks up historical
funding by signal-bar timestamp via the live worker's
`LiveFundingOverlay`, and emits the same `tick … st=UP line=… pos=…`
display lines `multi_loop.py` produces. The `--trades-out` CSV
columns are: `asset, direction, entry_time, exit_time, entry_price,
exit_price, return_pct, net_return_pct, setup, exit_reason,
bars_held, funding_decision`.

Replay is research / educational tooling. It does NOT trade, does
NOT write to `state/`, and is independent of the live worker.

## Live decay monitor (Issue #15)

`scripts/monitor_strategy_decay.py` reads the live worker's
`state/trades.jsonl` and reports whether recent paper-trading
performance has degraded versus research-time baselines:

```bash
uv run python scripts/monitor_strategy_decay.py
# add --json --output results/decay_<ts>.json for a structured log
# add --self-test to run the built-in regression checks
```

Per-window metrics (default windows 10 / 25 / 50): trade count,
total / avg / median return, win rate, profit factor, max drawdown,
average holding time, worst / best trade, trailing consecutive
losses, latest trade time. Warns when PF < 1.20, win rate < 65% of
baseline, DD > 125% of baseline, ≥ 4 consecutive losses, or total
return < 0. Exit code `0` OK, `1` DEGRADED, `2` insufficient data.

**Monitor / report only** — does not modify trading decisions,
resize positions, or auto-disable strategies. Cron / Slack / Datadog
integration is intentionally out of scope and easy to add later via
the exit code or the JSON output. See
`research/decay_monitor_report.md`.

## Disclaimer

This repository is **research code**. No part of it is financial advice,
trading recommendation, or guarantee of performance. Paper-trading
results have **not** been validated live. Past performance does not
predict future results. Use at your own risk.
