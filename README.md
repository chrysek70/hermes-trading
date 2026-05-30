# hermes-trading

A personal research framework for crypto paper-trading on BTC/USDT.
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

### Live paper worker

```bash
cd ~/hermes-trading && export PATH="$HOME/.local/bin:$PATH"
uv run python -m hermes_trading.run
```

Polls every 10 s by default; configurable via `HERMES_POLL_SECONDS`.
Persists open position and reflection counter across restarts.

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

## Disclaimer

This repository is **research code**. No part of it is financial advice,
trading recommendation, or guarantee of performance. Paper-trading
results have **not** been validated live. Past performance does not
predict future results. Use at your own risk.
