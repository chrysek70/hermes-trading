# Volume confirmation filter — research report (Issue #35)

_Generated 20260531_084837._

Issue #35 — volume confirmation at the SuperTrend flip bar. Rule: ``volume_at_signal_bar >= volume.rolling(20).mean()`` (locked, no tuning). Same rule applied to long and short entries. The filter applies at the runner level — `signals.long_entry` / `signals.short_entry` and the live worker remain untouched.

## Methodology

The runner reuses the locked Issue #20 / #21 funding gate and the Issue #27 / #33 vol_sizing overlay verbatim. The only added behaviour is the per-asset rolling-20-bar volume mean comparison at the signal bar. Both variants share the same walk-forward geometry; only the entry-filter callback differs.

Volume mean is causal (computed from the 20-bar history ending at the signal bar's close). Warmup bars (first 20 of the universe) fail open — the volume filter does not block during indicator warmup.

## Filter rule

```
vol_mean_20 = volume.rolling(20).mean()
allow_entry = volume[signal_bar] >= vol_mean_20[signal_bar]
```

Same rule for long and short. Direction does not change the rule, only whether `signals.long_entry` or `signals.short_entry` would have fired.

## Counter-factual diagnostics

- longs filtered out: 11
- shorts filtered out: 8
- detailed trades CSV: `results/trades_volume_confirmation_20260531_084837.csv`



Issue #35 — volume confirmation at the SuperTrend flip bar. Rule: ``volume_at_signal_bar >= volume.rolling(20).mean()`` (locked, no tuning). Same rule applied to long and short entries. The filter applies at the runner level — `signals.long_entry` / `signals.short_entry` and the live worker remain untouched.

- universe: BTC/USDT + ETH/USDT (parallel; max 2 concurrent positions)
- strategy: SuperTrend(10,3) long-short — `state/strategy_supertrend_long_short.yaml`
- funding hard gate: long-block at p>=95, short-block at p<=5 (Issue #20 / #21)
- vol_sizing: 24-bar rv, trailing 12mo train window, Q1=1.00 / Q2_Q3=0.50 / Q4=0.25 (Issue #27 / #33)
- 48mo span: 2022-05-01 -> 2026-04-30 (8766 bars)
- walk-forward: train=1440 / test=360 / embargo=6
- costs: fee=0.001/side, slippage=0.0005 (Issue #29 fill model)

## Full 48-month walk-forward OOS

| variant | trades | total return | max DD | PF | win% | ret/exp | stop% | mean mult |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_vol` | 123 | +73.34% | 2.02% | 4.53 | 58.5% | +133.14% | 94.3% | 0.551 |
| `baseline_funding_vol_plus_volume_conf` | 104 | +74.67% | 1.34% | 5.79 | 62.5% | +135.65% | 94.2% | 0.550 |

### Head-to-head (baseline → +filter)

| window | Δ trades | Δ return | Δ max DD | Δ PF | Δ win% | Δ ret/exp | Δ stop% |
|---|---:|---:|---:|---:|---:|---:|---:|
| 48mo | -19 | +1.33pp | -0.68pp | +1.26 | +3.96pp | +2.50pp | -0.08pp |
| 3mo | -5 | -0.15pp | -0.79pp | +2.77 | +10.00pp | -0.37pp | -10.00pp |
| 6mo | -8 | +0.00pp | -0.79pp | +6.04 | +14.78pp | +0.18pp | -2.32pp |
| 12mo | -8 | +0.01pp | -0.56pp | +2.70 | +7.55pp | -0.32pp | -1.49pp |
| 24mo | -13 | +2.78pp | -0.55pp | +1.84 | +6.69pp | +6.39pp | -0.09pp |

## Recent-window subsets (sliced by exit timestamp)

### Last 3 months

| variant | trades | total return | max DD | PF | win% | ret/exp | stop% | mean mult |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_vol` | 10 | +3.57% | 1.52% | 2.87 | 50.0% | +8.94% | 90.0% | 0.400 |
| `baseline_funding_vol_plus_volume_conf` | 5 | +3.43% | 0.74% | 5.64 | 60.0% | +8.57% | 80.0% | 0.400 |

### Last 6 months

| variant | trades | total return | max DD | PF | win% | ret/exp | stop% | mean mult |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_vol` | 23 | +12.30% | 1.52% | 5.84 | 65.2% | +22.19% | 95.7% | 0.554 |
| `baseline_funding_vol_plus_volume_conf` | 15 | +12.31% | 0.74% | 11.89 | 80.0% | +22.38% | 93.3% | 0.550 |

### Last 12 months

| variant | trades | total return | max DD | PF | win% | ret/exp | stop% | mean mult |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_vol` | 37 | +22.05% | 1.52% | 6.15 | 64.9% | +37.95% | 94.6% | 0.581 |
| `baseline_funding_vol_plus_volume_conf` | 29 | +22.06% | 0.96% | 8.85 | 72.4% | +37.63% | 93.1% | 0.586 |

### Last 24 months

| variant | trades | total return | max DD | PF | win% | ret/exp | stop% | mean mult |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_vol` | 74 | +37.28% | 1.52% | 4.13 | 62.2% | +73.07% | 91.9% | 0.510 |
| `baseline_funding_vol_plus_volume_conf` | 61 | +40.06% | 0.98% | 5.97 | 68.9% | +79.46% | 91.8% | 0.504 |

## Adoption verdict

**Question**: Does volume confirmation reduce chop losses without killing trade count or long-term return?

**YES** — volume confirmation clears the PF and trade-count gates and preserves return.
