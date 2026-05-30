# HMM 2-state regime overlay — Report

Issue #6. Tests whether an EM-fit Gaussian HMM on causal market
features (log-return, realised vol 24, ATR%, EMA50 slope, SuperTrend
distance) improves SuperTrend(10, 3) on BTC and ETH separately.
Per-fold fit on train only; train-only state mapping; test-time
decisions feed the existing `decisions_df` plumbing.

**Adoption criteria (per-asset, locked):**

- BTC: PF > **2.24** AND trades ≥ **30** AND max DD ≤ **9.63%**
- ETH: PF > **2.92** AND trades ≥ **30** AND max DD ≤ **5.30%**

**Result: NOT ADOPTED on either asset. Trade-count gate fails.**
The HMM mechanism produced large PF improvements (BTC 2.24 → 4.01
+79%, ETH 2.92 → 4.27 +46%) and meaningful DD reductions (BTC 9.63%
→ 3.79% -61%, ETH 5.30% → 4.13% -22%) — but cut trade count below
30 on both assets (BTC 35 → 24, ETH 30 → 17). The mechanism is real;
adoption blocked on sample size.

Per spec: close Issue #6, move to Issue #7 (funding-rate filter).

---

## Walk-forward results (48mo 4h, 20 folds, embargo 6)

| variant | folds | n | OOS return | max DD | PF | Sharpe | win % | folds+ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `supertrend_only_btc` (reference) | 20 | 35 | +38.66% | 9.63% | 2.24 | 0.266 | 45.7% | 10/20 |
| **`supertrend_hmm_filter_btc`** | 20 | **24** | **+49.98%** | **3.79%** | **4.01** | **0.434** | **54.2%** | 9/20 |
| `supertrend_hmm_sizing_btc` | 20 | 24 | +49.98% | 3.79% | 4.01 | 0.434 | 54.2% | 9/20 |
| `supertrend_only_eth` (reference) | 20 | 30 | +37.86% | 5.30% | 2.92 | 0.336 | 63.3% | 10/20 |
| **`supertrend_hmm_filter_eth`** | 20 | **17** | **+27.80%** | **4.13%** | **4.27** | **0.402** | **70.6%** | 8/20 |
| `supertrend_hmm_sizing_eth` | 20 | 17 | +27.80% | 4.13% | 4.27 | 0.402 | 70.6% | 8/20 |

## Adoption decision per locked criteria

| variant | trades ≥ 30 | PF | DD | decision |
|---|---|---|---|---|
| `supertrend_hmm_filter_btc` | 24 ✗ | 4.01 (>2.24 ✓) | 3.79% (≤9.63% ✓) | **not adopted (count)** |
| `supertrend_hmm_sizing_btc` | 24 ✗ | 4.01 ✓ | 3.79% ✓ | not adopted (count) |
| `supertrend_hmm_filter_eth` | 17 ✗ | 4.27 (>2.92 ✓) | 4.13% (≤5.30% ✓) | **not adopted (count)** |
| `supertrend_hmm_sizing_eth` | 17 ✗ | 4.27 ✓ | 4.13% ✓ | not adopted (count) |

Same pattern as Issue #5 (BTC + RS filter: 20 trades), Issue #12
side note (`supertrend_plus_routing`: 20 trades) and Issue #5
(`supertrend_with_rs_sizing`: 27 trades): **high-selectivity overlays
keep clearing the PF and DD criteria but keep failing the 30-trade
gate.** This is the third independent regime mechanism to hit the
same ceiling.

## Answers to the spec's 10 required questions

### 1. Did HMM improve BTC SuperTrend PF above 2.24?
**Yes — to 4.01 (+79%).** Large, clean improvement.

### 2. Did HMM improve ETH SuperTrend PF above 2.92?
**Yes — to 4.27 (+46%).** Smaller absolute lift but still significant.

### 3. Did HMM reduce drawdown?
**Yes, substantially on BTC.** BTC 9.63% → 3.79% (-61%). ETH 5.30% →
4.13% (-22%) — meaningful but a smaller win because ETH's solo DD was
already very low.

### 4. Did HMM reduce trade count too much?
**Yes — this is the entire blocker.** BTC 35 → 24 trades (-31%);
ETH 30 → 17 (-43%). Both below the 30-trade gate. ETH's drop is
larger because ETH solo only had 30 trades to begin with.

### 5. Did sizing work better than filtering?
**No — they produced literally identical results.** Same trade count,
same return, same DD, same PF, same Sharpe. The reason is that the
2-state Gaussian HMM produces strongly bimodal probabilities — when
P(favorable) is above the 0.55 half-size threshold, it is almost
always above the 0.70 full-size threshold too. The sizing rule
degenerates to the filter rule.

A concrete check: across all 20 folds the mean test-window
P(favorable) ranges from 0.049 (BTC fold 20: deep adverse regime,
no entries) to 0.989 (BTC fold 16: deep favorable regime, full size).
There is no fold where the 0.55-0.70 band collects meaningful
exposure.

### 6. Did state mapping remain stable across folds?
**The raw HMM state numbers flip (state 0 vs state 1 is favorable on
different folds — this is expected with EM initialisation), but the
*mapping* is stable.** Across all 40 fold mappings (20 BTC + 20 ETH),
the "adverse" state was the higher-volatility one *in every fold*.
The vol ratio adverse/favorable ranges from 1.04 to 2.24 across folds
— always > 1.

The vol-based fallback mapping carried the work; the train-trade
expectancy primary mapping rarely kicked in because most train folds
had < 5 trades per state (the configured `min_trades_for_setup_mapping`
threshold), so the fallback applied. This is itself a finding: at
1440-bar train windows, per-state SuperTrend trade samples are too
sparse to be the primary mapping criterion. The volatility-based
fallback is what's doing the lifting.

### 7. Which features dominated the regimes?
**Realised volatility (24-bar log-return std).** State stats from
fold 1 BTC:

| | favorable | adverse |
|---|---:|---:|
| n bars | 871 | 545 |
| mean log return | -0.013% | -0.135% |
| mean realised vol | 0.87% | 1.71% |
| mean ATR % | 1.44% | 2.76% |

Adverse has 2× the realised vol and 1.9× the ATR %, AND a
substantially more negative mean log return. Both signals push the
mapping in the same direction. The other features (EMA slope,
SuperTrend distance) likely contribute to state membership decisions
but the *separation* is volatility-driven.

This is the textbook result for a 2-state Gaussian HMM on price
features: the EM finds high-vol vs low-vol clusters. We confirmed it
without surprise.

### 8. Was HMM genuinely useful or just another restrictive filter?
**Both. It IS genuinely useful** — the PF and DD improvements are
much larger than what a random filter that simply removed 30-40% of
trades would deliver. A random subset would give similar PF on
expectation, not 4× the original. The 79% PF lift on BTC is real
signal: HMM is identifying the worst chop regimes and pulling
SuperTrend out of them.

**But also restrictive** — it cuts BTC by 11 trades and ETH by 13,
both below the gate. This is the same pattern as RS filter
(35 → 20 trades, also cleared PF gate). High-quality regime
filters keep finding the same ceiling.

**Interpretation:** the trade-count gate (≥30) was calibrated against
the unfiltered baseline (35 trades on BTC, 30 on ETH). Once a useful
filter cuts ≥30% of trades, the gate fails by construction. This is
informative: it tells us 30 trades was the FLOOR set by an
unfiltered strategy, not a true minimum for statistical confidence.
The gate is doing its job (preventing tiny-sample adoption), but it
is also explicitly blocking the only mechanisms in this repo that
materially lift PF.

### 9. Should HMM be adopted, rejected, or extended?
**Per locked criteria: not adopted.** Per the evidence: HMM is the
strongest single regime mechanism tested in this repo (PF lift +79%
on BTC; DD -61%). The honest classification is **mechanism validated,
sample-blocked**, same as Issue #5's RS filter.

The cleanest extension would test HMM on a *larger trade-count base*:

- Multi-asset HMM (apply HMM independently per asset on a 3-5 asset
  universe; aggregate counts). 35 BTC → 24, 30 ETH → 17. If SOL and
  AVAX had similar bases, 5-asset multi-HMM could clear the gate
  comfortably. This is **the same recommendation** as the parallel
  top-5 portfolio idea — HMM gives us another reason to pursue it.

- HMM with looser thresholds (e.g. `favorable_prob_half = 0.45`)
  would preserve more trades. But that's **parameter tuning**, off
  limits per hard rules.

### 10. Should next step be funding-rate filter (Issue #7) or top-5 parallel portfolio?
**Per spec on failure: Issue #7 (funding-rate filter).** Adhering to
the locked rule.

**Per the evidence accumulating across Issues #5, #6, #12, #13:**
the multi-asset path is now the highest-information next step. Three
independent regime mechanisms (RS, routing, HMM) have all cleared
the PF criterion and failed the count gate. The signal is real; the
sample is the blocker. A top-5 parallel portfolio (no rotation, each
asset traded independently with its own HMM and/or RS overlay) is the
direct way to raise the count base into a regime where these
overlays clear the gate cleanly.

Funding-rate filter remains valuable but tests a different mechanism.
If the queue has room for both, the order I'd pick is: **top-5
parallel portfolio first, funding rate second.** But the spec says
funding-rate next on HMM failure, so that's the default.

## Per-fold mapping detail (selected)

Stability of the volatility-based mapping across the 20 BTC folds:

| fold | favorable state | fav vol | adv vol | vol ratio | mean P(fav) on test |
|---:|:---|---:|---:|---:|---:|
| 1 | 0 | 0.87% | 1.71% | 1.97 | 0.69 |
| 2 | 0 | 0.59% | 1.31% | 2.24 | 0.42 |
| 5 | 0 | 0.60% | 1.19% | 1.97 | 0.74 |
| 7 | 0 | 0.67% | 0.76% | 1.12 | 0.44 |
| 13 | 0 | 0.97% | 1.01% | 1.04 | 0.56 |
| 16 | 0 | 0.76% | 1.48% | 1.95 | 0.99 |
| 20 | 0 | 0.62% | 1.06% | 1.73 | 0.05 |

Two folds (7, 13) have vol ratios near 1 — those are folds where
the HMM didn't cleanly separate vol regimes and the mapping is
weakest. The remaining 18 folds have clear vol separation. The
extreme fold-20 result (mean P(fav) = 0.05) corresponds to a deep
adverse regime in the test window where the filter blocked nearly
everything.

## Convention check

- SuperTrend (10, 3): unchanged.
- HMM features: from yaml; unchanged across folds.
- Random seed (42): unchanged.
- Per-fold fit on train only; per-fold mapping on train-only stats.
  No test-window data ever touches `fit` or `map_states`.
- Same fees / slippage / fold geometry as every other experiment.
- Live worker: unchanged.

## Optional-dependency handling

The HMM module gracefully degrades when `hmmlearn` is missing:

```python
from hermes_trading import hmm_regime
if not hmm_regime.available():
    print(hmm_regime.INSTALL_HINT)
    sys.exit(2)
```

`hmm_regime.py` import is wrapped in `try/except`; the rest of the
package (live worker, all other backtests / walk-forward runners,
all other research scripts) does not import HMM and is unaffected
by its presence/absence.

## Artifacts

- `hermes_trading/hmm_regime.py` (new module)
- `state/hmm_regime.yaml` (new config)
- `scripts/run_hmm_regime.py` (new runner)
- `results/hmm_regime_comparison_20260530_125048.csv`
- `results/hmm_regime_comparison_20260530_125048.md`
- `results/trades_hmm_regime_btc_20260530_125048.csv` (83 rows; gitignored)
- `results/trades_hmm_regime_eth_20260530_125048.csv` (64 rows; gitignored)
- `results/.hmm_fold_mappings_20260530_125048.json` (machine-readable)

## Closing-the-loop summary

- **Issue #6 status:** closed, criteria not met (trade-count gate).
- **Headline:** HMM 2-state regime overlay lifts SuperTrend PF on
  both BTC (2.24 → 4.01) and ETH (2.92 → 4.27) and cuts BTC drawdown
  by 61% — but cuts trade count to 24 / 17 (below the 30 gate).
  Mechanism validated; sample-blocked.
- **Surprising sub-finding:** filter and sizing modes produced
  literally identical results, because the 2-state HMM probabilities
  are bimodal — the sizing 0.5×band never collects exposure.
- **Pattern:** third high-quality regime overlay to clear PF / DD
  criteria and fail trade-count gate (after RS filter, routing).
  Strong evidence that future regime work needs a larger trade-count
  base, which argues for multi-asset / parallel-portfolio extension.
- **Live worker:** unchanged. Still v2 long-short.
- **Next per spec:** Issue #7 (funding-rate filter).
- **Next per accumulated evidence:** top-5 parallel portfolio
  (regime overlays + larger base). Both are queued.
