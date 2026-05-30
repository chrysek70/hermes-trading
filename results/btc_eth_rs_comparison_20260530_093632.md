# BTC/ETH relative-strength experiment — 20260530_093632

- baseline strategy: `state/strategy_v2_long_short.yaml`
- supertrend strategy: `state/strategy_supertrend.yaml`
- RS-enabled strategy: `state/strategy_supertrend_rs.yaml`
- BTC history: 48 months (2022-05-01 -> 2026-04-30)
- ETH history: 48 months (aligned, 8766 bars)
- decision TF: 4h
- walk-forward: train_bars=1440 / test_bars=360 / embargo_bars=6
- costs: fee=0.001/side, slippage=0.0005 (unchanged)
- RS config: lookback=30, ratio_ema=30, min_btc_minus_eth_return=0.0, require_ratio_above_ema=true

| variant | folds | n | OOS return | max DD | PF | Sharpe | win% | folds+ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_v2` | 20 | 103 | +3.28% | 12.74% | 1.09 | 0.027 | 25.2% | 8/20 |
| `supertrend_only` | 20 | 35 | +38.66% | 9.63% | 2.24 | 0.266 | 45.7% | 10/20 |
| `supertrend_with_btc_eth_rs_filter` | 20 | 20 | +35.43% | 7.07% | 3.33 | 0.384 | 55.0% | 8/20 |
| `supertrend_with_btc_eth_rs_sizing` | 20 | 27 | +38.03% | 6.29% | 3.01 | 0.338 | 48.1% | 10/20 |

### By RS / regime state

- **baseline_v2**: unknown:103
- **supertrend_only**: unknown:35
- **supertrend_with_btc_eth_rs_filter**: rs_strong:20
- **supertrend_with_btc_eth_rs_sizing**: rs_partial:7; rs_strong:20

### By exit reason

- **baseline_v2**: stop:68; end:4; trail_exit:20; target_rsi:11
- **supertrend_only**: stop:32; end:3
- **supertrend_with_btc_eth_rs_filter**: stop:18; end:2
- **supertrend_with_btc_eth_rs_sizing**: stop:24; end:3