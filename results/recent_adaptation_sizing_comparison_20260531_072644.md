# Recent-adaptation sizing comparison — 20260531_072644

- source trades: `trades_adaptive_sizing_20260531_005427.csv` (Issue #27 walk-forward, 48mo, 20 folds)
- variants: ['baseline_funding_only', 'hmm_sizing', 'vol_sizing', 'hmm_plus_vol_sizing']
- windows: anchored at last exit (2026-03-31); cutoff = anchor − N days
- methodology: walk-forward preserved — each trade was sized using train-window quartiles known at its entry. Subsetting trades by exit date is forward-causal.

## Window: `3mo` (since 2025-12-29)

| variant | trades | return | DD | PF | win% | mean mult | ret/exp | stop% | maxCL |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_only` | 15 | +8.53% | 4.40% | 2.34 | 53.3% | 1.000 | +8.53% | 93% | 4 |
| `hmm_sizing` | 15 | +1.96% | 2.10% | 1.63 | 53.3% | 0.500 | +3.92% | 93% | 4 |
| `vol_sizing` | 15 | +5.18% | 1.44% | 3.62 | 53.3% | 0.500 | +10.36% | 93% | 4 |
| `hmm_plus_vol_sizing` | 15 | +3.17% | 1.44% | 2.61 | 53.3% | 0.417 | +7.61% | 93% | 4 |

## Window: `6mo` (since 2025-09-29)

| variant | trades | return | DD | PF | win% | mean mult | ret/exp | stop% | maxCL |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_only` | 24 | +19.54% | 4.40% | 3.27 | 62.5% | 1.000 | +19.54% | 96% | 4 |
| `hmm_sizing` | 24 | +7.21% | 2.10% | 2.46 | 62.5% | 0.594 | +12.15% | 96% | 4 |
| `vol_sizing` | 24 | +10.42% | 1.44% | 5.10 | 62.5% | 0.469 | +22.23% | 96% | 4 |
| `hmm_plus_vol_sizing` | 24 | +8.31% | 1.44% | 4.29 | 62.5% | 0.417 | +19.95% | 96% | 4 |

## Window: `12mo` (since 2025-03-31)

| variant | trades | return | DD | PF | win% | mean mult | ret/exp | stop% | maxCL |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_only` | 41 | +49.65% | 4.40% | 4.67 | 68.3% | 1.000 | +49.65% | 95% | 4 |
| `hmm_sizing` | 41 | +26.82% | 2.10% | 4.69 | 68.3% | 0.671 | +39.98% | 95% | 4 |
| `vol_sizing` | 41 | +25.42% | 1.44% | 7.42 | 68.3% | 0.530 | +47.92% | 95% | 4 |
| `hmm_plus_vol_sizing` | 41 | +21.96% | 1.44% | 6.62 | 68.3% | 0.482 | +45.58% | 95% | 4 |

## Window: `24mo` (since 2024-03-31)

| variant | trades | return | DD | PF | win% | mean mult | ret/exp | stop% | maxCL |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_only` | 79 | +84.80% | 4.40% | 3.31 | 59.5% | 1.000 | +84.80% | 92% | 4 |
| `hmm_sizing` | 79 | +40.02% | 2.45% | 3.22 | 59.5% | 0.630 | +63.55% | 92% | 4 |
| `vol_sizing` | 79 | +36.45% | 1.88% | 3.88 | 59.5% | 0.491 | +74.30% | 92% | 4 |
| `hmm_plus_vol_sizing` | 79 | +28.08% | 1.44% | 3.62 | 59.5% | 0.424 | +66.22% | 92% | 4 |

## Window: `full_oos` (since 2023-01-01)

| variant | trades | return | DD | PF | win% | mean mult | ret/exp | stop% | maxCL |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_only` | 123 | +139.71% | 4.64% | 3.35 | 58.5% | 1.000 | +139.71% | 94% | 4 |
| `hmm_sizing` | 123 | +78.38% | 2.45% | 3.84 | 58.5% | 0.652 | +120.13% | 94% | 4 |
| `vol_sizing` | 123 | +72.71% | 2.10% | 4.63 | 58.5% | 0.533 | +136.54% | 94% | 4 |
| `hmm_plus_vol_sizing` | 123 | +59.69% | 1.57% | 4.49 | 58.5% | 0.472 | +126.59% | 94% | 4 |
