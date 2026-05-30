# Phase 3 — Edge Diagnostics

Quantitative breakdown of the 57 trades in the 24-month BTC/USDT 4h backtest
(no Markov gating; fees 10 bps/side, slippage 5 bps).

CSV source: `results/trades_detailed_20260530_082437.csv`.

**Headline (full window)**: 57 trades, +0.42% total return, max DD 10.48%,
win rate 26.3%, PF 1.04, exposure 11.7%, avg win/loss +2.05%/-0.70%.
Net of fees, on the full history (not OOS) this is essentially break-even —
which is what made the walk-forward stitched OOS (+8.98% in earlier sweeps)
both interesting *and* suspicious.

---

## Performance by side × setup

| side × setup | n | avg net | win% | avg win | avg loss | PF | compounded |
|---|---:|---:|---:|---:|---:|---:|---:|
| long × breakout | 25 | -0.062% | 28.0% | +1.14% | -0.53% | **0.84** | -1.66% |
| long × pullback | 9 | -0.439% | 22.2% | +1.26% | -0.93% | **0.39** | -3.92% |
| short × breakout_short | 17 | +0.062% | 17.6% | +3.89% | -0.76% | 1.10 | +0.55% |
| short × pullback_short | 6 | +0.949% | 50.0% | +2.83% | -0.93% | **3.04** | +5.71% |

**Reading:**
- Long pullback is the worst — PF 0.39. The "buy RSI<32 oversold in an uptrend"
  thesis loses on 4h BTC.
- Long breakout is essentially break-even after fees (PF 0.84).
- Short pullback (small sample, 6 trades) is the unexpected winner — PF 3.04,
  50% win rate. Selling overbought into a confirmed downtrend works.
- Short breakout is marginal (PF 1.10) — a few big winners carry it.

## Performance by Markov state

| state | n | avg net | win% | PF | compounded |
|---|---:|---:|---:|---:|---:|
| up_low_vol | 17 | +0.53% | 29.4% | **2.16** | +8.87% |
| up_high_vol | 16 | +0.27% | 37.5% | 1.65 | +4.26% |
| down_high_vol | 12 | -0.50% | 25.0% | **0.20** | -5.83% |
| down_low_vol | 12 | -0.52% | 8.3% | **0.17** | -6.04% |

This is the cleanest signal in the dataset:
**up-regime states are strongly profitable; down-regime states are equally
strongly *un*profitable.** Skipping all down-state trades flips the strategy
from break-even to clearly positive — which is exactly what `strategy_routing`
did in the Markov sweep (+9.23% / PF 2.17 OOS).

## Performance by RSI bucket at entry

| RSI | n | avg net | win% | PF | compounded |
|---|---:|---:|---:|---:|---:|
| <30 (deep oversold — long pullback territory) | 6 | -0.96% | **0.0%** | **0.00** | -5.64% |
| 30–40 | 10 | -0.13% | 30.0% | 0.78 | -1.38% |
| 40–50 | 13 | +0.19% | 15.4% | 1.34 | +2.04% |
| 50–60 | 7 | -0.20% | 42.9% | 0.32 | -1.38% |
| 60–70 | 12 | -0.09% | 25.0% | 0.79 | -1.12% |
| >70 (overbought — short pullback territory) | 9 | **+0.92%** | 44.4% | **3.44** | +8.45% |

**Striking asymmetry:** entries at RSI<30 went **0/6 wins**. Entries at RSI>70
won 44%. The classic "RSI mean-reversion long in BTC uptrend" thesis is
**measurably negative**; the mirror thesis (short into RSI>70 in a downtrend)
*does* work in this sample.

## Performance by ATR% bucket (volatility regime)

| ATR% quartile | n | avg net | PF | compounded |
|---|---:|---:|---:|---:|
| low_vol | 15 | -0.12% | 0.73 | -1.83% |
| med_low | 14 | +0.65% | **2.84** | +9.07% |
| med_high | 14 | -0.59% | **0.12** | -7.99% |
| high_vol | 14 | +0.15% | 1.25 | +1.94% |

The "med-low vol" quartile is the sweet spot. The "med-high" quartile is
toxic — likely catches the noisy chop between calm trending and full
crisis-mode high-vol.

## Performance by EMA50 slope quartile (trend strength)

| EMA50 slope | n | avg net | PF | compounded |
|---|---:|---:|---:|---:|
| strong_down | 15 | -0.51% | 0.31 | -7.41% |
| mild_down | 14 | +0.58% | **2.20** | +7.88% |
| mild_up | 14 | -0.04% | 0.90 | -0.67% |
| strong_up | 14 | +0.10% | 1.23 | +1.22% |

"Strong down" trend is where the strategy bleeds — even though those bars
are theoretically blocked from longs by `_bullish_regime`, the bearish-regime
shorts that fire instead are losing.

## Performance by VWAP distance quartile

| VWAP distance | n | avg net | win% | PF | compounded |
|---|---:|---:|---:|---:|---:|
| far_below | 15 | -0.84% | **0.0%** | **0.00** | **-11.94%** |
| near_below | 14 | +0.72% | 42.9% | **2.93** | +10.08% |
| near_above | 14 | +0.23% | 28.6% | 1.73 | +3.13% |
| far_above | 14 | +0.04% | 35.7% | 1.09 | +0.45% |

**The single strongest filter in the dataset.** Trades entered with price
*far below* VWAP go **0 for 15**, dropping -11.94% compounded. The 30%
"keep me out of the trash" filter you'd want is: don't enter when
`vwap_distance_pct` is in the bottom quartile (~< -1.5%). The strategy
currently has a binary `require_above_vwap` toggle for breakouts but does
not have a "how far below VWAP is too far" rule for any setup.

## Performance by holding-bars bucket (the biggest single finding)

| holding bars | n | avg net | win% | PF | compounded |
|---|---:|---:|---:|---:|---:|
| 1 bar | 13 | -0.44% | 15.4% | 0.03 | -5.57% |
| 2–3 bars | 12 | -0.85% | **0.0%** | **0.00** | -9.73% |
| 4–7 bars | 11 | -0.29% | 18.2% | 0.57 | -3.27% |
| 8–15 bars | 11 | -0.09% | 27.3% | 0.78 | -1.02% |
| **16+ bars** | **10** | **+2.13%** | **80.0%** | **15.84** | **+23.06%** |

This is the most actionable finding in the report. **Almost all of the
strategy's PnL comes from the 10 trades that survive past ~16 bars (~2.5
days at 4h).** The 47 trades that exit in <15 bars are collectively a
~20% drawdown.

The strategy is being **whipsawed out of winners** by tight stops and the
21-EMA trail. The trades that escape that get to ride trends and produce
80% win rate.

## Performance by exit reason

| exit reason | n | avg net | win% | PF |
|---|---:|---:|---:|---:|
| stop | 43 | -0.18% | 18.6% | 0.72 |
| target_rsi | 5 | +2.20% | **100%** | inf |
| trail_exit | 9 | -0.25% | 22.2% | **0.08** |

- `target_rsi` (RSI 55 for longs / RSI 45 for shorts) is the **only**
  profitable exit reason and wins every time (5/5). Tiny sample but
  consistent.
- `trail_exit` (21-EMA trailing stop on breakouts) is **worse than the
  static stop** — PF 0.08. The trail kicks the position out at a small
  net loss after the position has already moved against it.
- `stop` is the dominant exit — 75% of trades. PF 0.72: small frequent
  losses.
- `regime_flip` and `time_stop` never fire in this sample.

## Performance by day of week (noisy but suggestive)

| day | n | avg net | PF |
|---|---:|---:|---:|
| Thursday | 5 | +2.09% | 6.10 |
| Monday | 8 | +0.12% | 1.22 |
| Saturday | 7 | +0.05% | 1.18 |
| Sunday | 7 | -0.04% | 0.93 |
| Friday | 11 | -0.36% | 0.30 |
| Tuesday | 10 | -0.32% | 0.47 |
| Wednesday | 9 | -0.34% | 0.40 |

Thursday is a tiny-sample outlier. The mid-week (Tue–Wed–Fri) entries
underperform — too few samples to be meaningful.

## Hour-of-day not analyzed

4h bars at UTC midnight aligned mean entries land on {00, 04, 08, 12, 16, 20}
UTC only — 6 buckets with 9–11 trades each. Not useful for hour-of-day
analysis at this timeframe.

---

## Direct answers to the Phase-3 questions

### Is RSI mean reversion useful at all?

- **Long pullback (RSI<32, buy oversold dip): NO.** PF 0.39 over 9 trades.
  Entries at RSI<30 went 0 for 6. The bias against this on 4h BTC is real.
- **Short pullback (RSI>68, sell overbought rip): YES but tiny sample.** PF
  3.03 on 6 trades. Cannot be ruled out as variance; cannot be confidently
  declared an edge either. Worth keeping and watching.

### Is 3-bar-play useful at all?

- **Long breakout**: marginal at best — PF 0.84 over 25 trades. Roughly
  break-even after costs in this sample.
- **Short breakout**: marginally positive — PF 1.10 over 17 trades. Carried
  by a few big winners; median trade is a loss.
- Combined PF 0.94 — the 3-bar play, as wired, is not an edge by itself.

### Is EMA/VWAP trend continuation useful at all?

- **VWAP positioning is the strongest filter in the data.** Trades entered
  far below VWAP have **zero wins** out of 15. A VWAP-distance gate (don't
  enter if `vwap_distance_pct < -X`) would have prevented the biggest single
  drag on the strategy.
- EMA slope is mildly useful: strong_down quartile is a loser; mild_down is
  the most profitable bucket (counter-intuitive — driven by short setups
  firing in mild-down regimes).

### Are we exiting too early?

**Yes — definitively.** Holding-bucket analysis shows the 16+ bar trades
print +2.13% per trade at 80% win rate (PF 15.84). Trades exiting in 1–3
bars collectively lose ~15%. The current exits are kicking the strategy
out of its winners.

### Are stops too tight?

**Probably yes.** 75% of trades exit via stop. ATR multipliers of 1.2–1.5
at 4h ATR ~1% of price → stops ~1.2–1.8% wide. Many trades are stopped on
normal 4h chop and then the trend resumes without us. Widening stops to
2–3× ATR is a candidate experiment.

### Are we trading too often?

Trade count (57 / 24mo ≈ 2.4/mo) is not high in absolute terms, but
*quality* is poor — the median trade is a loss in every bucket except
"holding 16+ bars" and "RSI > 70". Tighter selection (state filter, VWAP
filter) would cut count by ~half while improving expectancy.

### Are we missing better trend-following entries?

**Yes.** The strategy's profit lives in the "survives 16+ bars" bucket —
i.e., in trades that successfully ride a trend. But the entries (RSI
oversold, 3-bar play) and exits (21-EMA trail) are designed for mean
reversion, not trend continuation. A direct trend-following entry
(EMA50/EMA200 cross, Donchian breakout, etc.) is unexplored.

### Is long-only better than long/short?

In this 24-mo sample:
- Long-only PnL ≈ -0.06% × 25 + -0.44% × 9 ≈ −5.6% compounded
- Short-only PnL ≈ +0.06% × 17 + +0.95% × 6 ≈ +6.0% compounded

Shorts are *slightly* additive in aggregate, with the win concentrated in
short_pullback (small sample). Long-only is not obviously better; the
short_pullback edge is the most interesting hypothesis to keep.

### Is BTC alone too limited?

57 trades in 24 months means single-period or single-regime variance
dominates. BTC alone gives roughly two cycles (post-LUNA → pre-rally,
the 2024–2026 window). Cross-asset (ETH, SOL) on the same engine would
multiply the sample without adding new code — the data loader already
supports any Binance Vision symbol. This is a small but high-value
experiment.

---

## Top three concrete edges visible in the data

1. **Block all entries in down_low_vol / down_high_vol Markov states.** Per-state
   PF is 0.17 / 0.20 — straight losses. Replicates Phase-2 `strategy_routing`
   finding with explicit per-state evidence.
2. **Block entries when `vwap_distance_pct < -1.5%`.** 0/15 win rate in the
   far_below bucket; would have prevented the worst stretch.
3. **Don't trail-exit on 21-EMA for breakouts.** PF 0.08 — worse than just
   holding to target. Either widen the trail (e.g., 21 EMA - 1×ATR) or
   replace it with a momentum-based exit (RSI crossing back through 50,
   close vs ema_fast, etc.).

These three filters/exits, in combination, are the simplest changes most
likely to lift the OOS profit factor — and each is testable in walk-forward.
