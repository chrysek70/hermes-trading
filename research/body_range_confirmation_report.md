# Body-to-range confirmation — research report (Issue #37)

_Generated 20260531_084844._

Issue #37 — candle body-to-range confirmation at the SuperTrend flip bar. Rule: ``body_to_range = abs(close-open) / max(high-low, eps); ratio >= 0.50`` AND direction-consistent body sign (long: close > open; short: close < open). Locked, no tuning. Filter applies at the runner level — `signals.py` and the live worker remain untouched.

## Methodology

The filter is a pure function of the signal bar's own OHLC. It is therefore trivially causal — no rolling window, no future leakage, no train-window dependence.

Two clauses are tested in conjunction: (1) body magnitude (``ratio >= 0.50``) and (2) body sign consistent with the entry direction. A long entry on a bar that closes below its open is rejected even if the ratio is large; a short entry on a bar that closes above its open is rejected the same way. Doji bars (range = 0) are mapped through ``max(range, 1e-12)`` to avoid division by zero — a true doji with body == 0 falls below the 0.50 threshold and is rejected, which is the spec's intended behaviour.

## Filter rule

```
body_to_range = abs(close - open) / max(high - low, 1e-12)
allow_entry = (body_to_range >= 0.50)
              AND (direction == 'long' implies close > open)
              AND (direction == 'short' implies close < open)
```

## Counter-factual diagnostics

- longs filtered out: 9
- shorts filtered out: 10
- detailed trades CSV: `results/trades_body_range_confirmation_20260531_084844.csv`



Issue #37 — candle body-to-range confirmation at the SuperTrend flip bar. Rule: ``body_to_range = abs(close-open) / max(high-low, eps); ratio >= 0.50`` AND direction-consistent body sign (long: close > open; short: close < open). Locked, no tuning. Filter applies at the runner level — `signals.py` and the live worker remain untouched.

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
| `baseline_funding_vol_plus_body_range` | 104 | +67.68% | 1.33% | 5.54 | 61.5% | +122.96% | 94.2% | 0.550 |

### Head-to-head (baseline → +filter)

| window | Δ trades | Δ return | Δ max DD | Δ PF | Δ win% | Δ ret/exp | Δ stop% |
|---|---:|---:|---:|---:|---:|---:|---:|
| 48mo | -19 | -5.65pp | -0.69pp | +1.01 | +3.00pp | -10.19pp | -0.08pp |
| 3mo | -1 | +0.20pp | -0.19pp | +0.32 | +5.56pp | +0.12pp | -1.11pp |
| 6mo | -2 | +0.35pp | -0.19pp | +0.86 | +6.21pp | -0.05pp | -0.41pp |
| 12mo | -5 | +1.35pp | -0.19pp | +2.89 | +7.01pp | +0.45pp | -0.84pp |
| 24mo | -8 | +1.82pp | -0.19pp | +0.94 | +4.50pp | +1.72pp | +0.53pp |

## Recent-window subsets (sliced by exit timestamp)

### Last 3 months

| variant | trades | total return | max DD | PF | win% | ret/exp | stop% | mean mult |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_vol` | 10 | +3.57% | 1.52% | 2.87 | 50.0% | +8.94% | 90.0% | 0.400 |
| `baseline_funding_vol_plus_body_range` | 9 | +3.78% | 1.33% | 3.19 | 55.6% | +9.06% | 88.9% | 0.417 |

### Last 6 months

| variant | trades | total return | max DD | PF | win% | ret/exp | stop% | mean mult |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_vol` | 23 | +12.30% | 1.52% | 5.84 | 65.2% | +22.19% | 95.7% | 0.554 |
| `baseline_funding_vol_plus_body_range` | 21 | +12.65% | 1.33% | 6.71 | 71.4% | +22.15% | 95.2% | 0.571 |

### Last 12 months

| variant | trades | total return | max DD | PF | win% | ret/exp | stop% | mean mult |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_vol` | 37 | +22.05% | 1.52% | 6.15 | 64.9% | +37.95% | 94.6% | 0.581 |
| `baseline_funding_vol_plus_body_range` | 32 | +23.40% | 1.33% | 9.05 | 71.9% | +38.40% | 93.8% | 0.609 |

### Last 24 months

| variant | trades | total return | max DD | PF | win% | ret/exp | stop% | mean mult |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_vol` | 74 | +37.28% | 1.52% | 4.13 | 62.2% | +73.07% | 91.9% | 0.510 |
| `baseline_funding_vol_plus_body_range` | 66 | +39.09% | 1.33% | 5.07 | 66.7% | +74.79% | 92.4% | 0.523 |

## Adoption verdict

**Question**: Does candle-quality confirmation reduce weak flip entries without over-filtering the strategy?

**YES** — body-to-range confirmation clears the PF and trade-count gates and preserves return.
