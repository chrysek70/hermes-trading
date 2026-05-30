# Phase 4 — Signal Improvement Plan

Practical strategy upgrades for BTC 4h derived from the Phase-3 edge
diagnostics. Each proposal is research-only — implementation is gated on
walk-forward validation in a future phase. The current strategy's biggest
problems (per Phase 3) are: (a) tight stops cause premature exits, (b)
21-EMA trail kills breakout winners, (c) entries far below VWAP go 0/15,
(d) long-side mean reversion has no measurable edge. The proposals below
target these.

For every candidate: **hypothesis · entry rule · exit rule · stop · required
data · expected weakness · overfit risk · walk-forward plan**.

---

## 1. Trend-following breakout (Donchian-style)

**Hypothesis.** The 16+-bar holds in Phase 3 made all the strategy's money;
those were latent trend trades. A direct trend-following entry removes the
RSI-oversold contradiction that currently confuses the long-side logic.

**Entry.** Long when `close > max(high, period=20)` of prior 20 bars
(Donchian-20 breakout); short when `close < min(low, period=20)` of prior
20 bars. Optional confirm: EMA50 slope sign matches direction.

**Exit.** Trail under `max(close[i-10..i-1]) - 2×ATR` (chandelier-style) for
longs / mirror for shorts. Exit on opposite-side Donchian-10 break.

**Stop.** Initial stop = entry − 2×ATR(14) for longs (looser than current 1.5).

**Indicators.** Donchian high/low (rolling max/min of high/low), ATR(14).
Already computable from OHLCV; no new feed.

**Expected weakness.** Donchian breakouts whipsaw in choppy markets
(low-vol sideways regimes). The Markov state filter from Phase 5 should
gate this — only run it in `up_*` / favourable states.

**Overfit risk.** Low — Donchian-20 is a single hyperparameter with well-
established literature. Don't sweep it on test.

**Walk-forward plan.** Add as `setups.donchian_breakout` in a v3 strategy
yaml; walk-forward 24mo 4h vs baseline + with Markov routing. Single
fold-forward run, no per-fold tuning.

---

## 2. Pullback in confirmed uptrend (anti-RSI)

**Hypothesis.** The current "RSI<32 in uptrend" pullback loses (PF 0.39).
The data suggests the *direction* is correct but the *trigger* is wrong —
oversold extremes in BTC 4h often presage continuation, not reversion. A
shallower pullback to the rising 50-EMA may work where the 21-EMA-touch
+ RSI<32 does not.

**Entry.** Long when (a) `ema_fast > ema_slow` (uptrend confirmed), AND
(b) `close` touches `ema_fast - 0.5×ATR` from above and closes back above
`ema_fast`, AND (c) prior bar was below `ema_fast`.

**Exit.** Target = entry + 2R (R = entry − stop). Trail under prior swing
low after target hit.

**Stop.** Initial stop = entry − 1.5×ATR.

**Indicators.** EMA50, ATR(14). Already computed.

**Expected weakness.** Loose entry rule — many touches of the 50-EMA in a
sideways market will fire false signals.

**Overfit risk.** Medium. The 0.5×ATR "near-touch" threshold is a
sensitivity dial.

**Walk-forward plan.** Walk-forward this in parallel with the current
pullback to measure relative PF.

---

## 3. Volatility-compression breakout (NR4 / Bollinger squeeze)

**Hypothesis.** The Phase-3 ATR-bucket analysis showed `med_high` vol
quartile is toxic (PF 0.12) — entries in "noisy chop" lose. Entries that
fire only after a low-vol compression are higher-quality breakouts.

**Entry.** Long when (a) `atr(14)` is in the bottom quartile of its
rolling 100-bar distribution (compression), AND (b) `close` breaks above
`max(high, 20)`. Short symmetric.

**Exit.** Trail under `max(close[i-5..i-1]) - 1×ATR`. Exit on
regime-flip.

**Stop.** Initial stop = entry − 1.5×ATR.

**Indicators.** ATR(14), Donchian(20), ATR percentile (rolling). New
computation: rolling rank of ATR.

**Expected weakness.** Requires patience; compression setups are rare.
Trade count will be low.

**Overfit risk.** Low-medium — compression threshold (quartile cutoff) is
the main free parameter; rolling-rank approach makes it self-adaptive.

**Walk-forward plan.** Same as #1; compare expectancy and exposure.

---

## 4. Channel (Keltner / Donchian) breakout with ATR-trailing stop

**Hypothesis.** Generalisation of #3 using two channels (mid-band trail,
outer-band breakout entry).

**Entry.** Long when `close > ema_fast + 1.5×ATR` (Keltner upper).
Short symmetric.

**Exit.** Trail under `ema_fast - 0.5×ATR` (mid-band). Hard stop at
`entry - 2.5×ATR`.

**Indicators.** EMA50, ATR(14). Already computed.

**Expected weakness.** Late entries — by the time we cross the upper band,
much of the move is done. Helps risk/reward (looser stop) but hurts
hit rate.

**Overfit risk.** Low. Two multipliers (entry 1.5×, mid 0.5×) are the
only knobs.

**Walk-forward plan.** Walk-forward at 24mo 4h, compare R:R distribution
vs current breakout.

---

## 5. SuperTrend / ATR-trend system

**Hypothesis.** Replace the EMA50/200 regime gate + 3-bar play entry with
a single SuperTrend(10,3) signal — entry on flip, exit on opposite flip.

**Entry.** Long when SuperTrend flips from below to above price. Short
on opposite flip.

**Exit.** SuperTrend flip in opposite direction (this *is* the trail).

**Stop.** SuperTrend line is the implicit stop.

**Indicators.** SuperTrend = `mid_band ± multiplier × ATR`, where
`mid_band = (high + low) / 2`. Standard formula.

**Expected weakness.** Whipsaws in chop. Phase-3 already showed `med_high`
ATR quartile is toxic — a SuperTrend system would amplify that.

**Overfit risk.** Low. Two free parameters (period 10, multiplier 3) are
TA-conventional defaults.

**Walk-forward plan.** Walk-forward as a standalone strategy; compare to
the current v2-long-short. If it has *any* edge alone, combine with the
Markov state filter.

---

## 6. BTC/ETH relative-strength filter

**Hypothesis.** When ETH is outperforming BTC on a rolling basis, crypto
flow is "risk-on" — BTC longs are more likely to follow through. When
BTC is outperforming ETH, money is rotating to safety — BTC alone may
top.

**Entry.** Apply the existing setups but only when
`pct_change(ETH/USDT, 20) - pct_change(BTC/USDT, 20)` agrees with the
direction (positive for longs, negative for shorts).

**Exit.** Unchanged.

**Stop.** Unchanged.

**Indicators / data.** ETH/USDT 4h data alongside BTC. `data.load_klines`
already supports any Binance Vision symbol — minimal new code.

**Expected weakness.** Adds a continuous degree of freedom (the
20-bar lookback for relative strength). Sample size of 57 BTC trades is
already small; halving by an RS filter could push us below
statistical-power thresholds.

**Overfit risk.** Medium. The RS lookback period is a tuning knob.

**Walk-forward plan.** Walk-forward 24mo on both BTC and ETH. Compare to
BTC-only. Use only the OOS BTC trades that pass the RS filter; don't
peek at OOS ETH trades to derive the filter threshold.

---

## 7. Higher-timeframe 200-EMA risk filter

**Hypothesis.** Phase-3 down-state PF (0.17–0.20) is partly explained by
"big-picture bear" — when price is below the 1d 200-EMA, even 4h
"up_low_vol" classifications turn out to be bear-market dead-cat-bounces.

**Entry.** No change to the trigger conditions; just add a *gate*:
allow longs only when `close_1d > ema(close_1d, 200)`. Allow shorts only
when `close_1d < ema(close_1d, 200)`.

**Exit / Stop.** Unchanged.

**Indicators / data.** 1d resample of the same BTC stream + 200-day EMA.
`data.resample` already supports this.

**Expected weakness.** EMA200-on-1d only flips a few times per year. In
a 24-mo sample, we may have 1–2 regime changes — easy to fit one period
and miss the next.

**Overfit risk.** Low (one binary filter, no thresholds).

**Walk-forward plan.** Walk-forward as a gate on top of the existing
strategy; report change in trade count, PF, DD.

---

## 8. Funding-rate stress filter (if data source available)

**Hypothesis.** Negative perpetuals funding = paid-to-go-long = shorts
are paying longs; sustained negative funding precedes upside squeezes.
Positive funding overheated = long crowd is paying; precedes downside.

**Entry.** Block long entries when funding > 0.05% (8h-equivalent).
Block short entries when funding < -0.05%.

**Exit / Stop.** Unchanged.

**Indicators / data.** Funding-rate series for BTC perpetuals — Binance,
Bybit, or OKX. Need to add a new data adapter (`data.py`'s only adapter
today is spot OHLCV). Realistic implementation effort: ~30 LOC.

**Expected weakness.** Funding-rate data may be lagged or noisy across
exchanges; choice of source matters. Free-tier APIs may rate-limit.

**Overfit risk.** Medium — the 0.05% threshold is a free parameter.

**Walk-forward plan.** Build the funding-rate loader, walk-forward
24mo BTC 4h with the filter, compare to baseline.

---

## 9. Volume / volatility confirmation

**Hypothesis.** Volume z-score at entry isn't currently a filter. Trades
on weak volume (z-score < 0) may not have institutional flow behind them
and reverse.

**Entry.** Existing entries gated by `volume_zscore > 0.5` (above-average
volume).

**Exit.** Unchanged.

**Stop.** Unchanged.

**Indicators.** Rolling volume z-score, window 20. Already computed
in `_attach_diagnostics`.

**Expected weakness.** Reduces trade count. Volume z-score on 4h crypto
spot is noisy.

**Overfit risk.** Low (single threshold, set on z-score scale).

**Walk-forward plan.** Single fold-forward run with z-score gate;
compare PF and exposure.

---

## 10. Time-based stop and profit-taking improvements

**Hypothesis.** Phase 3's holding-bars analysis showed that trades in the
1–3 bar bucket lose ~10% compounded, while 16+ bar trades make 23%. The
strategy is doing the opposite of what works: cutting winners early via
the 21-EMA trail and holding losers (or being stopped) early.

**Proposal A — Tighter time stop on early losers:** if a long trade is
still under-water at `bar+8`, exit. (Cuts the 1–3 bar stop-out tail
without giving up winners that take time to develop.)

**Proposal B — Break-even-plus stop after R reached:** when unrealised
PnL ≥ 1R (= initial stop distance), move stop to entry + 0.25R.
Lets the trade run while removing tail-loss risk.

**Proposal C — Replace trail with target laddering:** Take 50% off at
1R, 25% at 2R, trail the remainder under `ema_fast - 1×ATR`.

**Indicators / data.** None new.

**Expected weakness.** Multiple TP rules add hidden degrees of freedom
even when individual rules are simple.

**Overfit risk.** Medium for laddering; low for break-even-plus.

**Walk-forward plan.** Walk-forward each variant separately, compare
total return × max-DD × PF axes.

---

## Combined ranking — what to build first

Given Phase-3 evidence-strength + implementation cost:

| Rank | Proposal | Expected impact | Cost |
|---|---|---|---|
| 1 | **#7 1d 200-EMA risk filter** — simplest, low overfit, strong prior | High | Tiny |
| 2 | **#10B break-even-plus stop** — directly addresses "winners turned to losers" | High | Small |
| 3 | **#1 Donchian breakout** — directly trend-following, the bucket that pays | High | Small |
| 4 | **#6 BTC/ETH RS filter** — diversifies single-period risk | Medium | Small |
| 5 | **#3 Vol-compression breakout** — addresses `med_high` PF 0.12 | Medium | Medium |
| 6 | **#5 SuperTrend** — clean trend-follow framework | Medium | Medium |
| 7 | **#2 50-EMA pullback** — replaces the broken RSI<32 pullback | Medium | Small |
| 8 | **#9 Volume z-score filter** — quick to test | Low–Medium | Tiny |
| 9 | **#4 Keltner channel** — overlap with #1 | Medium | Small |
| 10 | **#8 Funding-rate filter** — new data adapter | High potential | Largest |

Phase 5 implements **none of these** — it implements only the safest
combined-Markov experiment (routing + soft sizing) per the user's spec.
The list above is the runway for Phase 6+.
