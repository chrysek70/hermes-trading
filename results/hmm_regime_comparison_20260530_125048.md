# HMM regime overlay — 20260530_125048

- supertrend strategy: `/Users/krzys/hermes-trading/state/strategy_supertrend.yaml`
- HMM config: `/Users/krzys/hermes-trading/state/hmm_regime.yaml` (or defaults if missing)
- BTC: 48mo (2022-05-01 -> 2026-04-30)
- ETH: 48mo (aligned, 8766 bars)
- decision TF: 4h
- walk-forward: train=1440 / test=360 / embargo=6
- costs: fee=0.001/side, slippage=0.0005

Adoption criteria — BTC: PF > 2.24 AND trades >= 30 AND DD <= 9.63%. ETH: PF > 2.92 AND trades >= 30 AND DD <= 5.30%.

| variant | folds | n | OOS return | max DD | PF | Sharpe | win% | folds+ | by state |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `supertrend_only_btc` | 20 | 35 | +38.66% | 9.63% | 2.24 | 0.266 | 45.7% | 10/20 | no_hmm:35 |
| `supertrend_hmm_filter_btc` | 20 | 24 | +49.98% | 3.79% | 4.01 | 0.434 | 54.2% | 9/20 | no_hmm:24 |
| `supertrend_hmm_sizing_btc` | 20 | 24 | +49.98% | 3.79% | 4.01 | 0.434 | 54.2% | 9/20 | no_hmm:24 |
| `supertrend_only_eth` | 20 | 30 | +37.86% | 5.30% | 2.92 | 0.336 | 63.3% | 10/20 | no_hmm:30 |
| `supertrend_hmm_filter_eth` | 20 | 17 | +27.80% | 4.13% | 4.27 | 0.402 | 70.6% | 8/20 | no_hmm:17 |
| `supertrend_hmm_sizing_eth` | 20 | 17 | +27.80% | 4.13% | 4.27 | 0.402 | 70.6% | 8/20 | no_hmm:17 |