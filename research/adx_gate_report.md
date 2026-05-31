# ADX trend-strength gate — research report (Issue #36)

_Generated 20260531_084841._

Issue #36 — ADX(14) trend-strength gate at the SuperTrend flip bar. Rule: ``ADX_14 >= 20`` (Wilder's smoothed construction; locked, no tuning). Same threshold applied to long and short entries. ADX is computed inline in the runner so that `signals.py` stays unchanged.

## Methodology

ADX(14) is implemented inline in the runner using Wilder's smoothed True Range + directional movement construction (equivalent to TA-Lib's ADXR=14 family). The series is causal by construction; warmup bars fail open. The same threshold (``>= 20``) is applied to both long and short entries because the spec specifies one threshold and the test should not introduce a direction-asymmetry.

## Filter rule

```
+DM = high[t] - high[t-1]      if positive and > -DM
-DM = low[t-1] - low[t]        if positive and > +DM
TR  = max(H-L, |H-Cprev|, |L-Cprev|)
smooth = Wilder EWMA with alpha = 1/14
DI+ = 100 * smooth(+DM) / smooth(TR)
DI- = 100 * smooth(-DM) / smooth(TR)
DX  = 100 * |DI+ - DI-| / (DI+ + DI-)
ADX = smooth(DX)
allow_entry = ADX >= 20
```

## Counter-factual diagnostics

- longs filtered out: 31
- shorts filtered out: 29
- detailed trades CSV: `results/trades_adx_gate_20260531_084841.csv`



Issue #36 — ADX(14) trend-strength gate at the SuperTrend flip bar. Rule: ``ADX_14 >= 20`` (Wilder's smoothed construction; locked, no tuning). Same threshold applied to long and short entries. ADX is computed inline in the runner so that `signals.py` stays unchanged.

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
| `baseline_funding_vol_plus_adx` | 63 | +35.32% | 1.21% | 4.62 | 57.1% | +70.08% | 92.1% | 0.504 |

### Head-to-head (baseline → +filter)

| window | Δ trades | Δ return | Δ max DD | Δ PF | Δ win% | Δ ret/exp | Δ stop% |
|---|---:|---:|---:|---:|---:|---:|---:|
| 48mo | -60 | -38.02pp | -0.81pp | +0.09 | -1.39pp | -63.06pp | -2.25pp |
| 3mo | -2 | -0.44pp | -0.32pp | +0.10 | +0.00pp | -1.22pp | -2.50pp |
| 6mo | -11 | -4.48pp | -0.32pp | -0.06 | +1.45pp | -6.55pp | -3.99pp |
| 12mo | -19 | -9.77pp | -0.32pp | -0.56 | +1.80pp | -14.06pp | -5.71pp |
| 24mo | -35 | -16.99pp | -0.32pp | +0.21 | +1.94pp | -30.30pp | -2.15pp |

## Recent-window subsets (sliced by exit timestamp)

### Last 3 months

| variant | trades | total return | max DD | PF | win% | ret/exp | stop% | mean mult |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_vol` | 10 | +3.57% | 1.52% | 2.87 | 50.0% | +8.94% | 90.0% | 0.400 |
| `baseline_funding_vol_plus_adx` | 8 | +3.14% | 1.21% | 2.97 | 50.0% | +7.72% | 87.5% | 0.406 |

### Last 6 months

| variant | trades | total return | max DD | PF | win% | ret/exp | stop% | mean mult |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_vol` | 23 | +12.30% | 1.52% | 5.84 | 65.2% | +22.19% | 95.7% | 0.554 |
| `baseline_funding_vol_plus_adx` | 12 | +7.82% | 1.21% | 5.78 | 66.7% | +15.65% | 91.7% | 0.500 |

### Last 12 months

| variant | trades | total return | max DD | PF | win% | ret/exp | stop% | mean mult |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_vol` | 37 | +22.05% | 1.52% | 6.15 | 64.9% | +37.95% | 94.6% | 0.581 |
| `baseline_funding_vol_plus_adx` | 18 | +12.28% | 1.21% | 5.60 | 66.7% | +23.89% | 88.9% | 0.514 |

### Last 24 months

| variant | trades | total return | max DD | PF | win% | ret/exp | stop% | mean mult |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_vol` | 74 | +37.28% | 1.52% | 4.13 | 62.2% | +73.07% | 91.9% | 0.510 |
| `baseline_funding_vol_plus_adx` | 39 | +20.29% | 1.21% | 4.34 | 64.1% | +42.77% | 89.7% | 0.474 |

## Adoption verdict

**Question**: Does ADX reduce false SuperTrend flips in chop while preserving the core edge?

**NO** — PF holds but the ADX gate prunes too many trades; the core edge is not preserved.
