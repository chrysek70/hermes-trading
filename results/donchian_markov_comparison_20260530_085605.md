# Donchian × Markov comparison — 20260530_085605

- baseline strategy: `state/strategy_v2_long_short.yaml`
- donchian strategy: `state/strategy_donchian_markov.yaml`
- baseline markov:   `state/markov_regime.yaml` (mode=strategy_routing)
- donchian markov:   `state/markov_donchian.yaml` (mode=strategy_routing, donchian_breakout in up_* routes)
- decision TF: 4h, history: 24 months BTC/USDT
- walk-forward: train_bars=1440 / test_bars=360 / embargo_bars=6
- costs: fee=0.001/side, slippage=0.0005

| variant | n | OOS return | max DD | PF | Sharpe | win% | avg_size | folds+ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline` | 33 | +8.97% | 4.43% | 1.69 | 0.137 | 30.3% | 1.00 | 3/8 |
| `strategy_routing` | 19 | +3.94% | 3.44% | 1.54 | 0.099 | 21.1% | 0.89 | 2/8 |
| `donchian_only` | 33 | -2.16% | 6.79% | 0.90 | -0.038 | 33.3% | 1.00 | 3/8 |
| `donchian_plus_routing` | 25 | -2.97% | 6.54% | 0.84 | -0.071 | 32.0% | 0.96 | 3/8 |

### By setup count

- **baseline**: breakout=14, pullback_short=4, breakout_short=12, pullback=3
- **strategy_routing**: breakout=8, breakout_short=9, pullback_short=2
- **donchian_only**: donchian_breakout=24, breakout=9
- **donchian_plus_routing**: donchian_breakout=22, breakout=3

### By markov state

- **baseline**: unknown:n33@PF1.69
- **strategy_routing**: up_low_vol:n10@PF2.14, up_high_vol:n8@PF0.88, nan:n1@PF0.00
- **donchian_only**: unknown:n33@PF0.90
- **donchian_plus_routing**: up_low_vol:n21@PF1.05, up_high_vol:n4@PF0.02