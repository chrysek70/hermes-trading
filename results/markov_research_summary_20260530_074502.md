# Markov research summary — 20260530_074502

- strategy: `/Users/krzys/hermes-trading/state/strategy_v2_long_short.yaml`
- decision timeframe: `4h`
- history: `24` months
- walk-forward: train_bars=`1440` test_bars=`360` embargo=`6`
- costs: fee=`0.001`/side, slippage=`0.0005`

| variant | n | OOS return | max DD | PF | Sharpe | win % | avg_size | folds+ |
|---|---|---|---|---|---|---|---|---|
| `baseline_no_markov` | 33 | +8.97% | 4.43% | 1.69 | 0.137 | 30.3% | 1.00 | 3/8 |
| `hard_filter` | 24 | +7.70% | 4.43% | 1.68 | 0.142 | 25.0% | 1.00 | 3/8 |
| `soft_sizing` | 33 | +7.48% | 2.38% | 1.86 | 0.145 | 30.3% | 0.64 | 3/8 |
| `bad_regime_avoidance` | 33 | +8.97% | 4.43% | 1.69 | 0.137 | 30.3% | 1.00 | 3/8 |
| `multi_timeframe_soft_sizing` | 33 | +7.48% | 2.38% | 1.86 | 0.145 | 30.3% | 0.64 | 3/8 |
| `strategy_routing` | 21 | +9.23% | 2.29% | 2.17 | 0.193 | 28.6% | 0.88 | 3/8 |

### How to read

All numbers above are stitched out-of-sample across walk-forward folds.
No variant saw the data it was evaluated on at fit time. The bad-regime
avoidance variant fits its 'bad state' set on the TRAIN slice of each
fold only.

`folds+` is the count of folds with positive OOS return; a useful
consistency check independent of the aggregate.