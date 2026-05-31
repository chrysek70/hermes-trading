# BTC/ETH long-short SuperTrend + Overlays — Report (Issue #20)

Issue #19 produced a long-short variant that cleared every gate
except DD (5.76% vs the 5.54% live-floor cap, a 0.22 pp miss). This
experiment tests whether any already-implemented overlay (HMM,
funding, RS) can pull DD below 5.54% on the long-short variant
without losing the return / PF advantage. No new parameters, no new
thresholds — same configs from Issues #5, #6, #7.

48-month walk-forward on BTC + ETH 4h, 20 folds (train 1440 / test
360 / embargo 6), fees 10 bps/side + 5 bps slippage. SuperTrend
(10, 3) unchanged. Live config (`state/live_multiasset.yaml`)
unmodified and untouched.

**Adoption gates (from Issue #20 spec):**

- **Primary:** DD ≤ 5.54% AND PF ≥ 3.26 AND return ≥ +139.47% AND trades ≥ 100
- **Secondary:** DD ≤ 5.54% AND PF ≥ 3.00 AND return ≥ +120% AND trades ≥ 100

**Result: `funding_filter` passes the PRIMARY gate. `funding_sizing` passes the SECONDARY gate.** Live config stays unchanged per the hard rules — user explicit approval required before any live switch.

---

## Walk-forward results

| variant | folds | n | L | S | OOS return | L ret | S ret | max DD | PF | Sharpe | win % | folds+ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `btc_eth_long_short_baseline` (Issue #19) | 20 | 129 | 65 | 64 | +139.47% | +39.72% | +99.75% | 5.76% | 3.26 | 0.379 | 57.4% | 16/20 |
| `btc_eth_long_short_hmm_filter` | 20 | 74 | 40 | 34 | +49.25% | +14.66% | +34.59% | **4.29%** | 3.04 | 0.339 | 54.1% | 15/20 |
| `btc_eth_long_short_hmm_sizing` | 20 | 74 | 40 | 34 | +49.25% | +14.66% | +34.59% | 4.29% | 3.04 | 0.339 | 54.1% | 15/20 |
| **`btc_eth_long_short_funding_filter`** | 20 | **123** | 63 | 60 | **+139.71%** | +39.97% | +99.74% | **4.64%** | **3.35** | **0.391** | **58.5%** | **16/20** |
| `btc_eth_long_short_funding_sizing` | 20 | 123 | 63 | 60 | +133.64% | +35.13% | +98.50% | 4.71% | 3.34 | 0.386 | 58.5% | 16/20 |
| `btc_eth_long_short_rs_sizing` | 20 | 92 | 48 | 44 | +74.61% | +24.71% | +49.90% | **4.09%** | 3.64 | 0.408 | 59.8% | **17/20** |

## Decision per locked criteria

| variant | trades ≥ 100 | PF ≥ 3.26 | DD ≤ 5.54% | return ≥ +139.47% | verdict |
|---|---|---|---|---|---|
| `funding_filter` | 123 ✓ | 3.35 ✓ | 4.64% ✓ | +139.71% ✓ | **ADOPTED — primary** |
| `funding_sizing` | 123 ✓ | 3.34 ✓ | 4.71% ✓ | +133.64% (✗ by 5.83 pp) | secondary (return ≥ +120% ✓, PF ≥ 3.00 ✓) |
| `hmm_filter` | 74 ✗ | 3.04 ✗ | 4.29% ✓ | +49.25% ✗ | not adopted (count + return + PF) |
| `hmm_sizing` | 74 ✗ | 3.04 ✗ | 4.29% ✓ | +49.25% ✗ | not adopted (count + return + PF) |
| `rs_sizing` | 92 ✗ | 3.64 ✓ | 4.09% ✓ | +74.61% ✗ | not adopted (count + return) |

**Adopted as research candidate: `btc_eth_long_short_funding_filter`.** First overlay variant in the project to pass a primary adoption gate cleanly.

---

## Answers to the 10 spec questions

### 1. Did any overlay reduce DD below 5.54%?
**Yes — every overlay reduced DD below 5.54%.** Best: `rs_sizing` at 4.09%; worst (still ≤ gate): `funding_sizing` at 4.71%; live floor was 5.54%, baseline long-short 5.76%.

| overlay | DD reduction vs baseline 5.76% |
|---|---:|
| hmm_filter | -1.47 pp (-25.5%) |
| funding_filter | -1.12 pp (-19.4%) |
| funding_sizing | -1.05 pp (-18.2%) |
| rs_sizing | -1.67 pp (-29.0%) |

### 2. Did any overlay preserve return above +139.47%?
**Yes — `funding_filter` produced +139.71% (slightly higher).** Every other overlay cut return: HMM to +49%, funding_sizing to +134%, RS to +75%.

### 3. Did any overlay preserve PF above 3.26?
**Yes — `funding_filter` 3.35, `funding_sizing` 3.34, `rs_sizing` 3.64. HMM variants slipped to 3.04.**

### 4. Did HMM help longs, shorts, or both?
**Both directions cut roughly proportionally** (long 65 → 40, short 64 → 34). The HMM's adverse state — high realised volatility — affects both directions of trend-following equally. The DD reduction (5.76% → 4.29%) is the largest of any overlay, but the return collapse (+139% → +49%) means HMM filters out too much for this trade base. The pattern matches Issue #6's per-asset behaviour: HMM is mechanically strong but high-selectivity.

### 5. Did funding help longs, shorts, or both?
**Both** — direction-aware mapping (block longs at p95+, block shorts at p≤5) worked symmetrically. Trade counts dropped by similar small amounts on each side (long 65 → 63, short 64 → 60). Critically, those few removed trades carried disproportionate DD contribution: the path-dependent max-DD path improved by 1.12 pp while the average winner / loser was barely touched, which is why return and PF held.

### 6. Did RS apply cleanly to shorts?
**Yes — direction-aware mapping was clean.** Long-side decision for an asset = "asset stronger" (its own decision); short-side = "other asset stronger" (which means *this* asset is weaker → favored for short). No new RS rule designed; just the existing `build_asset_decisions` re-used with swapped inputs. The RS variant produced the **highest PF (3.64) and lowest DD (4.09%) of any variant**, but cut trade count to 92 (below the 100 gate) and return to +74.61%. The mechanism works; the count-discipline gate blocks adoption.

### 7. Did overlays reduce trade count too much?
**HMM and RS yes; funding no.**

| overlay | trades | drop vs 129 |
|---|---:|---:|
| hmm_filter / sizing | 74 | -43% |
| rs_sizing | 92 | -29% |
| funding_filter / sizing | 123 | -5% |

The funding filter is the only overlay that preserves the trade base. That is why it's the only one that clears the primary gate cleanly.

### 8. Which variant is the best candidate?
**`funding_filter`.** Highest Sharpe (0.391), highest win rate (58.5%), 16/20 folds positive, PF 3.35 > 3.26 gate, return +139.71% > 139.47% gate, DD 4.64% < 5.54% gate, trades 123 > 100 gate. All four primary gates cleared. RS sizing is more risk-efficient (DD 4.09%, PF 3.64, 17/20 folds) but cuts return too much for the primary or secondary gate.

### 9. Should long-short now be adopted?
**Yes, as a research candidate** — specifically `btc_eth_long_short_funding_filter`. First overlay-variant in the project to pass a primary adoption gate without literal sample-illusion. Updated `ROADMAP.md` and `RESEARCH_LOG.md` to reflect the adoption.

### 10. Should live config remain long-only until explicit user approval?
**Yes.** Per the hard rules in Issue #20: "If an overlay passes: update README, ROADMAP, RESEARCH_LOG, mark as adopted research candidate only, **do not modify live config without my explicit approval**." `state/live_multiasset.yaml` continues to point at the long-only `state/strategy_supertrend.yaml`. The user can switch to the long-short + funding-filter variant manually by:

1. Wiring a funding-filter decision attacher into `hermes_trading/multi_loop.py` (currently the multi-asset live worker does not consume decisions_df — it only runs the bare strategy). That is a separate code change.
2. OR pointing the live config at `state/strategy_supertrend_long_short.yaml` directly to get the long-short variant without the funding filter, accepting the 0.22 pp DD miss.

Both are deliberate manual decisions, not auto-applied.

---

## Honest caveats

1. **The funding filter affects very few trades.** Long-short baseline 129 → funding_filter 123 = only 6 trades removed. The DD improvement (1.12 pp) is real but comes from a small number of high-impact removals — the path-dependent nature of DD means a single avoided losing trade at a drawdown peak can move the metric meaningfully. **Medium-high confidence**, not certainty.

2. **Issue #7 said funding effect was within noise on long-only.** That was correct for long-only — only 2 of 65 trades were affected, return barely moved, DD shifted within noise. On long-short, the filter has more raw trades to act on (129 baseline) and the symmetric inversion catches both extreme-positive (overheated longs) and extreme-negative (squeeze-bottom shorts) funding bars. The effect is structurally larger because the long-short variant exposes the filter to both ends of the funding distribution.

3. **RS sizing was the cleanest mechanism wise** (highest PF, lowest DD, 17/20 folds positive) — only the 30-trade discipline applied to the long-short universe (here re-set to 100 trades, the primary gate floor) keeps it out. If sample size grows further (top-5 universe, longer history), RS sizing on long-short is a strong adoption candidate.

4. **HMM cut everything too much.** Same pattern as Issue #6. The HMM has the strongest *per-asset* signal but is also the most selective. On 129 trades it still cut to 74 — about right for the per-side gate (30 per direction). Sample size is the recurring blocker for HMM in this project; the multi-asset top-5 path is the natural place to test it next.

5. **Funding-filter on long-short is not in the live worker yet.** The current `multi_loop.run` does not attach decisions_df. The adopted research candidate exists in research / backtest only.

---

## Artifacts

- `scripts/run_long_short_overlays.py` (new runner; reuses existing modules)
- `results/long_short_overlay_comparison_20260530_222812.csv`
- `results/long_short_overlay_comparison_20260530_222812.md`
- `results/trades_long_short_overlay_20260530_222812.csv` (615 rows; gitignored)
- `research/long_short_overlay_report.md` (this file)

No changes to `signals.py`, `multi_loop.py`, `loop.py`, or any strategy yaml. The new RS direction-aware mapping is implemented in the script itself by re-using the existing `build_asset_decisions` API with swapped inputs (long uses asset's own decision, short uses the other asset's). The funding direction-aware mapping is implemented in the script's `_build_funding_decisions` helper.

## Closing-the-loop summary

- **Issue #20 status:** closed, primary gate cleared by `funding_filter`.
- **Headline:** the funding filter, applied direction-aware (block longs at p95+, block shorts at p≤5), reduces BTC/ETH long-short DD 5.76% → 4.64% while preserving return (+139.71% vs +139.47% baseline) and lifting PF (3.35 vs 3.26). All four primary adoption gates cleared.
- **Adopted as research candidate:** `btc_eth_long_short_funding_filter`. First overlay variant to clear a primary gate in the project.
- **Live worker:** unchanged. `state/live_multiasset.yaml` still loads the long-only `state/strategy_supertrend.yaml`. Wiring the adopted research candidate into live requires (a) implementing the long-short strategy yaml in live AND (b) attaching the funding-decisions DataFrame in `multi_loop.run`. Neither auto-applied.
- **Next research priority:** if user accepts the long-short + funding filter as the new strongest adopted candidate, the cleanest next experiment is **wiring this overlay into the live worker** (Issue-pending). If user keeps live conservative, **HMM and RS variants on a top-5 universe** is the highest-information next research step — the trade-count discipline that blocked HMM and RS here would be substantially eased by a 5-asset trade base.
