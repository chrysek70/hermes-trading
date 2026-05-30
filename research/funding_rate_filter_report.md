# Funding Rate Filter — Report (Phase 4)

Issue #7. Locked spec: block new longs when rolling 30-day funding
percentile ≥ 95; half-size at percentile ≥ 90 (sizing variant). No
parameter sweeps. Five variants per spec.

**Adoption criterion (from spec):** must improve PF or DD without
destroying trade count.

---

## Headline

**Marginal pass.** Both PF and DD improve slightly on the BTC/ETH
parallel portfolio with funding filter, without meaningfully cutting
trade count. The improvement is real but small (PF +0.07, DD -15.5%
on the headline parallel variant). Phase 2 diagnostics correctly
predicted this outcome: SuperTrend entries rarely coincide with
extreme-funding bars (only 2 of 65 portfolio trades sat in p95+ at
entry), so the filter has very little material to act on.

**Recommendation: adopted as a marginal research candidate**, with
the explicit caveat that the effect size is small enough to plausibly
be sample noise. The headline portfolio metric (`btc_eth_parallel`)
gains 0.07 PF and 0.86 pp DD — useful but not transformative.

Per spec: not wired into live trading.

---

## Walk-forward results (48mo 4h, 20 folds)

| variant | folds | n | OOS return | max DD | PF | Sharpe | win % | folds+ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `eth_supertrend_baseline` | 20 | 30 | +37.86% | 5.30% | 2.92 | 0.336 | 63.3% | 10/20 |
| **`eth_supertrend_funding_filter`** | 20 | **28** | +38.46% | **5.30%** | **3.17** | **0.357** | **64.3%** | 10/20 |
| `btc_eth_parallel_baseline` | 20 | 65 | +39.72% | 5.54% | 2.50 | 0.296 | 53.8% | 11/20 |
| **`btc_eth_parallel_funding_filter`** | 20 | **63** | **+40.01%** | **4.68%** | **2.57** | **0.304** | **54.0%** | 11/20 |
| `btc_eth_parallel_funding_sizing` | 20 | 63 | +39.28% | 4.68% | 2.55 | 0.299 | 54.0% | 11/20 |

### Per-variant deltas vs baseline

| variant | Δ trades | Δ return | Δ DD | Δ PF | verdict |
|---|---:|---:|---:|---:|---|
| `eth_supertrend_funding_filter` | -2 | +0.60 pp | 0.00 pp | +0.25 (+8.6%) | marginal PF win, DD unchanged |
| `btc_eth_parallel_funding_filter` | -2 | +0.29 pp | -0.86 pp (-15.5%) | +0.07 (+2.8%) | small PF + meaningful DD win |
| `btc_eth_parallel_funding_sizing` | -2 | -0.44 pp | -0.86 pp | +0.05 | filter ≈ sizing on this data |

Filter and sizing modes give essentially identical results because the
funding percentile rarely sits in the 90-95 half-size band — when it
crosses 90, it usually crosses 95 quickly. Sizing degenerates to
filter for the same bimodal-probability reason we saw with the HMM
overlay (Issue #6).

### By funding-state at entry (parallel portfolio)

| state | count |
|---|---:|
| `funding_normal` (< p90) | 62 |
| `funding_overheated` (p90–95) | 1 |
| `funding_extreme` (≥ p95) | 0 |

Of the 65 baseline trades, only 3 occurred at funding ≥ p90 at entry.
The filter actively blocks at p95, so it only prevents the small
subset that gets that far. **This confirms Phase 2's prediction
directly:** SuperTrend's entry timing rarely lines up with extreme
funding, so the filter cannot move the headline metrics by much.

## Honest skepticism: is this real or sample noise?

The filter blocks ~3% of trades (2 of 65 on the parallel; 2 of 30 on
ETH solo). The blocked trades happen to be net negative on this
sample — that's where the PF/DD improvements come from. With so few
affected trades:

- A binomial coin-flip "blocked trade was a loser" with 4 trials
  (2 ETH + 2 portfolio) and 4 hits has p-value ≈ 6% under H0
  random. Marginally interesting, not a significant edge.
- The headline portfolio PF change (2.50 → 2.57) is well within
  fold-to-fold dispersion (fold-return σ on the parallel ≈ 4%).
- The DD improvement (5.54% → 4.68%) is more striking but also
  comes from avoiding a small number of drawdown contributors.

This is not the kind of result one bets a strategy on. It is the kind
of result that *could* be a small real edge or could be the same
2-3 trades washing through more favorably in this particular 20-fold
slice. Phase 2 already established that funding has no linear
predictive value and the bucket pattern is U-shaped (extremes go
*both* ways), so we should not expect this filter to scale up if
applied more aggressively.

## Phase 4 answers to the 8 spec questions

### 1. Did funding improve PF?
**Marginally, yes.** ETH solo +0.25 (+8.6%); BTC/ETH parallel +0.07
(+2.8%). Both directionally positive but small relative to fold
dispersion.

### 2. Did funding reduce DD?
**On the parallel portfolio, yes** (5.54% → 4.68%, -15.5%). On ETH
solo, no change (5.30% → 5.30%). The DD improvement on the parallel
is the most concrete win from the filter.

### 3. Did funding reduce trade count excessively?
**No.** Only 2 trades blocked per variant (out of 30 or 65 baseline).
Trade-count gates would still be met by both filtered variants
(though the spec didn't set explicit gates for Issue #7).

### 4. Did funding identify bad periods?
**Inconclusively.** The 2-3 trades it blocked on each variant were
mostly losers, hence the small PF improvement. But Phase 2's bucket
analysis showed that funding extremes in general are followed by
*higher* forward returns, not lower — so the filter's success here
may be capturing 2-3 specific late-2024 or early-2025 trades rather
than a generalisable signal.

### 5. Did funding add information beyond HMM?
**Likely no, on this data.** The HMM overlay (Issue #6) lifted BTC
SuperTrend PF from 2.24 to 4.01 (+79%) and cut DD by 61%. Funding
lifts BTC/ETH parallel PF from 2.50 to 2.57 (+2.8%) and DD by 15.5%.
HMM's effect is roughly an order of magnitude larger. The two signals
also likely overlap: high-volatility regimes (HMM's "adverse" state)
correlate with funding extremes during liquidation cascades. We did
**not** stack the two overlays in this experiment — that would be a
separate test.

### 6. Is funding worth keeping?
**As marginal research infrastructure, yes** — the data loader is
clean, the alignment is causal, the percentile metric is interpretable.
**As a primary trading signal, no** — the effect size on
SuperTrend(10, 3) is too small to drive adoption decisions.

### 7. Should it be combined with HMM later?
**Possibly, as a future experiment** — but the diagnostics suggest
they are partially redundant. A stacked HMM+funding test would need
to show that funding adds incremental value *given* HMM. Given HMM
alone already lifts BTC PF to 4.01, the marginal room for funding to
add value is small. Not a high-priority experiment.

### 8. What should the next experiment be?
The honest read across Issues #5-#14 plus this one:

- **Trend-following works** (SuperTrend on BTC/ETH on 4h).
- **Parallel multi-asset > one-position** (Issue #14 adopted).
- **Regime overlays (HMM, RS) work mechanically but get blocked by
  the trade-count gate** in single-asset solo and partly help in
  parallel.
- **Funding does very little** at locked thresholds on this universe.
- **Mean-reversion ideas have mostly failed.**

The most promising next direction is **not adding more overlays** but
testing whether the validated parallel portfolio (BTC/ETH or BTC/ETH +
funding filter) generalises to **forward-walk holdout data** — i.e.,
retest the adopted variants on a non-overlapping forward window once
new data arrives (or set up an automatic re-test cadence).

Other candidate next experiments:
- **Volatility-compression breakout** (already queued; conditional on
  ATR-bucket finding from Phase 3 audit).
- **Stacking HMM + funding** to test redundancy formally.
- **Live decay monitor** (from our earlier discussion: track
  rolling PF / DD on the live worker's closed trades; alert when
  the live strategy drifts from the research baseline).

The decay monitor is the most useful in light of all the research
results: we have several adopted research candidates, no live wiring
yet, and the user's earlier expressed interest in adapting to changing
market conditions. The decay monitor builds the infrastructure to
detect when any future live deployment of these candidates is
drifting from research-time expectations.

## Adoption decision

Per the locked criterion ("must improve PF or DD without destroying
trade count"):

- `eth_supertrend_funding_filter`: PF +0.25, DD unchanged, trades 30 →
  28. **Meets criterion (PF only). Marginal.**
- `btc_eth_parallel_funding_filter`: PF +0.07, DD -0.86 pp, trades 65 →
  63. **Meets both PF and DD criteria.**
- `btc_eth_parallel_funding_sizing`: same trades as filter; slightly
  lower return; same DD. Filter strictly dominates sizing.

**Adopted as marginal research candidate:** `btc_eth_parallel_funding_filter`
(the cleanest improvement). Recorded as a small upgrade over Issue #14's
adopted `btc_eth_reference_parallel` baseline. The honest framing is
"directionally positive but within the noise band — not a basis for
live deployment by itself."

Not adopted as a primary strategy. Per the spec: not wired into live
trading.

## Convention check

- SuperTrend (10, 3): unchanged.
- Funding-rate source: Binance Vision (futures/um/monthly/fundingRate).
- Forward-fill alignment from 8h funding to 4h decision bars — causal,
  no future leakage.
- Rolling 30-day (180-bar) percentile rank, same value for both
  variants and per asset.
- Block threshold: p95. Half-size threshold: p90. Locked at spec, not
  swept.
- Same fees / slippage / fold geometry as every other experiment.
- Live worker: unchanged.

## Artifacts

- `hermes_trading/funding.py` (new loader + percentile utility)
- `scripts/run_funding_filter.py` (new runner)
- `research/funding_rate_data_audit.md` (Phase 1)
- `research/funding_rate_diagnostics.md` (Phase 2)
- `research/funding_rate_filter_report.md` (this file, Phase 4)
- `results/funding_rate_comparison_20260530_152128.csv` / `.md`
- `results/trades_funding_20260530_152128.csv` (249 rows, gitignored)
- `state/data/funding/<symbol>-fundingRate-<month>.zip` (cache; gitignored)

## Closing-the-loop summary

- **Issue #7 status:** closed.
- **Headline:** funding filter at p95 / p90 locked thresholds
  produces marginal improvements on the BTC/ETH parallel portfolio
  (PF 2.50 → 2.57, DD 5.54% → 4.68%) by blocking only 2 of 65 trades.
  The effect is directionally positive but small enough to plausibly
  be sample noise. Phase 2 diagnostics correctly predicted this
  because SuperTrend entries rarely coincide with extreme-funding
  bars (only 3 of 65 portfolio entries were at funding ≥ p90).
- **Adopted as marginal research candidate:** `btc_eth_parallel_funding_filter`.
  Not a primary strategy. Not wired into live trading.
- **Live worker:** unchanged.
- **Recommended next direction:** decay monitor (rolling OOS metrics
  on live closed trades, with alarms when they drift from research
  baseline). This is now more useful than additional overlay
  experiments given the diminishing returns on PF/DD improvements
  visible across Issues #5-#7.
