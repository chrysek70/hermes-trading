# Top-5 parallel SuperTrend portfolio — 20260530_132641

- supertrend strategy: `/Users/krzys/hermes-trading/state/strategy_supertrend.yaml`
- HMM config: `/Users/krzys/hermes-trading/state/hmm_regime.yaml`
- requested universe: ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT']
- universe used: ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT']  (n=5)
- universe NOT loaded: none
- aligned bars: 8766  span: 2022-05-01 -> 2026-04-30
- per-asset equal size: 1/5 = 0.2000
- decision TF: 4h
- walk-forward: train=1440 / test=360 / embargo=6
- costs: fee=0.001/side, slippage=0.0005

Adoption: trades >= 60 AND PF > 2.24 AND DD <= 9.63% AND return > 38.66% AND max single-asset profit share <= 60%.

| variant | assets | folds | n | OOS return | max DD | PF | Sharpe | win% | folds+ | concurrent | exposure | max share |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `top5_supertrend_parallel` | 5 | 20 | 155 | +40.70% | 2.49% | 2.19 | 0.256 | 51.6% | 12/20 | 4 | 35.7% | 32.6% |
| `top5_supertrend_hmm_filter_parallel` | 5 | 20 | 95 | +26.74% | 1.86% | 2.49 | 0.288 | 51.6% | 14/20 | 4 | 24.9% | 33.9% |
| `top5_supertrend_hmm_sizing_parallel` | 5 | 20 | 95 | +25.17% | 1.86% | 2.41 | 0.281 | 51.6% | 14/20 | 4 | 24.9% | 35.7% |
| `btc_eth_reference_parallel` | 2 | 20 | 65 | +39.72% | 5.54% | 2.50 | 0.296 | 53.8% | 11/20 | 2 | 18.1% | 51.0% |

### Per-asset trade count

- **top5_supertrend_parallel**: BNBUSDT:42; ETHUSDT:30; XRPUSDT:20; BTCUSDT:35; SOLUSDT:28
- **top5_supertrend_hmm_filter_parallel**: BNBUSDT:26; ETHUSDT:17; XRPUSDT:11; BTCUSDT:24; SOLUSDT:17
- **top5_supertrend_hmm_sizing_parallel**: BNBUSDT:26; ETHUSDT:17; XRPUSDT:11; BTCUSDT:24; SOLUSDT:17
- **btc_eth_reference_parallel**: ETHUSDT:30; BTCUSDT:35

### Per-asset return contribution (gross of overlay sizing, before equity compounding)

- **top5_supertrend_parallel**: BNBUSDT:+10.56%; ETHUSDT:+6.78%; XRPUSDT:-1.43%; BTCUSDT:+7.05%; SOLUSDT:+11.81%
- **top5_supertrend_hmm_filter_parallel**: BNBUSDT:+6.26%; ETHUSDT:+5.17%; XRPUSDT:-1.19%; BTCUSDT:+8.56%; SOLUSDT:+5.29%
- **top5_supertrend_hmm_sizing_parallel**: BNBUSDT:+4.99%; ETHUSDT:+5.17%; XRPUSDT:-1.19%; BTCUSDT:+8.56%; SOLUSDT:+5.29%
- **btc_eth_reference_parallel**: ETHUSDT:+16.94%; BTCUSDT:+17.61%

### Profit share by asset (winners only)

- **top5_supertrend_parallel**: BNBUSDT:29.2%; ETHUSDT:18.7%; XRPUSDT:0.0%; BTCUSDT:19.5%; SOLUSDT:32.6%
- **top5_supertrend_hmm_filter_parallel**: BNBUSDT:24.8%; ETHUSDT:20.4%; XRPUSDT:0.0%; BTCUSDT:33.9%; SOLUSDT:20.9%
- **top5_supertrend_hmm_sizing_parallel**: BNBUSDT:20.8%; ETHUSDT:21.5%; XRPUSDT:0.0%; BTCUSDT:35.7%; SOLUSDT:22.0%
- **btc_eth_reference_parallel**: ETHUSDT:49.0%; BTCUSDT:51.0%