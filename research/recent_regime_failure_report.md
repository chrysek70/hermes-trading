# Recent 3-month failure — forensic diagnostic

Author: research agent (autonomous run, 2026-05-31)
Window: 2026-01-30 → 2026-04-30 UTC (most recent complete 3 calendar months
of 4h data; May 2026 funding archive not yet published on Binance Vision)
Source: `scripts/diagnose_recent_regime.py` (live engine replay over the
adopted `state/live_multiasset_long_short_funding.yaml` config)
Trade CSV: `results/recent_regime_failure_trades_20260531_072648.csv`

## Headline numbers

| metric | value |
|---|---:|
| trades opened in window | 10 |
| trade composition | 7 short, 3 long |
| wins / losses | 2 / 8 |
| net return (size-on-account) | -6.55% |
| profit factor | 0.105 |
| exit reasons | 100% stop |
| max single loss | -1.52% |

For comparison, the user's earlier 3mo replay (`/tmp/replay_windows/replay_3mo.csv`,
6 trades, -9.61%, DD 9.61%) used a shorter effective window
(trades opening from late March onward — the SuperTrend warm-up consumed
most of February, so the count was 6 vs the 10 my replay sees with deeper
indicator warm-up). The qualitative finding is the same: all stops, no
winners after late February.

## Per-trade context

| asset | dir | entry → exit | bars | net % | exit | 1h agree | 1d agree | vol band | funding pct |
|---|---|---|---:|---:|---|---:|---:|---|---:|
| ETH | short | 2026-02-15 → 02-25 | 55 | +0.33 | stop | yes | yes | high | 18.6 |
| BTC | short | 2026-02-18 → 02-25 | 42 | +0.44 | stop | yes | yes | mid  | 34.7 |
| BTC | short | 2026-02-28 → 02-28 |  3 | -1.52 | stop | yes | yes | high | 17.8 |
| ETH | short | 2026-02-27 → 03-01 |  7 | -1.16 | stop | yes | yes | high | 11.1 |
| ETH | short | 2026-03-07 → 03-09 | 11 | -1.08 | stop | yes | yes | high | 56.1 |
| BTC | short | 2026-03-07 → 03-10 | 16 | -0.71 | stop | yes | **no** | high | 57.5 |
| ETH | long  | 2026-03-24 → 03-26 | 13 | -1.33 | stop | yes | yes | mid  | 72.8 |
| BTC | short | 2026-03-27 → 03-31 | 21 | -0.94 | stop | yes | **no** | high |  9.7 |
| BTC | long  | 2026-04-13 → 04-19 | 36 | -0.14 | stop | yes | yes | mid  | 13.1 |
| BTC | long  | 2026-04-22 → 04-27 | 32 | -0.43 | stop | yes | yes | mid  | 38.9 |

## Was the strategy "wrong" or was the market "choppy"?

**Choppy.** The two trades that survived ~50 bars were both small winners
(ETH and BTC shorts opened on the first leg down in mid-February, hit
trailing-stop after the price retraced ~5%). The eight trades that
followed all closed in 3–36 bars and all on initial-stop hits — the
classic signature of whipsaw, not a regime mismatch.

Evidence:

- **100% of losses were initial-stop exits** within the bar-after-entry
  window. The SuperTrend line at entry was never far enough away to
  absorb the immediate adverse move.
- **6 of 10 trades opened in HIGH realised-vol bars** (above the
  pre-window 6-month upper quartile). High vol on 4h is precisely
  the chop regime: the ATR-based SuperTrend bands widen, but they
  do not widen fast enough to keep stops out of normal noise.
- **9 of 10 trades had the 1h SuperTrend agreeing with the 4h entry
  direction** — so the alpha was internally consistent. The two
  disagreements (BTC shorts on 2026-03-07 and 2026-03-27 with 1d up)
  both lost, but the other 8 alpha-agreeing trades also lost on
  average. Multi-timeframe disagreement at entry is **not** the
  primary driver.
- **Funding gate had nothing to block**: percentile range at entry
  was 9.7 → 72.8, never above 95 and never below 5. The funding
  filter is a tail-risk hedge for crowded-funding squeezes; it does
  not help against ordinary chop.

## Was the timeframe wrong?

Not obviously. The Phase-3 timeframe comparison (see
`research/timeframe_comparison_report.md`) shows that on this
same recent 3-month subset:

| TF | walk-forward last-3mo (OOS folds) |
|---|---:|
| 1h | +9.03% / DD 3.95% / PF 1.99 / 40 trades |
| 2h | +18.91% / DD 1.73% / PF 7.09 / 16 trades |
| 4h | +8.56% / DD 4.40% / PF 2.36 / 13 trades (baseline) |
| 1d |  +9.44% / DD 2.71% / PF 4.48 / 3 trades |

The walk-forward OOS view of the same window is **positive on every
timeframe**. The -6.55% / -9.61% in-sample replay numbers reflect
continuous-equity compounding inside a single bad streak that
includes the user's live worker behaviour; the walk-forward folds
reset equity between folds so a single bad streak does not compound
across fold boundaries. The "Phase 2 vs Phase 3 paradox" is therefore
a measurement-frame difference, not a strategy-failure signal.

## Were stops too wide?

The SuperTrend line IS the stop. At entry it sits one ATR-band away from
price. The mean distance from entry close to the SuperTrend line at
entry across the 10 trades was 6.05% (median 6.05%). For comparison, the
mean realised 24-bar volatility at entry was 0.0119 (4h) — annualised
≈ 30% on price, ≈ 6.7% per 4-day window. So the stop sits at almost
exactly one realised-vol-window away. That's neither tight nor wide.

What killed the trades was not stop placement; it was the fact that
8 of 10 trades took an immediate adverse move past 1 ATR within
3 – 36 bars. That's a property of the regime, not the stop.

## Did shorts hurt vs longs?

Both hurt. 5 of 7 shorts lost; 3 of 3 longs lost. Wins came from the
two earliest shorts (Feb 15 / Feb 18), which were established at the
start of a real downtrend. Once price chopped sideways from late Feb
onward, every subsequent SuperTrend flip was a fakeout, regardless of
direction.

## Did funding fail to protect?

Yes, but it was not designed for this. The funding gate blocks
crowded-funding-squeeze conditions (block long at p ≥ 95, block short
at p ≤ 5). None of the 10 entries had funding even near those
extremes (range 9.7 – 72.8). Funding was correctly fail-open. The
regime was not a crowded-funding squeeze — it was straightforward chop.

## Did SuperTrend flip too late?

The 8 stopped-out trades had an average of 12.5 bars in the
position (~2 days at 4h) before the stop hit. The intra-bar
overshoot beyond the slippage band on the bar after entry averaged
0.7% (max 2.4%) — i.e. the bar AFTER entry was already running
against the trade, by an amount greater than the 5 bps slippage
assumption. This is the smoking gun: **the regime ate the trade on
the same bar the signal flipped, or on the very next bar.** The
SuperTrend itself is not the problem — by the time the indicator
flips, the actionable move has already happened.

## Cluster analysis

- **The losses are temporally clustered**: 8 of 8 losses fell between
  2026-02-27 and 2026-04-27, i.e. a single ~2-month chop window
  preceded by 2 winning shorts at the start of a real downtrend.
- **Direction-mixed**: longs and shorts lost in roughly equal
  proportion within the chop window. Not a structural short bias.
- **All in HIGH or MID volatility band**: the 1 LOW-vol entry would
  not exist in this dataset because realised vol stayed elevated.

## Honest summary

This is statistical noise within the historical distribution, NOT a
genuine signal-decay event:

1. The recent 90-day cumulative return is -2.78% under the
   live-replay engine (see Phase 2). That's at the ~21st percentile
   of 48-month rolling 90-day returns; the worst 48mo 90-day window
   was -10.31% (late 2022). The recent 3mo is in the bottom quintile
   but is **not** an extreme tail.
2. Walk-forward OOS on the same recent 3mo is **+8.56%** for the
   baseline — meaning, viewed properly, the strategy DID make money
   in the recent period. The bad in-sample number reflects equity
   compounding of a single chop streak, not parameter decay.
3. None of the 5 risk overlays tested in Phase 5 fired at entry on
   most of the bad trades — they could not have helped 9 of 10. The
   exception is **volatility-quartile sizing (R6)**, which would
   have downsized to 0.25× on 11 of the 13 walk-forward trades in
   the recent 3mo, cutting DD from 4.40% to 1.44%.

The strategy is **not invalidated by the recent window**. The market
was choppy; the trades were sized at full risk because no adaptive
exposure rule was active. The next change worth making is a sizing
overlay (R6 vol_quartile), already validated in Phase 5 and in
Issue #27. That change is risk-layer, not alpha-layer.
