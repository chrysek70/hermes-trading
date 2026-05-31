# Adaptive regime-based position sizing (Issue #27)

48-month walk-forward research on the currently adopted **BTC/ETH
long-short SuperTrend(10, 3) + funding-filter** candidate (Issues
#20 / #21). The question: can HMM or volatility-band sizing
reduce drawdown or improve PF on the adopted candidate *without*
materially cutting trade count?

**Headline: yes — all three sizing variants pass. The strongest
balance is volatility-quartile sizing.** No live wiring proposed
in this report; the recommendation is the next gate (user approval +
forward-paper test).

## Setup

- Universe: BTC/USDT + ETH/USDT (parallel coordinator, max 2
  concurrent positions, per-asset weight = 0.5).
- Strategy: `state/strategy_supertrend_long_short.yaml`
  (SuperTrend(10, 3) on 4h, EMA50/200 regime gate, both directions).
- Funding filter: direction-aware, block long at p ≥ 95, block short
  at p ≤ 5 (Issue #20 thresholds). **Applied to every variant in
  this study** — the baseline IS the adopted live candidate.
- Walk-forward: train=1440, test=360, embargo=6 (same geometry as
  every other experiment in this repo).
- Fees: 10 bps/side, slippage: 5 bps.
- Span: 2022-05-01 → 2026-04-30 (8 766 4h bars, 20 OOS folds).
- Runner: `scripts/run_adaptive_sizing.py`.
- Detailed trades: `results/trades_adaptive_sizing_20260531_005427.csv`.

## Sizing concept (locked per Issue #27)

| regime | multiplier |
|---|---|
| favourable | 1.00 |
| neutral | 0.50 |
| adverse / high-vol | 0.25 |

The multiplier multiplies against the existing per-asset base
(`size_per_asset = 0.5`). Sizing **never raises** above the base; it
only reduces exposure.

**HMM mapping** (per-fold per-asset 2-state Gaussian HMM on causal
features: log return, realised vol 24, ATR%, EMA50 slope,
SuperTrend distance). State→{favourable, adverse} labelling uses
train-fold trade outcomes (vol-based fallback when sparse).

- P(favourable) ≥ 0.70 → 1.00
- 0.55 ≤ P(favourable) < 0.70 → 0.50
- P(favourable) < 0.55 OR P(adverse) ≥ 0.70 → 0.25

**Volatility mapping** (24-bar log-return rolling std; quartile
thresholds computed on TRAIN bars only and applied causally to
TEST):

- Q1 (low vol) → 1.00
- Q2 / Q3 (mid) → 0.50
- Q4 (high vol) → 0.25

**Stacking** (variant 4): combined multiplier = MIN(HMM mult, vol
mult). Stacking only reduces; it can never exceed either component.

## Headline result

| variant | trades | OOS return | max DD | PF | mean mult | return / exposure | DD / exposure |
|---|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_only` (= adopted live) | 123 | +139.71% | 4.64% | 3.35 | 1.000 | +139.71% | 4.64% |
| `hmm_sizing` | 123 | +78.38% | **2.45%** | **3.84** | 0.652 | +120.13% | 3.75% |
| `vol_sizing` | 123 | +72.71% | **2.10%** | **4.63** | 0.533 | +136.54% | 3.94% |
| `hmm_plus_vol_sizing` | 123 | +59.69% | **1.57%** | **4.49** | 0.472 | +126.59% | **3.34%** |

Trade count is **identical (123)** across every variant. The sizing
layer is multiplicative — it never gates a signal — so the trade
count is bit-for-bit identical to the adopted baseline. The
adoption criterion's "must not materially reduce trade count" is met
trivially (Δ = 0.0%).

20 OOS folds; 16/20 positive in every variant.

Baseline numbers match the Issue #20 adopted candidate
byte-for-byte (+139.71% return, 4.64% DD, PF 3.35, 123 trades) — a
correctness check that the runner reproduces the existing result
before applying any overlay.

## By-regime breakdown (48mo)

**HMM regime label at entry** — confirms the HMM separates favourable
from adverse regimes on this universe:

| variant | regime | trades | win% | total return |
|---|---|---:|---:|---:|
| `hmm_sizing` | favourable | 58 | **63.8%** | **+50.22%** |
| `hmm_sizing` | adverse | 57 | 56.1% | +10.25% |
| `hmm_sizing` | warmup | 8 | 37.5% | −1.39% |

The favourable band contributes ~5× the return of the adverse band
at similar trade count. That is the textbook signature of a
working regime overlay.

**Volatility band at entry** — also separates cleanly:

| variant | band | trades | win% | total return |
|---|---|---:|---:|---:|
| `vol_sizing` | favourable (low vol Q1) | 29 | **72.4%** | **+30.28%** |
| `vol_sizing` | neutral (Q2/Q3) | 52 | 57.7% | +20.47% |
| `vol_sizing` | adverse (high vol Q4) | 42 | 50.0% | +4.65% |

Win rate falls monotonically from low → high vol. High-vol trades
have ~half the win rate of low-vol trades and ~1/7 the return per
unit trade.

The 12-month smoke run inverted this pattern (only 25 trades, the
low-vol band was a small unlucky slice — 5 trades). The 48-month
full sample is the trustworthy reading.

## Sizing vs hard-filter comparison

This experiment is a direct test of the hypothesis that **sizing is
a better risk overlay than hard filtering** for the regimes the HMM
identifies. Compared to Issue #6 / #20's hard HMM filter:

| approach | trade count vs baseline | DD vs baseline | PF vs baseline | adoption status |
|---|---:|---:|---:|---|
| HMM as hard filter (Issue #20, long-short) | reduced below 100 gate | reduced | improved | **failed** (count gate) |
| HMM as sizing multiplier (this study) | **identical** (123) | reduced (4.64 → 2.45) | improved (3.35 → 3.84) | **passes** |

This is the punchline of the experiment: **the same HMM model that
was rejected as a filter becomes a useful Risk-layer overlay once
applied as a sizing multiplier.** The filter dropped the trade
count below the adoption gate; the multiplier preserves every
signal and only modulates risk.

## Adoption recommendation

Per the Issue #27 criterion — "reduce DD or improve PF without
materially reducing trade count" — all three sizing variants pass.

**Strongest candidate: `vol_sizing`.**

- Best PF (4.63) and second-lowest DD (2.10%).
- Highest return-per-unit-exposure (+136.54%) of any sizing
  variant — essentially preserves the adopted candidate's
  efficiency while halving DD.
- Simpler than HMM (no model fit required; just a 24-bar rolling
  std and per-fold quartile thresholds). No new heavy dependency.
- Per-band breakdown is monotonic and consistent with theory.

Secondary candidate: `hmm_plus_vol_sizing`.

- Lowest DD (1.57%) and lowest DD-per-exposure (3.34%).
- The most defensive variant; cuts mean exposure to 0.47×.
- Lower absolute return than `vol_sizing` because two independent
  overlays compound their cuts.
- Worth considering if the operational preference is "minimum
  drawdown" over "best efficiency".

Tertiary: `hmm_sizing` alone.

- Slightly higher DD than `vol_sizing` (2.45% vs 2.10%) and lower
  PF (3.84 vs 4.63).
- Adds a heavy dependency (`hmmlearn`) and per-fold fit complexity
  for marginal benefit over the simpler vol overlay.

**Recommendation: pursue `vol_sizing` as the next live-candidate
add-on, contingent on (a) user approval, (b) forward paper-test
period, and (c) the live paper-fill slippage model being shipped
first — adding a sizing layer on top of a known modelling asymmetry
compounds the live-vs-research drift. The Execution-layer slippage
gap is item #1 on the architecture roadmap (Issue #25 / ARCHITECTURE.md).**

Do NOT wire this into `state/live_multiasset_long_short_funding.yaml`
from this commit. Adoption requires a separate user-explicit edit
once the prerequisite Execution-layer fix has shipped.

## Open questions for future work

1. **Volatility window sensitivity.** This study used `vol_window =
   24` (4 days at 4h) to match the HMM's `realized_vol_24` feature
   for consistency. Sensitivity to vol windows of 12 / 48 / 96 bars
   is worth testing as a follow-up (a single experiment, locked to
   the same fold geometry).
2. **Sizing-band parameter sensitivity.** The 1.0 / 0.5 / 0.25
   ladder is the Issue #27 spec. The boundary thresholds for HMM
   (0.70 / 0.55) and for vol (Q1 / Q4) are conservative defaults.
   A walk-forward search over plausible bands could either confirm
   robustness or expose overfit.
3. **Compounded-overlay interaction.** Stacked HMM + vol cuts mean
   exposure to 0.47×. If both overlays are flagging the same
   regimes (correlated cuts), the second overlay is mostly
   redundant. A correlation analysis between `hmm_size_multiplier`
   and `vol_size_multiplier` per bar would quantify this.
4. **Sizing during DD events.** A useful diagnostic: at the exact
   bars where the baseline reached its 4.64% DD low, what was the
   sizing multiplier on the trades that contributed to the
   drawdown? If sizing was already low at those bars, the layer is
   working as designed; if it was high, the layer is mis-timed.

## Out of scope (explicit)

- Real-money execution.
- New alpha logic.
- Funding filter parameter changes.
- New asset universe.
- LLM / RL regime detection.

## Files

- Runner: `scripts/run_adaptive_sizing.py`
- Summary CSV: `results/adaptive_sizing_comparison_20260531_005427.csv`
- Summary MD: `results/adaptive_sizing_comparison_20260531_005427.md`
- Per-trade CSV: `results/trades_adaptive_sizing_20260531_005427.csv`
  (492 rows; includes `overlay_size_multiplier`,
  `hmm_regime_label_at_entry`, `vol_regime_label_at_entry`,
  `hmm_size_multiplier_at_entry`, `vol_size_multiplier_at_entry`
  for downstream regime-conditioned analysis).
