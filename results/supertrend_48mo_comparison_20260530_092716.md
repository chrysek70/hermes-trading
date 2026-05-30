# SuperTrend(10, 3) on 48-month history — 20260530_092716

- baseline strategy: `state/strategy_v2_long_short.yaml`
- supertrend strategy: `state/strategy_supertrend.yaml` (unchanged from issue #4)
- history: 48 months BTC/USDT (2022-05-01 -> 2026-04-30)
- decision TF: 4h
- walk-forward: train_bars=1440 / test_bars=360 / embargo_bars=6 (identical to issue #4)
- costs: fee=0.001/side, slippage=0.0005 (unchanged)

| variant | folds | n | OOS return | max DD | PF | Sharpe | win% | folds+ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline` | 20 | 103 | +3.28% | 12.74% | 1.09 | 0.027 | 25.2% | 8/20 |
| `supertrend_only` | 20 | 35 | +38.66% | 9.63% | 2.24 | 0.266 | 45.7% | 10/20 |
| `supertrend_plus_routing` | 20 | 20 | +29.56% | 5.95% | 3.16 | 0.337 | 50.0% | 8/20 |

### By setup

- **baseline**: pullback_short:12; pullback:20; breakout:42; breakout_short:29
- **supertrend_only**: supertrend:35
- **supertrend_plus_routing**: supertrend:20

### By exit reason

- **baseline**: stop:68; end:4; trail_exit:20; target_rsi:11
- **supertrend_only**: stop:32; end:3
- **supertrend_plus_routing**: stop:19; end:1