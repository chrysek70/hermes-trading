# SuperTrend(10, 3) comparison — 20260530_092000

- baseline strategy: `state/strategy_v2_long_short.yaml`
- supertrend strategy: `state/strategy_supertrend.yaml`
- decision TF: 4h, history: 24 months BTC/USDT
- walk-forward: train_bars=1440 / test_bars=360 / embargo_bars=6
- costs: fee=0.001/side, slippage=0.0005

| variant | n | OOS return | max DD | PF | Sharpe | win% | folds+ |
|---|---:|---:|---:|---:|---:|---:|---:|
| `baseline` | 33 | +8.97% | 4.43% | 1.69 | 0.137 | 30.3% | 3/8 |
| `supertrend_only` | 9 | +13.00% | 1.49% | 9.02 | 0.666 | 77.8% | 4/8 |
| `supertrend_plus_routing` | 4 | +2.47% | 0.07% | 34.54 | 0.978 | 75.0% | 3/8 |

### By setup

- **baseline**: breakout:14; pullback_short:4; breakout_short:12; pullback:3
- **supertrend_only**: supertrend:9
- **supertrend_plus_routing**: supertrend:4

### By exit reason

- **baseline**: stop:21; trail_exit:7; target_rsi:4; end:1
- **supertrend_only**: stop:9
- **supertrend_plus_routing**: stop:4