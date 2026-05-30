# ETH vs BTC SuperTrend diagnostic comparison — 20260530_121820

- BTC: 48mo 4h (2022-05-01 -> 2026-04-30)
- ETH: 48mo 4h (aligned, 8766 bars)
- walk-forward: train=1440 / test=360 / embargo=6
- costs: fee=0.001/side, slippage=0.0005

## Q1 — trade diagnostics

| metric | BTC | ETH |
|---|---:|---:|
| trades | 35 | 30 |
| avg_winner | +0.0398 | +0.0271 |
| avg_loser | -0.0150 | -0.0160 |
| win_rate | 45.7% | 63.3% |
| profit_factor | 2.24 | 2.92 |
| expectancy | +0.0101 | +0.0113 |
| avg_holding_bars | 26.37 | 24.70 |
| median_holding_bars | 20.00 | 19.50 |
| n_stop | 32 | 27 |
| n_end | 3 | 3 |
| n_trail_exit | 0 | 0 |
| n_target_rsi | 0 | 0 |
| n_max_hold | 0 | 0 |
| stop_pct | 91.4% | 90.0% |
| trail_pct | 0.0% | 0.0% |
| max_hold_pct | 0.0% | 0.0% |

## Per-fold ETH-vs-BTC consistency

- folds compared: 20
- BTC better in: **8** folds
- ETH better in: **10** folds
- ties: 2
- BTC fold-return mean: +1.73% σ 4.10%
- ETH fold-return mean: +1.69% σ 3.98%

## Q2 — trend quality (full window, post-warmup)

| metric | BTC | ETH |
|---|---:|---:|
| n_flips | 205.00 | 209.00 |
| run_count | 206.00 | 210.00 |
| mean_run_bars | 42.55 | 41.74 |
| median_run_bars | 34.00 | 33.00 |
| short_runs_share | 6.8% | 5.7% |
| long_runs_share | 84.5% | 83.3% |
| trending_time_share | 97.4% | 97.1% |
| adx_mean | 28.1196 | 27.6657 |
| adx_median | 25.2085 | 24.6944 |
| adx_pct_above_25 | 50.7% | 48.9% |
| atr_pct_mean | 0.0147 | 0.0201 |
| atr_pct_median | 0.0135 | 0.0183 |
| atr_pct_std | 0.0063 | 0.0088 |

## Q3 — market structure (full window)

| metric | BTC | ETH |
|---|---:|---:|
| ret_autocorr_lag1 | -0.0006 | 0.0031 |
| ret_autocorr_lag5 | -0.0125 | -0.0200 |
| ret_autocorr_lag24 | 0.0028 | -0.0140 |
| absret_autocorr_lag1 | 0.2076 | 0.2206 |
| absret_autocorr_lag5 | 0.1615 | 0.1587 |
| absret_autocorr_lag24 | 0.0915 | 0.1169 |
| ret_skew | 0.0252 | -0.1055 |
| ret_kurtosis | 7.4013 | 8.4550 |
| dd_count_5pct | 71.0000 | 30.0000 |
| dd_maxdepth_5pct | -0.6055 | -0.6904 |
| dd_median_duration_bars_5pct | 4.0000 | 9.0000 |
| dd_count_10pct | 67.0000 | 40.0000 |
| dd_maxdepth_10pct | -0.6055 | -0.6904 |
| dd_median_duration_bars_10pct | 4.0000 | 3.0000 |

## Q4 — rotation selectors (walk-forward OOS)

| variant | folds | n | OOS return | max DD | PF | Sharpe | win% | folds+ | by asset |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| BTC solo (reference) | 20 | 35 | (see Q1) | — | 2.24 | — | 45.7% | — | BTC:35 |
| ETH solo (reference) | 20 | 30 | (see Q1) | — | 2.92 | — | 63.3% | — | ETH:30 |
| `rotation_supertrend_distance` | 20 | 47 | +48.54% | 10.09% | 2.12 | 0.237 | 46.8% | 10/20 | ETH:20; BTC:27 |
| `rotation_rs_score` | 20 | 47 | +45.64% | 10.09% | 2.01 | 0.224 | 46.8% | 11/20 | ETH:18; BTC:29 |