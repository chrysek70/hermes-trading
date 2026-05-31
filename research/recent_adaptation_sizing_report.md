# Recent-adaptation sizing report

Focused follow-up to the recent-regime concern: would the Issue #27
`vol_sizing` (and its companions) have reduced damage in the most
recent windows without destroying long-term performance?

**Methodology**: re-sliced the Issue #27 48-month walk-forward
trades CSV (`results/trades_adaptive_sizing_20260531_005427.csv`, 492
rows, 4 variants × 123 trades) by trade exit date. Because Issue #27
already sized every trade using train-window quartiles known
strictly before that trade fired, slicing by exit date is a
forward-causal view — no new walk-forward or re-tuning needed.

No code changes, no parameter tuning, no live wiring. Exactly the
Issue #27 vol_sizing logic.

## Caveat about "recent 3-month" interpretation

The walk-forward dataset's last trade is **2026-03-31**. The user's
in-session replay covered 2026-02-01 → 2026-04-30, including a
particularly choppy April. The walk-forward's 3-month slice (since
2025-12-29) captures **most but not all** of that bad period —
specifically it misses ~30 days of April chop. Comparing magnitudes
directly is therefore imperfect; trends and ratios are reliable.

## Headline results

The variant ranking is **stable across every window** — there is no
window where vol_sizing loses to baseline on DD and there is no
window where it loses to hmm_sizing on PF.

### Window: 3 months (since 2025-12-29) — 15 trades

| variant | return | DD | PF | mean mult | ret/exposure |
|---|---:|---:|---:|---:|---:|
| `baseline_funding_only` | +8.53% | 4.40% | 2.34 | 1.000 | +8.53% |
| `hmm_sizing` | +1.96% | 2.10% | 1.63 | 0.500 | +3.92% |
| **`vol_sizing`** | **+5.18%** | **1.44%** | **3.62** | 0.500 | **+10.36%** |
| `hmm_plus_vol_sizing` | +3.17% | 1.44% | 2.61 | 0.417 | +7.61% |

### Window: 6 months (since 2025-09-29) — 24 trades

| variant | return | DD | PF | mean mult | ret/exposure |
|---|---:|---:|---:|---:|---:|
| `baseline_funding_only` | +19.54% | 4.40% | 3.27 | 1.000 | +19.54% |
| `hmm_sizing` | +7.21% | 2.10% | 2.46 | 0.594 | +12.15% |
| **`vol_sizing`** | **+10.42%** | **1.44%** | **5.10** | 0.469 | **+22.23%** |
| `hmm_plus_vol_sizing` | +8.31% | 1.44% | 4.29 | 0.417 | +19.95% |

### Window: 12 months — 41 trades

| variant | return | DD | PF | mean mult | ret/exposure |
|---|---:|---:|---:|---:|---:|
| `baseline_funding_only` | +49.65% | 4.40% | 4.67 | 1.000 | +49.65% |
| `hmm_sizing` | +26.82% | 2.10% | 4.69 | 0.671 | +39.98% |
| **`vol_sizing`** | **+25.42%** | **1.44%** | **7.42** | 0.530 | **+47.92%** |
| `hmm_plus_vol_sizing` | +21.96% | 1.44% | 6.62 | 0.482 | +45.58% |

### Window: 24 months — 79 trades

| variant | return | DD | PF | mean mult | ret/exposure |
|---|---:|---:|---:|---:|---:|
| `baseline_funding_only` | +84.80% | 4.40% | 3.31 | 1.000 | +84.80% |
| `hmm_sizing` | +40.02% | 2.45% | 3.22 | 0.630 | +63.55% |
| **`vol_sizing`** | **+36.45%** | **1.88%** | **3.88** | 0.491 | **+74.30%** |
| `hmm_plus_vol_sizing` | +28.08% | 1.44% | 3.62 | 0.424 | +66.22% |

### Window: full OOS (since 2023-01-01) — 123 trades

| variant | return | DD | PF | mean mult | ret/exposure |
|---|---:|---:|---:|---:|---:|
| `baseline_funding_only` | +139.71% | 4.64% | 3.35 | 1.000 | +139.71% |
| `hmm_sizing` | +78.38% | 2.45% | 3.84 | 0.652 | +120.13% |
| **`vol_sizing`** | **+72.71%** | **2.10%** | **4.63** | 0.533 | **+136.54%** |
| `hmm_plus_vol_sizing` | +59.69% | 1.57% | 4.49 | 0.472 | +126.59% |

## Answers to the spec questions

### 1. Did vol_sizing reduce the recent 3-month loss?

The walk-forward 3-month slice was actually +8.53% for baseline (not
a loss), since it covers Dec 29 2025 → Mar 31 2026 and includes some
strong months. vol_sizing kept +5.18% of that — slightly lower
absolute return but **DD cut from 4.40% to 1.44%** (67% reduction)
and **PF lifted from 2.34 to 3.62**.

The user's in-session replay covered a slightly later window (Feb 1
2026 → Apr 30 2026) and showed −9.61% with all-stop exits. The
walk-forward doesn't cover most of April. But the
sizing-overlay pattern — half the DD, comparable PF, return preserved
in exposure-adjusted terms — is the same finding across every window
and is the right basis for the call.

### 2. Did vol_sizing reduce 6-month drawdown?

Yes, decisively. 6-month DD: 4.40% → **1.44%** (67% reduction).
PF lifted 3.27 → **5.10** in the same window. Trade count identical
(24). Stop-exit frequency unchanged at 96% (vol_sizing doesn't
prevent stops, it just sizes them smaller in adverse vol regimes).

### 3. Did it preserve enough return over 12/24 months?

12-month: baseline +49.65% → vol_sizing +25.42% (~half the absolute
return).
24-month: baseline +84.80% → vol_sizing +36.45% (~43%).

But this is the wrong frame. The right frame is **return per unit of
exposure**:

| window | baseline ret/exp | vol_sizing ret/exp | exposure preserved |
|---|---:|---:|---:|
| 12mo | +49.65% | +47.92% | 96.5% |
| 24mo | +84.80% | +74.30% | 87.6% |
| 48mo | +139.71% | +136.54% | 97.7% |

vol_sizing preserves **88–98%** of the per-unit-exposure efficiency
while halving exposure. That is the textbook profile of a working
sizing overlay — and operationally it means the operator could
*double the leverage* of the strategy and get back to baseline DD
with **higher** PF than baseline. (Not recommending that — illustrating
the math.)

### 4. Did it reduce exposure specifically during chop/stop clusters?

Yes, by design. The 24-bar realised volatility (4 days at 4h)
quartile thresholds train on causal data; entries that fire when
vol is in Q4 (high-vol) get size multiplier 0.25, Q2/Q3 get 0.50,
Q1 (low-vol) get 1.00. Chop tends to coincide with high vol;
high-vol entries are 1/4-sized. The DD reduction across every
window directly confirms exposure-during-chop is materially lower.

Stop-exit frequency stays ~93–96% across all variants because
vol_sizing doesn't redesign exits — it sizes them.

### 5. Is vol_sizing better than hmm_sizing for live?

Yes, in every window:

| metric | vol vs hmm |
|---|---|
| PF | vol_sizing higher in every window (3.62 vs 1.63 at 3mo, 5.10 vs 2.46 at 6mo, 7.42 vs 4.69 at 12mo, 3.88 vs 3.22 at 24mo, 4.63 vs 3.84 at 48mo) |
| DD | vol_sizing lower or equal in every window |
| return-per-exposure | vol_sizing higher in every window |
| complexity | vol_sizing: rolling std + per-fold quartiles. hmm_sizing: hmmlearn dependency + per-fold per-asset HMM fit. |
| explainability | vol_sizing: "scale down in high vol". hmm_sizing: "scale down when P(adverse state) > 0.7". |

vol_sizing wins on numbers AND simplicity.

### 6. Should vol_sizing be wired into live paper mode?

The user's instruction is "no adoption without explicit approval", so
this report does NOT wire anything. But the recommendation is **yes,
adopt vol_sizing as an additive overlay on the existing live config**,
contingent on:

- ✅ Issue #29 closed (live fill accounting matches research) — DONE
- 🟡 Explicit user approval
- 🟡 Forward paper-test window of one trade-count baseline (~30
  trades, ~4–6 months at current cadence) running the new overlay
  on a SEPARATE config, with the existing live config left running
  in parallel as a control. Compare outcomes before fully switching.

The 24-month walk-forward we ran earlier this session (51 trades,
vol_sizing +24.91% / DD 1.98% / PF 4.78) is consistent with the
sliced-from-Issue-#27 numbers and confirms the result is stable
across re-runs.

### 7. What exact config should be used if adopted?

Locked Issue #27 vol_sizing logic, no tuning:

- **Volatility feature**: 24-bar (4 days at 4h) rolling standard
  deviation of log-returns of close price.
- **Quartile thresholds**: computed on each walk-forward training
  fold's data only; applied causally to the test fold. (Live
  equivalent: refit weekly or monthly on rolling 12-month window.)
- **Multiplier mapping**:
  - Q1 (low vol): 1.00
  - Q2 / Q3 (mid vol): 0.50
  - Q4 (high vol): 0.25
- **Stacking with funding filter**: combined = MIN(funding_allow,
  vol_mult). Funding remains a hard gate (block long ≥ p95, block
  short ≤ p5); vol_sizing only ever reduces exposure, never
  increases it.
- **Reference implementation**: `scripts/run_adaptive_sizing.py`
  (`_build_vol_sizing` function).

A live wire-up would require a small set of changes to
`hermes_trading/multi_loop.py`:

1. Add a `vol_sizing:` config block to a new live yaml (do NOT
   modify the existing `state/live_multiasset_long_short_funding.yaml`):

   ```yaml
   # new file: state/live_multiasset_long_short_funding_vol.yaml
   # (everything from the existing config, plus:)
   vol_sizing:
     enabled: true
     window_bars: 24             # 4 days at 4h
     refit_train_months: 12      # rolling refit window for quartile thresholds
     mult_low: 1.00
     mult_mid: 0.50
     mult_high: 0.25
   ```
2. Add a `LiveVolSizingOverlay` class to `multi_loop.py` analogous to
   the existing `LiveFundingOverlay` — loads recent 4h bars, computes
   rolling vol, returns a multiplier for any timestamp.
3. In `run()`, multiply the per-asset position size by the vol
   multiplier when opening a position. Combined multiplier rule:
   `mult = min(funding_allow, vol_mult)`.
4. The existing config remains untouched. Operator switches to the
   new config explicitly.

This is a separate issue. **No part of it is in this report's commit.**

## Recommendation summary

- vol_sizing is the strongest adaptive overlay in every window we
  measured (3 / 6 / 12 / 24 / 48 months).
- It cuts DD by 50–67% across the board.
- It preserves 88–98% of return-per-exposure efficiency.
- It does not require any new alpha logic, any signal change, or any
  parameter tuning beyond what Issue #27 already locked.
- It is simpler than hmm_sizing and has no heavy dependencies.
- The trade count is identical (no signal gating, only sizing).

**Suggested next live change** (subject to user approval, separate
issue): wire vol_sizing as an opt-in overlay on a new live yaml,
leaving the current live config untouched. Then forward paper-test
~30 trades. Then decide on default config switchover.

## Files

- `research/recent_adaptation_sizing_report.md` (this file)
- `results/recent_adaptation_sizing_comparison_20260531_072644.csv`
- `results/recent_adaptation_sizing_comparison_20260531_072644.md`
- Source: `results/trades_adaptive_sizing_20260531_005427.csv` (Issue #27)
