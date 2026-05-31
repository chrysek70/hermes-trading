# Adaptive risk-layer rules — 20260531_073505

- universe: BTC/USDT + ETH/USDT (parallel, 4h decision)
- strategy: `/Users/krzys/hermes-trading/state/strategy_supertrend_long_short.yaml`
- walk-forward: train=1440 / test=360 / embargo=6
- costs: fee=0.001/side, slippage=0.0005

Rules tested:
- R1: pause (size=0) when rolling 10-trade PF < 1.0
- R2: half-size after 3 consecutive losses
- R3: half-size when rolling 30-day return < -3%
- R4: half-size when stop-exit frequency > 80% over last 5 trades
- R5: half-size when HMM adverse probability > 0.7 at entry
- R6: volatility-quartile sizing (low=1.0, mid=0.5, high=0.25)

## 48mo OOS

| variant | n | ret | DD | PF | win | folds+ | triggers | recent_modified |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 123 | +139.71% | 4.64% | 3.35 | 58.5% | 16/20 | 0 | 0 |
| R1_pf_pause | 47 | +29.96% | 1.84% | 3.32 | 55.3% | 7/20 | 4388 | 0 |
| R2_consec_loss | 123 | +135.70% | 4.64% | 3.33 | 58.5% | 16/20 | 265 | 1 |
| R3_30d_return | 123 | +139.71% | 4.64% | 3.35 | 58.5% | 16/20 | 84 | 0 |
| R4_stop_freq | 123 | +73.52% | 4.09% | 3.10 | 58.5% | 16/20 | 4867 | 7 |
| R5_hmm_adverse | 123 | +97.15% | 3.14% | 3.60 | 58.5% | 16/20 | 57 | 9 |
| R6_vol_quartile | 123 | +72.71% | 2.10% | 4.63 | 58.5% | 16/20 | 42 | 11 |

## Trailing windows

| variant | scope | n | ret | DD | PF | win |
|---|---|---:|---:|---:|---:|---:|
| baseline | last_24mo | 79 | +84.80% | 4.40% | 3.31 | 59.5% |
| baseline | last_12mo | 41 | +49.65% | 4.40% | 4.67 | 68.3% |
| baseline | last_6mo | 24 | +19.54% | 4.40% | 3.27 | 62.5% |
| baseline | last_3mo | 13 | +8.56% | 4.40% | 2.36 | 53.8% |
| R1_pf_pause | last_24mo | 47 | +29.96% | 1.84% | 3.32 | 55.3% |
| R1_pf_pause | last_12mo | 39 | +25.23% | 1.84% | 3.30 | 56.4% |
| R1_pf_pause | last_6mo | 17 | +9.47% | 1.84% | 2.33 | 47.1% |
| R1_pf_pause | last_3mo | 7 | -1.26% | 1.82% | 0.63 | 28.6% |
| R2_consec_loss | last_24mo | 79 | +81.71% | 4.09% | 3.28 | 59.5% |
| R2_consec_loss | last_12mo | 41 | +50.19% | 4.06% | 4.82 | 68.3% |
| R2_consec_loss | last_6mo | 24 | +19.96% | 4.06% | 3.42 | 62.5% |
| R2_consec_loss | last_3mo | 13 | +8.95% | 4.06% | 2.50 | 53.8% |
| R3_30d_return | last_24mo | 79 | +84.80% | 4.40% | 3.31 | 59.5% |
| R3_30d_return | last_12mo | 41 | +49.65% | 4.40% | 4.67 | 68.3% |
| R3_30d_return | last_6mo | 24 | +19.54% | 4.40% | 3.27 | 62.5% |
| R3_30d_return | last_3mo | 13 | +8.56% | 4.40% | 2.36 | 53.8% |
| R4_stop_freq | last_24mo | 79 | +44.88% | 4.09% | 2.90 | 59.5% |
| R4_stop_freq | last_12mo | 41 | +23.68% | 3.42% | 3.57 | 68.3% |
| R4_stop_freq | last_6mo | 24 | +10.24% | 3.42% | 2.51 | 62.5% |
| R4_stop_freq | last_3mo | 13 | +3.78% | 3.42% | 1.77 | 53.8% |
| R5_hmm_adverse | last_24mo | 79 | +53.80% | 3.00% | 3.26 | 59.5% |
| R5_hmm_adverse | last_12mo | 41 | +34.12% | 2.87% | 4.68 | 68.3% |
| R5_hmm_adverse | last_6mo | 24 | +11.23% | 2.87% | 2.83 | 62.5% |
| R5_hmm_adverse | last_3mo | 13 | +4.18% | 2.87% | 2.01 | 53.8% |
| R6_vol_quartile | last_24mo | 79 | +36.45% | 1.88% | 3.88 | 59.5% |
| R6_vol_quartile | last_12mo | 41 | +25.42% | 1.44% | 7.42 | 68.3% |
| R6_vol_quartile | last_6mo | 24 | +10.42% | 1.44% | 5.10 | 62.5% |
| R6_vol_quartile | last_3mo | 13 | +5.21% | 1.44% | 3.70 | 53.8% |