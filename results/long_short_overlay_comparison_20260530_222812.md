# BTC/ETH long-short + overlays — 20260530_222812

- long-short strategy: `/Users/krzys/hermes-trading/state/strategy_supertrend_long_short.yaml`
- HMM config: `/Users/krzys/hermes-trading/state/hmm_regime.yaml` (per-asset per-fold fit; train-only mapping)
- RS config: lookback=30, ratio_ema=30 (direction-aware: long uses own decision, short uses other asset's)
- funding overlay: long-side blocks at p95 (Issue #7); short-side mirrors at p5
- universe: BTC/USDT + ETH/USDT (parallel)
- 48mo span: 2022-05-01 -> 2026-04-30 (8766 bars)
- walk-forward: train=1440 / test=360 / embargo=6
- costs: fee=0.001/side, slippage=0.0005

Adoption (primary): DD <= 5.54% AND PF >= 3.26 AND return >= +139.47% AND trades >= 100.
Adoption (secondary): DD <= 5.54% AND PF >= 3.00 AND return >= +120% AND trades >= 100.

| variant | folds | n | L | S | OOS return | L ret | S ret | max DD | PF | Sharpe | win% | folds+ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `btc_eth_long_short_baseline` | 20 | 129 | 65 | 64 | +139.47% | +34.56% | +55.18% | 5.76% | 3.26 | 0.379 | 57.4% | 16/20 |
| `btc_eth_long_short_hmm_filter` | 20 | 74 | 40 | 34 | +49.25% | +21.47% | +19.65% | 4.29% | 3.04 | 0.339 | 54.1% | 15/20 |
| `btc_eth_long_short_hmm_sizing` | 20 | 74 | 40 | 34 | +49.25% | +21.47% | +19.65% | 4.29% | 3.04 | 0.339 | 54.1% | 15/20 |
| `btc_eth_long_short_funding_filter` | 20 | 123 | 63 | 60 | +139.71% | +34.76% | +55.07% | 4.64% | 3.35 | 0.391 | 58.5% | 16/20 |
| `btc_eth_long_short_funding_sizing` | 20 | 123 | 63 | 60 | +133.64% | +34.23% | +52.95% | 4.71% | 3.34 | 0.386 | 58.5% | 16/20 |
| `btc_eth_long_short_rs_sizing` | 20 | 92 | 48 | 44 | +74.61% | +25.35% | +31.59% | 4.09% | 3.64 | 0.408 | 59.8% | 17/20 |