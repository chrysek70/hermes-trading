# Adaptive sizing comparison (Issue #27) — 20260531_005427

- long-short strategy: `/Users/krzys/hermes-trading/state/strategy_supertrend_long_short.yaml`
- HMM config: `/Users/krzys/hermes-trading/state/hmm_regime.yaml` (per-asset per-fold fit; train-only mapping; this script uses its own Issue #27 sizing mapping)
- vol window: 24 bars (4 days at 4h)
- funding hard gate: long-block at p>=95, short-block at p<=5 (Issue #20 adopted) — applied to ALL variants
- universe: BTC/USDT + ETH/USDT (parallel)
- 48mo span: 2022-05-01 -> 2026-04-30 (8766 bars)
- walk-forward: train=1440 / test=360 / embargo=6
- costs: fee=0.001/side, slippage=0.0005

**Sizing concept** (locked, Issue #27): favourable=1.00, neutral=0.50, adverse/high-vol=0.25.

**Adoption criterion** (Issue #27): reduce DD or improve PF without materially reducing trade count.

| variant | folds | n | L | S | OOS return | max DD | PF | win% | mean mult | ret/exp | DD/exp | trade Δ vs base | folds+ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_only` | 20 | 123 | 63 | 60 | +139.71% | 4.64% | 3.35 | 58.5% | 1.000 | +139.71% | 4.64% | +0.0% | 16/20 |
| `hmm_sizing` | 20 | 123 | 63 | 60 | +78.38% | 2.45% | 3.84 | 58.5% | 0.652 | +120.13% | 3.75% | +0.0% | 16/20 |
| `vol_sizing` | 20 | 123 | 63 | 60 | +72.71% | 2.10% | 4.63 | 58.5% | 0.533 | +136.54% | 3.94% | +0.0% | 16/20 |
| `hmm_plus_vol_sizing` | 20 | 123 | 63 | 60 | +59.69% | 1.57% | 4.49 | 58.5% | 0.472 | +126.59% | 3.34% | +0.0% | 16/20 |

## Performance by HMM regime label at entry

| variant | regime | trades | win% | total return |
|---|---|---:|---:|---:|
| `baseline_funding_only` | n/a | 123 | 58.5% | +89.83% |
| `hmm_sizing` | warmup | 8 | 37.5% | -1.39% |
| `hmm_sizing` | favourable | 58 | 63.8% | +50.22% |
| `hmm_sizing` | adverse | 57 | 56.1% | +10.25% |
| `vol_sizing` | n/a | 123 | 58.5% | +55.41% |
| `hmm_plus_vol_sizing` | warmup | 8 | 37.5% | -0.73% |
| `hmm_plus_vol_sizing` | favourable | 58 | 63.8% | +37.91% |
| `hmm_plus_vol_sizing` | adverse | 57 | 56.1% | +10.25% |

## Performance by volatility band at entry

| variant | band | trades | win% | total return |
|---|---|---:|---:|---:|
| `baseline_funding_only` | n/a | 123 | 58.5% | +89.83% |
| `hmm_sizing` | n/a | 123 | 58.5% | +59.08% |
| `vol_sizing` | favourable | 29 | 72.4% | +30.28% |
| `vol_sizing` | neutral | 52 | 57.7% | +20.47% |
| `vol_sizing` | adverse | 42 | 50.0% | +4.65% |
| `hmm_plus_vol_sizing` | favourable | 29 | 72.4% | +27.94% |
| `hmm_plus_vol_sizing` | neutral | 52 | 57.7% | +14.84% |
| `hmm_plus_vol_sizing` | adverse | 42 | 50.0% | +4.65% |