# Funding-rate filter experiment — 20260530_152128

- supertrend strategy: `/Users/krzys/hermes-trading/state/strategy_supertrend.yaml`
- universe: BTCUSDT, ETHUSDT
- BTC + ETH: 48mo (2022-05-01 -> 2026-04-30, 8766 bars)
- funding source: Binance Vision (futures/um/monthly/fundingRate)
- rolling percentile window: 180 bars (~30 days @ 4h)
- thresholds: block at p95.0, half-size at p90.0
- decision TF: 4h
- walk-forward: train=1440 / test=360 / embargo=6
- costs: fee=0.001/side, slippage=0.0005

Adoption: must improve PF or DD without destroying trade count.

| variant | folds | n | OOS return | max DD | PF | Sharpe | win% | folds+ | by asset | by funding state |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `eth_supertrend_baseline` | 20 | 30 | +37.86% | 5.30% | 2.92 | 0.336 | 63.3% | 10/20 | ETH:30 | no_filter:30 |
| `eth_supertrend_funding_filter` | 20 | 28 | +38.46% | 5.30% | 3.17 | 0.357 | 64.3% | 10/20 | ETH:28 | no_filter:28 |
| `btc_eth_parallel_baseline` | 20 | 65 | +39.72% | 5.54% | 2.50 | 0.296 | 53.8% | 11/20 | ETHUSDT:30; BTCUSDT:35 | no_filter:65 |
| `btc_eth_parallel_funding_filter` | 20 | 63 | +40.01% | 4.68% | 2.57 | 0.304 | 54.0% | 11/20 | ETHUSDT:28; BTCUSDT:35 | funding_normal:62; funding_overheated:1 |
| `btc_eth_parallel_funding_sizing` | 20 | 63 | +39.28% | 4.68% | 2.55 | 0.299 | 54.0% | 11/20 | ETHUSDT:28; BTCUSDT:35 | funding_normal:62; funding_overheated:1 |