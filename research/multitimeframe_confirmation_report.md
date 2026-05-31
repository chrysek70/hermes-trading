# Multi-timeframe confirmation — Phase 4

Author: research agent (autonomous run, 2026-05-31)
Source: `scripts/run_multitimeframe_confirmation.py`
Window: 48 months (2022-05-01 → 2026-04-30) of BTC/USDT + ETH/USDT, 4h decision
Outputs: `results/multitimeframe_confirmation_20260531_073251.{csv,md}`
Per-trade CSV: `results/multitimeframe_confirmation_trades_20260531_073251.csv`

## Variants tested

Locked spec (no threshold tuning):

- **baseline** — current adopted candidate (4h SuperTrend + funding gate, no MTF)
- **A** — 4h entry only if the 1d SuperTrend direction agrees with the trade
- **B** — 4h entry only if the 1h SuperTrend direction agrees with the trade
- **C** — size scaled by agreement: 3 agree → 1.0; 2 of 3 → 0.5; only 4h → 0.25
- **D** — 4h entry, 1h early-warning exit (close when 1h flips against position)

Every variant inherits the SuperTrend(10, 3), the direction-aware funding
gate (block long ≥ p95, block short ≤ p5), and the standard costs.

## 48mo walk-forward OOS

| variant | n | ret | DD | PF | win | folds+ |
|---|---:|---:|---:|---:|---:|---:|
| baseline       | 123 | +139.71% | 4.64% | 3.35 | 58.5% | 16/20 |
| A_1d_agree     | 104 | +101.51% | 4.82% | 3.22 | 59.6% | 15/20 |
| B_1h_agree     | 106 |  +94.56% | 4.40% | 2.99 | 56.6% | 15/20 |
| C_size_scale   | 123 |  +99.22% | 4.14% | 3.11 | 58.5% | 16/20 |
| D_1h_early_exit| 124 |  +25.75% | 6.28% | 1.54 | 41.1% | 13/20 |

Verdict against the Issue #20 adoption gate (PF ≥ 3.26, DD ≤ 5.54%,
return ≥ +139.47%, trades ≥ 100):

- **A**: PF 3.22 fails (need ≥ 3.26), return +101.51% fails (need
  ≥ +139.47%). DD passes (4.82% ≤ 5.54%). Trade count passes.
  Verdict: **NOT adopted, return gate failed.**
- **B**: PF 2.99 fails, return +94.56% fails. Verdict: **NOT adopted.**
- **C**: PF 3.11 fails, return +99.22% fails (gives up 40 pp of
  return for a 0.50 pp DD improvement). Verdict: **NOT adopted.**
- **D**: PF 1.54 fails badly. The 1h early-warning exit triggers
  prematurely on noise and turns winning trades into losers.
  Verdict: **rejected.**

## Trailing-window slices

| variant | scope | n | ret | DD | PF | win |
|---|---|---:|---:|---:|---:|---:|
| baseline       | last_24mo | 79 | +84.80% | 4.40% | 3.31 | 59.5% |
| baseline       | last_12mo | 41 | +49.65% | 4.40% | 4.67 | 68.3% |
| baseline       | last_6mo  | 24 | +19.54% | 4.40% | 3.27 | 62.5% |
| baseline       | last_3mo  | 13 |  +8.56% | 4.40% | 2.36 | 53.8% |
| A_1d_agree     | last_24mo | 68 | +66.39% | 3.40% | 3.26 | 61.8% |
| A_1d_agree     | last_12mo | 38 | +47.19% | 2.97% | 5.05 | 71.1% |
| A_1d_agree     | last_6mo  | 22 | +21.33% | 2.97% | 4.01 | 68.2% |
| A_1d_agree     | last_3mo  | 13 | +10.16% | 2.97% | 3.06 | 61.5% |
| B_1h_agree     | last_24mo | 69 | +59.45% | 4.40% | 3.01 | 58.0% |
| B_1h_agree     | last_12mo | 34 | +32.25% | 4.40% | 4.16 | 67.6% |
| B_1h_agree     | last_6mo  | 21 | +13.67% | 4.40% | 2.79 | 61.9% |
| B_1h_agree     | last_3mo  | 12 |  +9.30% | 4.40% | 2.64 | 58.3% |
| C_size_scale   | last_24mo | 79 | +64.57% | 3.69% | 3.17 | 59.5% |
| C_size_scale   | last_12mo | 41 | +39.65% | 3.69% | 4.62 | 68.3% |
| C_size_scale   | last_6mo  | 24 | +17.47% | 3.69% | 3.36 | 62.5% |
| C_size_scale   | last_3mo  | 13 |  +9.75% | 3.69% | 2.84 | 53.8% |
| D_1h_early_exit| last_24mo | 77 | +12.66% | 4.63% | 1.44 | 42.9% |
| D_1h_early_exit| last_12mo | 41 | +12.74% | 4.63% | 2.14 | 48.8% |
| D_1h_early_exit| last_6mo  | 24 |  +6.86% | 4.63% | 1.92 | 41.7% |
| D_1h_early_exit| last_3mo  | 13 |  +4.50% | 4.63% | 1.91 | 38.5% |

## Detailed verdict per variant

### A — 1d agreement filter

- 48mo: takes 19 fewer trades (104 vs 123) by gating on the daily SuperTrend.
  Return drops 38 pp to +101.51%. PF nearly identical (3.22 vs 3.35), but
  fails the strict ≥ 3.26 gate.
- Recent 3mo: improves from +8.56% to +10.16% AND cuts DD from 4.40% to
  2.97%. That's the only variant with a clear improvement on the recent
  window.
- The problem: 27% of total return is given up. Acceptable if you only
  care about recent behaviour; unacceptable under the 48mo adoption
  gate.

### B — 1h agreement filter

- 48mo: drops 17 trades. Return drops 45 pp. PF below the 3.0 gate.
- Recent 3mo: marginally better (+9.30% vs +8.56%), DD unchanged.
- 1h direction is too noisy to use as a hard pre-entry filter. Whipsaws
  the entry gate.

### C — Size scaled by agreement

- 48mo: same 123 trades (alpha unchanged). Average size mult lower,
  so return falls 40 pp. DD improves modestly (4.14% vs 4.64%).
- Recent 3mo: +9.75% vs +8.56%, DD 3.69% vs 4.40%. Small improvement.
- This is the cleanest MTF design — preserves all alpha decisions,
  only modulates exposure. It's also a strict subset of what the
  Phase 5 R6 (vol-quartile sizing) rule does, but R6 conditions on
  realised volatility instead of MTF agreement and delivers a
  bigger DD improvement (2.10% vs 4.14% on 48mo).

### D — 1h early-warning exit

- Catastrophic. The 1h SuperTrend flips against a 4h position FAR
  more often than the 4h position actually fails. The early-warning
  exit kills winning trades at every minor pullback.
- Net 48mo +25.75%, PF 1.54, win rate 41.1%.
- Verdict: **never use 1h to early-exit a 4h trade**. The
  cross-TF noise ratio makes this the worst-performing variant in
  the experiment.

## Should we add MTF confirmation to live?

Strictly under the Issue #20 adoption gate: **no.** None of A, B, C
clear the return gate; D fails everywhere.

If the goal is **specifically to improve the recent window** at the
cost of long-term return, variant A (1d agreement) gives a clean
small win: +10.16% / DD 2.97% / PF 3.06 on the recent 3mo. But:

- Phase 5 R6 (vol-quartile sizing) gives +5.21% / DD 1.44% / PF 3.70
  on the same window — lower return but materially lower DD, AND
  it preserves all 123 trades over 48mo. R6 also has a known
  research-shop pedigree as the Issue #27 strongest sizing candidate.
- Variant A modifies the alpha decision (a hard pre-entry gate is
  alpha-level); R6 only modifies exposure (risk-layer). The
  architecture mapping in `ARCHITECTURE.md` says risk-layer changes
  are preferred when they achieve the same end.

**Recommendation: do not add MTF confirmation. Add R6 vol-quartile
sizing instead.** See `research/adaptive_regime_response_report.md`.
