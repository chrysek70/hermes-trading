# Multi-asset SuperTrend + BTC/ETH RS — 20260530_113106

- supertrend strategy: `/Users/krzys/hermes-trading/state/strategy_supertrend.yaml`
- multi-asset strategy: `/Users/krzys/hermes-trading/state/strategy_supertrend_multiasset_rs.yaml`
- BTC history: 48 months (2022-05-01 -> 2026-04-30)
- ETH history: 48 months (aligned, 8766 bars)
- decision TF: 4h
- walk-forward: train_bars=1440 / test_bars=360 / embargo_bars=6
- costs: fee=0.001/side, slippage=0.0005
- RS config: lookback=30, ratio_ema=30, min_return_advantage=0.0, require_ratio_above_ema=True

| variant | folds | n | OOS return | max DD | PF | Sharpe | win% | folds+ | fold σ | concurrent | by asset |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `btc_supertrend_only` | 20 | 35 | +38.66% | 9.63% | 2.24 | 0.266 | 45.7% | 10/20 | 4.10% | 1 | BTC:35 |
| `btc_supertrend_rs_sizing` | 20 | 27 | +38.03% | 6.29% | 3.01 | 0.338 | 48.1% | 10/20 | 3.85% | 1 | BTC:27 |
| `eth_supertrend_only` | 20 | 30 | +37.86% | 5.30% | 2.92 | 0.336 | 63.3% | 10/20 | 3.98% | 1 | ETH:30 |
| `eth_supertrend_rs_sizing` | 20 | 21 | +17.33% | 3.86% | 3.05 | 0.380 | 66.7% | 8/20 | 2.04% | 1 | ETH:21 |
| `multiasset_supertrend_rs_one_position` | 20 | 39 | +40.99% | 9.61% | 2.48 | 0.276 | 48.7% | 12/20 | 4.16% | 1 | BTC:26; ETH:13 |

### Return contribution by asset

- **btc_supertrend_only**: BTC:+0.3523
- **btc_supertrend_rs_sizing**: BTC:+0.3422
- **eth_supertrend_only**: ETH:+0.3388
- **eth_supertrend_rs_sizing**: ETH:+0.1648
- **multiasset_supertrend_rs_one_position**: BTC:+0.3064; ETH:+0.0600

### By RS / regime state

- **btc_supertrend_only**: unknown:35
- **btc_supertrend_rs_sizing**: rs_partial:7; rs_strong:20
- **eth_supertrend_only**: unknown:30
- **eth_supertrend_rs_sizing**: rs_strong:10; rs_partial:11
- **multiasset_supertrend_rs_one_position**: rs_partial:11; rs_strong:28

### By exit reason

- **btc_supertrend_only**: stop:32; end:3
- **btc_supertrend_rs_sizing**: stop:24; end:3
- **eth_supertrend_only**: stop:27; end:3
- **eth_supertrend_rs_sizing**: stop:19; end:2
- **multiasset_supertrend_rs_one_position**: stop:35; end:4