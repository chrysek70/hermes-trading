# MTF confirmation variants — 20260531_073251

- universe: BTC/USDT + ETH/USDT (parallel, 4h decision)
- strategy: `/Users/krzys/hermes-trading/state/strategy_supertrend_long_short.yaml`
- n_months: 48
- walk-forward: train=1440 / test=360 / embargo=6
- costs: fee=0.001/side, slippage=0.0005

Variants:
- baseline = current adopted candidate (no MTF confirmation)
- A: 4h entry only if 1d SuperTrend direction agrees with the trade
- B: 4h entry only if 1h SuperTrend direction agrees with the trade
- C: size scaled by agreement (3 agree -> 1.0, 2 -> 0.5, 1 -> 0.25)
- D: 4h entry, 1h early-warning exit (close on 1h flip vs position)

## 48mo OOS

| variant | n | ret | DD | PF | win | folds+ |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 123 | +139.71% | 4.64% | 3.35 | 58.5% | 16/20 |
| A_1d_agree | 104 | +101.51% | 4.82% | 3.22 | 59.6% | 15/20 |
| B_1h_agree | 106 | +94.56% | 4.40% | 2.99 | 56.6% | 15/20 |
| C_size_scale | 123 | +99.22% | 4.14% | 3.11 | 58.5% | 16/20 |
| D_1h_early_exit | 124 | +25.75% | 6.28% | 1.54 | 41.1% | 13/20 |

## Trailing-window slices (in-sample on WF trades)

| variant | scope | n | ret | DD | PF | win |
|---|---|---:|---:|---:|---:|---:|
| baseline | last_24mo | 79 | +84.80% | 4.40% | 3.31 | 59.5% |
| baseline | last_12mo | 41 | +49.65% | 4.40% | 4.67 | 68.3% |
| baseline | last_6mo | 24 | +19.54% | 4.40% | 3.27 | 62.5% |
| baseline | last_3mo | 13 | +8.56% | 4.40% | 2.36 | 53.8% |
| A_1d_agree | last_24mo | 68 | +66.39% | 3.40% | 3.26 | 61.8% |
| A_1d_agree | last_12mo | 38 | +47.19% | 2.97% | 5.05 | 71.1% |
| A_1d_agree | last_6mo | 22 | +21.33% | 2.97% | 4.01 | 68.2% |
| A_1d_agree | last_3mo | 13 | +10.16% | 2.97% | 3.06 | 61.5% |
| B_1h_agree | last_24mo | 69 | +59.45% | 4.40% | 3.01 | 58.0% |
| B_1h_agree | last_12mo | 34 | +32.25% | 4.40% | 4.16 | 67.6% |
| B_1h_agree | last_6mo | 21 | +13.67% | 4.40% | 2.79 | 61.9% |
| B_1h_agree | last_3mo | 12 | +9.30% | 4.40% | 2.64 | 58.3% |
| C_size_scale | last_24mo | 79 | +64.57% | 3.69% | 3.17 | 59.5% |
| C_size_scale | last_12mo | 41 | +39.65% | 3.69% | 4.62 | 68.3% |
| C_size_scale | last_6mo | 24 | +17.47% | 3.69% | 3.36 | 62.5% |
| C_size_scale | last_3mo | 13 | +9.75% | 3.69% | 2.84 | 53.8% |
| D_1h_early_exit | last_24mo | 77 | +12.66% | 4.63% | 1.44 | 42.9% |
| D_1h_early_exit | last_12mo | 41 | +12.74% | 4.63% | 2.14 | 48.8% |
| D_1h_early_exit | last_6mo | 24 | +6.86% | 4.63% | 1.92 | 41.7% |
| D_1h_early_exit | last_3mo | 13 | +4.50% | 4.63% | 1.91 | 38.5% |