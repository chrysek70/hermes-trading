# SuperTrend long-only vs long-short — 20260530_221646

- long-only strategy: `/Users/krzys/hermes-trading/state/strategy_supertrend.yaml`
- long-short strategy: `/Users/krzys/hermes-trading/state/strategy_supertrend_long_short.yaml`
- universe: BTC/USDT + ETH/USDT (parallel)
- BTC + ETH 48mo span: 2022-05-01 -> 2026-04-30 (8766 bars)
- walk-forward: train=1440 / test=360 / embargo=6
- costs: fee=0.001/side, slippage=0.0005

Adoption (long-short must beat the adopted BTC/ETH parallel long-only): PF > 2.50, DD <= 5.54%, return > 39.72%, trades >= 65.

| variant | folds | n | L | S | OOS return | L ret | S ret | max DD | PF | Sharpe | win% | folds+ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `btc_supertrend_long_only` | 20 | 35 | 35 | 0 | +38.66% | +35.23% | +0.00% | 9.63% | 2.24 | 0.266 | 45.7% | 10/20 |
| `btc_supertrend_long_short` | 20 | 65 | 35 | 30 | +107.57% | +35.23% | +41.73% | 9.98% | 2.87 | 0.353 | 52.3% | 15/20 |
| `eth_supertrend_long_only` | 20 | 30 | 30 | 0 | +37.86% | +33.88% | +0.00% | 5.30% | 2.92 | 0.336 | 63.3% | 10/20 |
| `eth_supertrend_long_short` | 20 | 64 | 30 | 34 | +163.94% | +33.88% | +68.63% | 5.30% | 3.67 | 0.406 | 62.5% | 15/20 |
| `btc_eth_parallel_long_only` | 20 | 65 | 65 | 0 | +39.72% | +34.56% | +0.00% | 5.54% | 2.50 | 0.296 | 53.8% | 11/20 |
| `btc_eth_parallel_long_short` | 20 | 129 | 65 | 64 | +139.47% | +34.56% | +55.18% | 5.76% | 3.26 | 0.379 | 57.4% | 16/20 |

### By exit reason

- **btc_supertrend_long_only**: stop:32; end:3
- **btc_supertrend_long_short**: stop:61; end:4
- **eth_supertrend_long_only**: stop:27; end:3
- **eth_supertrend_long_short**: stop:59; end:5
- **btc_eth_parallel_long_only**: stop:59; end:6
- **btc_eth_parallel_long_short**: stop:120; end:9