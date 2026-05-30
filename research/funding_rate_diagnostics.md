# Funding Rate Diagnostics (Phase 2)

Issue #7. Measures whether perpetuals funding rate has predictive or
coincident relationship to BTC/ETH returns, SuperTrend trade outcomes,
or drawdowns. **All measurements are diagnostic — no parameters are
fit, no trading logic is modified.**

## Setup

- Data: 48mo aligned, 2022-05-01 → 2026-04-30 (8766 4h bars)
- Funding source: Binance Vision (see `funding_rate_data_audit.md`)
- Forward-fill funding rate to 4h decision bars (causal)
- Rolling 30-day (180-bar) percentile rank used for bucketing
- Funding rate is in raw form (e.g. +0.0064 = 0.64 bps per 8h ≈ 7.0% APR)

## Correlation: funding rate vs price return

Full-window Pearson correlation between funding rate at bar t and
return at various horizons:

| horizon | BTC | ETH |
|---|---:|---:|
| coincident (4h ending at t) | -0.013 | +0.007 |
| next 4h | -0.015 | +0.002 |
| next 24h | +0.003 | +0.017 |
| next 5d | +0.013 | +0.030 |

**All four correlations are within statistical noise** on n=8000+
bars. The 95% confidence band on a true-zero correlation at this
sample size is roughly ±0.022 — only ETH's "next 5d" correlation
(+0.030) is marginally outside that band, and even then it points the
*wrong* way for the spec's hypothesis: higher funding correlates with
*positive* forward 5-day return on ETH, not negative.

**Verdict on linear predictive value: none.** Funding rate is not a
linear forecaster of crypto price at any tested horizon.

## Forward-return by funding-percentile bucket

If the relationship is non-linear (only the *extremes* matter), the
correlation could miss it. So we partition bars by the rolling 30-day
funding percentile and measure forward 5-day return per bucket:

### BTC

| bucket | n bars | mean 5-day fwd ret | win % |
|---|---:|---:|---:|
| p0-5 (very negative) | 553 | +1.210% | 57.0% |
| p5-50 (below median) | 3706 | +0.562% | 53.2% |
| p50-90 (above median) | 3441 | +0.250% | 50.8% |
| p90-95 (overheated) | 438 | +0.666% | 55.0% |
| p95-100 (extreme) | 368 | +1.336% | 54.6% |

### ETH

| bucket | n bars | mean 5-day fwd ret | win % |
|---|---:|---:|---:|
| p0-5 (very negative) | 531 | +0.477% | 56.1% |
| p5-50 (below median) | 3700 | +0.465% | 52.9% |
| p50-90 (above median) | 3433 | +0.314% | 49.2% |
| p90-95 (overheated) | 473 | +0.466% | 52.4% |
| p95-100 (extreme) | 369 | +1.192% | 54.5% |

**Both BTC and ETH show a U-shaped pattern: extreme-negative AND
extreme-positive funding buckets both precede higher-than-average
forward returns.** Mid-range funding is the worst forward bucket for
both. The "blow-off top" hypothesis (extreme positive funding =
mean-reversion sell) is **not supported by this data**. If anything,
extremely overheated funding has been followed by stronger returns
than usual over this 48-month window.

This is consistent with academic literature on perpetuals showing
that funding rate is mostly **coincident with momentum**, not
predictive — and that liquidation-driven extreme negative funding
often marks the bottom of a sell-off rather than the start of one.

## SuperTrend trade outcomes by funding-percentile bucket

If funding doesn't predict price but does predict trade quality (a
weaker claim), we should see SuperTrend trades in extreme-funding
buckets perform differently than those in normal buckets.

In-sample full-window SuperTrend(10, 3) backtest on the 48mo data,
trades tagged by funding-percentile at entry:

### BTC (39 in-sample trades)

| bucket | n | mean net ret | win % | PF |
|---|---:|---:|---:|---:|
| p0-5 (very negative) | 2 | +3.90% | 100% | inf |
| p5-50 (below median) | 19 | +1.40% | 63.2% | 2.99 |
| p50-90 (above median) | 18 | +0.62% | 33.3% | 1.73 |
| p90-95 (overheated) | **0** | — | — | — |
| p95-100 (extreme) | **0** | — | — | — |

### ETH (35 in-sample trades)

| bucket | n | mean net ret | win % | PF |
|---|---:|---:|---:|---:|
| p0-5 (very negative) | 1 | -3.66% | 0% | 0 |
| p5-50 (below median) | 21 | +1.28% | 61.9% | 2.98 |
| p50-90 (above median) | 10 | -0.23% | 50% | 0.72 |
| p90-95 (overheated) | 1 | +2.12% | 100% | inf |
| p95-100 (extreme) | 2 | -1.54% | 0% | 0 |

**The key finding from the bucket analysis is structural, not
statistical: SuperTrend entries almost never occur in extreme-funding
buckets.** BTC has 0 entries in p90+ buckets; ETH has 3 of 35.

Why: SuperTrend(10, 3) is a trend-flip entry. By the time funding
goes extreme (the "everyone is long" phase of a euphoric move), the
SuperTrend has typically been in the trade for many bars already.
Extreme funding marks late-stage trends; SuperTrend enters near the
start of trends. The two signals occupy non-overlapping timing
regions.

**A funding filter on the p95 threshold therefore has nothing to
filter on BTC, and only 2-3 trades to act on for ETH.** The filter
cannot move the headline metrics regardless of how clever the
threshold logic is.

## Funding at drawdown lows

Spec hypothesis: extreme funding might mark stress / drawdown
initiation. Test: measure mean funding rate on bars where the asset
hit a fresh equity drawdown low.

| | BTC | ETH |
|---|---:|---:|
| funding at fresh DD lows (mean) | +0.0063%/8h | +0.0060%/8h |
| funding at fresh DD lows (median) | +0.0061% | +0.0063% |
| overall funding (mean) | +0.0064%/8h | +0.0059%/8h |
| overall funding (median) | +0.0061% | +0.0062% |

**Identical to overall distribution.** Funding at DD lows is
indistinguishable from funding at any random bar. No information.

## Five questions the spec required

### 1. Are extremely positive funding rates bad?
**No, by the data.** Forward 5-day return in the p95-100 bucket is
*higher* than the overall mean for both BTC (+1.34%) and ETH
(+1.19%). And SuperTrend doesn't fire entries in this bucket anyway
(BTC: 0 of 39; ETH: 2 of 35).

### 2. Are extremely negative funding rates good?
**Marginally yes, by the data.** Forward 5-day return in the p0-5
bucket is also above the overall mean for both BTC (+1.21%) and ETH
(+0.48%). Probably capturing post-cascade bottoms. SuperTrend hardly
enters here either (BTC: 2; ETH: 1).

### 3. Is funding predictive?
**No.** Pearson correlation is statistically zero at every tested
horizon. The U-shaped bucket pattern (extremes precede higher
returns) is non-linear but small in magnitude and in the *opposite*
direction the filter hypothesis assumes.

### 4. Is funding coincident?
**Mildly, consistent with literature.** Negative coincident
correlation of -0.013 on BTC, slight positive +0.007 on ETH. Funding
mostly *follows* sentiment (which follows recent price), so the
coincident relationship is weak in the bar-resolution data.

### 5. Is funding useful only in extremes?
**No, on the data we have.** The 0 SuperTrend entries in BTC p95+
buckets means there is nothing to act on. The U-shaped pattern (both
extremes precede higher returns) directly contradicts the filter
hypothesis (block longs when overheated).

## Phase-2 conclusion

The data strongly suggests the funding filter, if applied with the
spec's percentile thresholds (block at p95+, reduce at p90+), will
have **negligible measurable effect on portfolio metrics** because:

1. Funding does not predict returns linearly at any horizon.
2. Where funding *is* informative (extremes), the direction is
   opposite to the filter hypothesis: extreme funding precedes
   *positive* returns, not negative.
3. SuperTrend entries do not coincide with extreme-funding bars in
   the 48mo window — there is essentially nothing to filter.

Per spec we still run Phase 3 (the filter experiment itself) on the
locked configuration to confirm the empirical result. But the prior
from this audit is that the filter will be inert: no significant
change to PF, DD, return, or trade count.

This is itself a useful finding. It tells us:

- Funding rate as a regime signal for SuperTrend(10, 3) on 4h BTC/ETH
  is **not productive** at the locked thresholds.
- A version of this experiment that triggered on the *opposite*
  hypothesis (allow trades only in p0-5 / p95-100 extremes) would be
  trading too few bars to matter.
- The funding-rate data is clean and easy to load; the result here is
  about market structure (SuperTrend entries vs funding timing), not
  data quality.

Next: Phase 3 implements the spec's filter and confirms.
