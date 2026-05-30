# Routing + Sizing comparison — 20260530_082927

- strategy: `state/strategy_v2_long_short.yaml`
- decision timeframe: 4h, 24 months BTC/USDT
- walk-forward: train_bars=1440 (8mo) / test_bars=360 (2mo) / embargo=6 (24h)
- costs: fee=0.001/side, slippage=0.0005

| variant | n | OOS return | max DD | PF | Sharpe | win% | avg_size | folds+ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_no_markov` | 33 | +8.97% | 4.43% | 1.69 | 0.137 | 30.3% | 1.00 | 3/8 |
| `strategy_routing` | 21 | +9.23% | 2.29% | 2.17 | 0.193 | 28.6% | 0.88 | 3/8 |
| `soft_sizing` | 33 | +7.48% | 2.38% | 1.86 | 0.145 | 30.3% | 0.64 | 3/8 |
| `routing_sizing_combined` | 19 | +2.55% | 3.19% | 1.37 | 0.078 | 21.1% | 0.87 | 2/8 |

### By-state breakdown

- **baseline_no_markov**: unknown:33@PF1.69
- **strategy_routing**: up_high_vol:10@PF2.68, up_low_vol:10@PF2.14, nan:1@PF0.00
- **soft_sizing**: up_high_vol:10@PF2.50, up_low_vol:10@PF1.98, down_high_vol:7@PF0.54, down_low_vol:5@PF0.77, nan:1@PF0.00
- **routing_sizing_combined**: up_low_vol:10@PF1.98, up_high_vol:8@PF0.83, nan:1@PF0.00