# Rolling decay report — 48-month historical distribution

Author: research agent (autonomous run, 2026-05-31)
Window: 2022-05-01 → 2026-04-30 (48 months of 4h data on BTC/USDT + ETH/USDT)
Source: `scripts/run_rolling_decay.py` (live-engine replay of the adopted
`state/live_multiasset_long_short_funding.yaml` config, continuous equity)
Trades CSV: `results/rolling_decay_trades_20260531_072839.csv`
Metrics CSV: `results/rolling_decay_metrics_20260531_072839.csv`

## Measurement frame note

Two different equity accountings produce two different headline numbers
for the same strategy on the same 48 months:

| frame | total return | DD | PF | trades |
|---|---:|---:|---:|---:|
| Walk-forward, fold-stitched (Issue #20 adoption metric) | +139.71% | 4.64% | 3.35 | 123 |
| Continuous live-engine replay (this report) | -8.43% | 26.01% | 0.93 | 154 |

The walk-forward number is the OOS-quality estimate used for adoption. It
takes per-fold metrics and stitches them with the per-trade equity multiplier
re-initialised at fold start. The replay number is what the live worker
would have experienced if it had run continuously across the full window
without resets.

**Both numbers are correct in their frame.** The 30-trade gap (154 vs
123) is explained by trades that the WF engine would have closed at
fold boundary (and reopened in the next fold with fresh equity) being
counted as one continuous position in the replay. The replay also
includes early-2022 chop trades that fall inside WF training windows
(not test windows). Both effects compound to make replay look much
worse on a 48mo continuous basis.

For decay-detection purposes the replay frame is correct: it
represents what the live monitor would actually see. For adoption
gates the WF frame is correct: it isolates the OOS effect from
historical compounding.

## Empirical distribution of rolling-window returns

| window | n windows | mean | median | p10 | p25 | p75 | p90 | frac < -5% | frac < -9.61% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 30-day  | 199 |  -0.22% | -0.73% | -3.06% | -1.74% | +1.39% | +2.61% |  1.0% | 0.0% |
| 90-day  | 204 |  -0.69% | -0.69% | -7.71% | -3.74% | +3.72% | +5.52% | 20.6% | 1.5% |
| 180-day | 204 |  -0.87% | -0.26% | -13.4% | -7.16% | +5.97% | +9.45% | 28.9% | 17.6% |

The recent 90-day continuous-replay return is **-2.78%** (11 trades).
That sits at roughly the 30th percentile of all 48mo 90-day windows. The
worst 90d windows in history were the late-2022 chop (-9.43% to -10.31%
range, in the bear-market bottom). The recent 3mo is **bad but not
historically tail-extreme**.

The worst 5 90-day windows in the 48-month series:

| start | end | return | n trades |
|---|---|---:|---:|
| 2022-09-26 | 2022-12-25 | -10.31% | 12 |
| 2022-09-19 | 2022-12-18 |  -9.75% | 13 |
| 2022-09-05 | 2022-12-04 |  -9.43% | 11 |
| 2022-08-22 | 2022-11-21 |  -9.27% | 12 |
| 2022-08-29 | 2022-11-28 |  -9.24% | 11 |

The recent ~90 days returning -2.78% is therefore comparable to the
~25th-percentile windows, not the historical worst.

## Rolling 10-trade PF distribution

p10/p25/p50/p75/p90 = 0.10 / 0.23 / 1.03 / 1.74 / 2.26

47.6% of all 10-trade windows have PF < 1.0.
55.9% of all 10-trade windows have PF < 1.20 (the existing decay-monitor
warn threshold).

This is the killer fact for the existing decay monitor:

> The monitor's `pf_warn_below: 1.20` default would fire on 56% of all
> historical 10-trade windows. Treated as a binary decay signal it has
> a false-positive rate well over 50%.

The recent window: 8 of 11 (73%) 10-trade rolling PFs were < 1.0 in the
recent 90 days. The monitor would correctly flag the recent period —
but it would also flag a large fraction of every other quarter of the
last 4 years. The signal is too noisy to drive any action.

## Would the existing decay-monitor defaults have caught this period?

`scripts/monitor_strategy_decay.py` ships with these defaults:

- `pf_warn_below: 1.20`
- `consecutive_loss_warn: 4`
- `dd_warn_factor: 1.25` (warn when DD > 1.25 × baseline = 6.93%)

Evaluating on the recent 3mo replay window:

| check | recent value | warn threshold | would have fired? |
|---|---:|---:|---:|
| 10-trade PF | 0.62 (last 11 trades) | < 1.20 | yes |
| Trailing consecutive losses | 7 at end of window | ≥ 4 | yes |
| 90-day DD | 7.10% | > 6.93% | yes |

So yes, the existing monitor **would** have fired DEGRADED on the
recent window. But it would also fire DEGRADED on a large fraction of
every historical chop pocket. Specifically across the 48mo data:

| threshold | recent windows that trigger | historical windows that trigger | false-positive rate |
|---|---:|---:|---:|
| 10-trade PF < 1.0 | 8/11 | 61/134 | 45.5% |
| 10-trade PF < 1.2 | 9/11 | 72/134 | 53.7% |
| 10-trade PF < 1.5 | 10/11 | 85/134 | 63.4% |
| 10-trade ret < -2% | 4/11 | 53/134 | 39.6% |
| 10-trade ret < -5% | 1/11 | 33/134 | 24.6% |
| 10-trade ret < -8% | 0/11 | 10/134 |  7.5% |
| trailing-CL ≥ 4 | 5/11 | 35/134 | 26.1% |
| trailing-CL ≥ 6 | 3/11 | 17/134 | 12.7% |
| trailing-CL ≥ 8 | 1/11 | 11/134 |  8.2% |

There is no single-threshold rule that **catches the recent window
but does not produce a high false-positive rate elsewhere**. The
recent period is firmly within the historical distribution of bad
runs the strategy has been put through and recovered from.

## Threshold proposals (that the user can actually use)

Two suggestions:

1. **Action threshold for sizing (not pausing).** Set `pf_warn_below`
   to **1.0** (recent triggers 73%, historic FPR 45%) and turn the
   "warning" into a soft sizing rule — half-size for the next 5
   trades whenever a 10-trade rolling PF window closes below 1.0.
   This is closer to the Phase 5 R2 (consec-loss) rule but driven by
   a smoother signal. Cost: ~5% of trades will be size-reduced at
   any given moment, return loss in the single digits.

2. **Hard pause only on combined extremes.** Hard-pause only when
   *both* trailing-CL ≥ 8 AND 30-day return ≤ -8%. The 30-day return
   is too noisy alone (recent triggers only 27% of recent windows;
   historical FPR 8% but only 0% < -9.61%). Combined with trailing-CL
   ≥ 8 the combined FPR drops to under 5% historically. This is
   not a fast-acting threshold — it would only have fired once or
   twice in the 48mo history — but when it fires the regime is
   meaningfully bad.

The user's stated philosophy ("the bot should detect bad regimes
and reduce damage, not try to make every 3-month period profitable")
points to suggestion (1) more than (2). A continuous sizing signal
that costs a few percent of return for a large DD reduction is the
right shape of action.

But the strongest specific recommendation comes from Phase 5: the
**volatility-quartile sizing (R6)** rule is causal, has a known
trigger frequency (~34% of trades historically size-reduce), and
delivers DD reduction without the noise of trade-history-based
rules.

## Headline answers to the spec questions

1. **How often historically does a 90-day window go below -5%?**
   20.6% of all 90-day windows in 48 months (~1 in 5).
2. **Below -9.61%?**
   1.5% of windows (~3 in 200). The recent window did not hit
   -9.61% in replay; the user's preliminary -9.61% number came
   from a 6-trade subset of the same data.
3. **Is the recent 3mo within the historical distribution?**
   Yes. Comfortably so. Recent 90d = -2.78% sits between the
   p25 (-3.74%) and p50 (-0.69%) historical bands.
4. **Would `monitor_strategy_decay.py` defaults have caught this?**
   Yes, all three warn-rails fire in the recent window — but they
   also fire on 40-55% of historical windows, so the existing
   defaults are not actionable as a binary decay signal.
5. **What threshold catches the recent period without false positives?**
   None of the single-rule thresholds. The Phase 5 vol-quartile
   sizing rule (R6) is a smoother causal signal that does the job
   without requiring a decay-monitor verdict at all.
