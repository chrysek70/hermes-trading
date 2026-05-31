# Timeframe comparison — 20260531_073039

- strategy: `/Users/krzys/hermes-trading/state/strategy_supertrend_long_short.yaml` (long-short SuperTrend(10,3) + direction-aware funding gate)
- universe: BTC/USDT + ETH/USDT (parallel)
- n_months: 48
- costs: fee=0.001/side, slippage=0.0005

TF geometry:
| TF | train | test | embargo | bars/day | funding window (bars) |
|---|---:|---:|---:|---:|---:|
| 1h | 1440 | 360 | 6 | 24 | 720 |
| 2h | 1440 | 360 | 6 | 12 | 360 |
| 4h | 1440 | 360 | 6 | 6 | 180 |
| 1d | 240 | 60 | 1 | 1 | 30 |

## Walk-forward OOS by timeframe

| TF | n | ret | DD | PF | win | folds+ |
|---|---:|---:|---:|---:|---:|---:|
| 1h | 561 | +269.11% | 7.19% | 2.26 | 53.3% | 65/93 |
| 2h | 260 | +116.78% | 8.25% | 2.10 | 52.7% | 27/44 |
| 4h | 123 | +139.71% | 4.64% | 3.35 | 58.5% | 16/20 |
| 1d | 26 | +26.30% | 15.13% | 2.19 | 61.5% | 8/20 |

## Trailing-window slices (in-sample on WF trades)

| TF | scope | n | ret | DD | PF | win |
|---|---|---:|---:|---:|---:|---:|
| 1h | last_24mo | 311 | +109.27% | 6.18% | 2.37 | 55.3% |
| 1h | last_12mo | 159 | +78.19% | 3.95% | 3.61 | 59.7% |
| 1h | last_6mo | 80 | +31.74% | 3.95% | 3.05 | 58.8% |
| 1h | last_3mo | 40 | +9.03% | 3.95% | 1.99 | 45.0% |
| 2h | last_24mo | 147 | +119.64% | 5.61% | 3.12 | 56.5% |
| 2h | last_12mo | 76 | +58.29% | 5.61% | 3.42 | 53.9% |
| 2h | last_6mo | 35 | +18.52% | 5.61% | 2.51 | 54.3% |
| 2h | last_3mo | 16 | +18.91% | 1.73% | 7.09 | 62.5% |
| 4h | last_24mo | 79 | +84.80% | 4.40% | 3.31 | 59.5% |
| 4h | last_12mo | 41 | +49.65% | 4.40% | 4.67 | 68.3% |
| 4h | last_6mo | 24 | +19.54% | 4.40% | 3.27 | 62.5% |
| 4h | last_3mo | 13 | +8.56% | 4.40% | 2.36 | 53.8% |
| 1d | last_24mo | 18 | -3.17% | 15.13% | 0.88 | 44.4% |
| 1d | last_12mo | 8 | +13.46% | 2.71% | 5.23 | 75.0% |
| 1d | last_6mo | 5 | +11.37% | 2.71% | 5.13 | 80.0% |
| 1d | last_3mo | 3 | +9.44% | 2.71% | 4.48 | 66.7% |