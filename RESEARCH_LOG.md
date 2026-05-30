# Research Log

Chronological record of experiments that were actually run and the
honest result. All numbers below are out-of-sample stitched walk-forward
on BTC/USDT 4h, 24 months, fees 10 bps/side + 5 bps slippage, 8 folds
(train 1440 / test 360 / embargo 6) unless noted otherwise.

---

## v1: RSI mean-reversion (decommissioned)

- 1-minute polling, hard-coded `RSI < threshold` long entries, flat % stop.
- Failed on costs: at 1m, ATR is ~0.025% of price; a 1.5×ATR stop is
  ~5× narrower than a 0.2% round-trip fee. Every trade opened underwater.
- In-sample 24-month: -75% total return, 7% win rate, PF 0.08.
- **Decommissioned** in favor of v2 long-short on 4h.

## v2 long-short (current baseline)

- 4h decision bars; long + short setups with EMA50/200 regime gate.
- Setups: pullback (RSI<32 + 21-EMA touch), breakout (3-bar play + VWAP).
- Walk-forward OOS: **+8.97% return, 4.43% max DD, PF 1.69, 33 trades**.
- Buy-and-hold over the same window: ~+27% return, ~50% DD. Strategy is
  risk-managed underperformer in absolute terms.
- Currently the floor every new variant must beat.

## Markov regime layer — first iteration (rejected)

- 6-state hand-defined alphabet (up/sideways/down × low/high vol).
- First-order transition matrix fit per walk-forward fold.
- Used as a binary long-entry filter.
- In-sample: PF jumped 1.69 → 0.86 (looked great).
- OOS: PF dropped to 0.60 (worse than baseline). Textbook overfit
  signature; first iteration rejected.

## Markov regime — multi-mode rebuild

A discipline-focused rebuild evaluated five modes:

| mode | OOS PF | OOS return | verdict |
|---|---:|---:|---|
| `hard_filter` | 1.68 | +7.70% | rejected — no improvement |
| `soft_sizing` | 1.86 | +7.48% | risk-adjusted win, lower return |
| `bad_regime_avoidance` | 1.69 | +8.97% | inert; per-fold sample too small to trigger |
| `multi_timeframe_soft_sizing` | 1.86 | +7.48% | degenerates to single-TF (1d slice < min_training_bars) |
| `strategy_routing` (MTF on) | 2.17 | +9.23% | initial "winner" — later recalibrated |
| `strategy_routing` (MTF off) | 1.54 | +3.94% | honest baseline; MTF path was inflating |
| `routing_sizing_combined` | 1.37 | +2.55% | combination worse than either component |

The MTF recalibration came from the code-review pass: the multi-timeframe
code path forward-fills missing `long_allowed` rows as True at fold
boundaries (see `BUGS.md` #4). With that disabled, the `strategy_routing`
result lands at PF 1.54 — below baseline. **The honest current best
remains the no-Markov baseline (PF 1.69).**

Confirmed quant-literature finding: regime models add value via exposure
control (`soft_sizing` does cut drawdown ~46% at modest return cost), not
direction prediction (`hard_filter` is OOS-neutral).

## Trading-logic audit (Phase 1–7)

A per-trade detailed CSV of the 57 baseline trades surfaced concrete
edge signals:

- **State filter works**: PF 2.16 / 1.65 in `up_*` states vs PF 0.17 /
  0.20 in `down_*` states. Strong evidence that exposure control on
  regime is the only Markov role with real signal.
- **Holding past 16 bars** is where the money is: 19 of 46 such
  trades won at **80% win rate / +2.13%/trade compounded**. The
  current exits kick the strategy out of trends.
- **RSI<32 long pullback** went 0/6 wins at PF 0.39. The classic
  "buy oversold dip in uptrend" thesis is measurably negative on 4h BTC.
- **21-EMA trail exit on breakouts** is PF 0.08 — the worst exit
  reason. Wider trail or different exit logic is needed.
- **Entries far below VWAP** (bottom quartile) went 0/15. A
  vwap-distance gate is the strongest single filter visible in the data.
- **Short pullback** (RSI > 68, bearish regime, near 21-EMA) won 50%
  at PF 3.04 on 6 trades. Small sample, worth keeping.

## Donchian-20 trend-following experiment

Direct attempt to capture the "16+ bar holds win 80%" finding via a
trend-continuation entry instead of mean-reversion.

| variant | OOS PF | OOS return | verdict |
|---|---:|---:|---|
| Donchian-20 only | 0.90 | -2.16% | rejected — fails both adoption criteria |
| Donchian-20 + strategy_routing | 0.84 | -2.97% | rejected — routing slightly hurts |

The trend-capture mechanism worked: 19 of 46 Donchian trades held ≥16
bars and won 89.5% at +1.53%/trade. The problem: the other 27 trades
stopped out short with average -1.07% per ATR-stop hit, more than
overwhelming the long-hold winners. Donchian-20 fires too often in
chop relative to confluence-triggered setups.

Per the hard rule "do not tune endlessly", this experiment is closed.

## SuperTrend(10, 3) trend-following experiment

Same trend-capture thesis as Donchian, more selective entry trigger
(ATR-banded directional flips, not channel-max touches). Long-only,
EMA50/200 regime-gated, single setup, no parameter tuning.

| variant | OOS PF | OOS return | trades | verdict |
|---|---:|---:|---:|---|
| supertrend_only | 9.02 | +13.00% | 9 | **not adopted** — trade-count gate (≥30) failed despite PF passing by ~5× |
| supertrend + strategy_routing | 34.54 | +2.47% | 4 | rejected — routing cut signal in half, PF inflated by tiny sample |

Headline data points: 77.8% win rate, 1.49% max DD (lowest of any
experiment), 4 of 8 folds positive vs baseline's 3, no fold reported
a >1.17% drawdown. The trend-capture mechanism Donchian failed to
realise (Phase 3's "16+ bars → PF 15.84" finding) appears to be
working here, but the signal fires roughly 4–5 times per year on 4h
BTC — too rare to confirm at 24-month horizon.

Per the locked criteria: not adopted at 24mo. The result is "promising
under-sampled signal", not "working strategy". See
`research/supertrend_report.md`. Follow-up at 48mo below.

## SuperTrend(10, 3) on 48-month history (Issue #11)

Same code, same parameters (period=10, multiplier=3.0), same fees,
same fold sizing (train 1440 / test 360 / embargo 6). Only the data
window changed: 24 → 48 months. NOT a parameter tune.

48-month walk-forward, 20 folds, BTC/USDT 4h:

| variant | n | OOS return | max DD | PF | Sharpe | win % | folds+ |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 103 | +3.28% | 12.74% | 1.09 | 0.027 | 25.2% | 8/20 |
| **`supertrend_only`** | **35** | **+38.66%** | 9.63% | **2.24** | **0.266** | **45.7%** | **10/20** |
| `supertrend_plus_routing` | 20 | +29.56% | **5.95%** | 3.16 | 0.337 | 50.0% | 8/20 |

**`supertrend_only` clears both adoption gates**: PF 2.24 > 1.69 and
35 ≥ 30. First variant since v2 itself to do so on a clean OOS
walk-forward. **Adopted as a research candidate.** Live worker
unchanged.

The 24-month result was clearly under-sampled noise around the
true edge: doubling the window quadrupled trades (9 → 35) and the PF
compressed from 9.02 to 2.24 — still 1.3× the gate. Win rate fell
77.8% → 45.7%, exactly what you'd expect as the sample stops being
dominated by a few clean trends.

`supertrend_plus_routing` has the best risk profile (DD 5.95%, win
50.0%, PF 3.16, Sharpe 0.337) but **fails the trade-count gate** at
20. The locked criteria explicitly refuse the move "adopt the
variant that filters to its best subset". Tracked as a candidate
sizing overlay for after Issue #5.

Baseline (v2 long-short) on the 48mo window degraded to PF 1.09 from
PF 1.69 on 24mo. This is itself informative: the original 24mo
baseline was a favorable window for the pullback/breakout setups, not
the floor it appeared to be.

See `research/supertrend_48mo_report.md`.

## BTC/ETH relative-strength experiment (Issue #5)

ETH used as **market context only** (not traded). Fixed conventional
windows: `lookback=30`, `ratio_ema=30`, `min_btc_minus_eth_return=0.0`,
`require_ratio_above_ema=true`. Same 48mo data, fees, fold geometry as
Issue #11. RS features are causal (close[i]/close[i-30], EMA recursive
on past). RS decisions computed once on the full aligned index then
sliced per fold — OOS-safe because no parameter is fit on train.

Two RS overlay modes:

- **filter**: long entries blocked unless BTC stronger than ETH **and**
  BTC/ETH ratio above its EMA30.
- **sizing**: keep all entries but scale size 1.0 (both gates pass) /
  0.5 (one passes) / 0.0 (neither passes — effectively blocked).

48-month walk-forward, 20 folds:

| variant | n | OOS return | max DD | PF | Sharpe | win % |
|---|---:|---:|---:|---:|---:|---:|
| `supertrend_only` | 35 | +38.66% | 9.63% | 2.24 | 0.266 | 45.7% |
| `supertrend_with_btc_eth_rs_filter` | 20 | +35.43% | **7.07%** | **3.33** | **0.384** | **55.0%** |
| `supertrend_with_btc_eth_rs_sizing` | 27 | +38.03% | **6.29%** | 3.01 | 0.338 | 48.1% |

**Result: NOT ADOPTED — fails the 30-trade discipline gate.**

The RS thesis is materially supported by the data: PF lifts +49% /
+34% and max DD drops -27% / -35% across the two modes. Win rate climbs
+9pp (filter) / +2pp (sizing). The 8 SuperTrend signals that would have
fired in `rs_weak` (neither gate passing) are exactly the ones the
overlay blocks — consistent with the thesis that BTC flips without
crypto-wide confirmation are lower-quality. Sizing mode is the gentler
and more informationally honest implementation (continuous, not binary)
and achieves the lowest DD of any SuperTrend variant in this repo.

The blocker is sample size: filter mode drops to 20 trades, sizing to
27. Project-wide discipline says ≥30 OOS trades, no exceptions for
high-PF subsets. This is the same gate that correctly rejected
SuperTrend on 24mo (9 trades) and SuperTrend+routing on 48mo (20
trades). Adopting here would break that consistency.

**Recommended next step (deviates from strict spec):** rather than
jumping to Issue #6 (HMM), the cleaner next experiment is a multi-asset
extension applying the SuperTrend + RS framework to ETH as a traded
asset on the same engine. That roughly doubles the trade count and
directly resolves the count-gate question this experiment leaves open.
HMM remains queued behind that.

See `research/btc_eth_relative_strength_report.md`.

## Multi-asset SuperTrend + RS portfolio (Issue #12)

ETH added as a tradeable asset on the same engine. One position open
at a time across BTC + ETH. When both signal on the same bar: pick by
RS score → SuperTrend distance → skip. Same SuperTrend(10, 3), same
RS config, same fees and fold geometry as Issue #5 / #11.

48-month walk-forward, 20 folds:

| variant | n | OOS return | max DD | PF | Sharpe | win % | folds+ |
|---|---:|---:|---:|---:|---:|---:|---:|
| `btc_supertrend_only` | 35 | +38.66% | 9.63% | 2.24 | 0.266 | 45.7% | 10/20 |
| `eth_supertrend_only` | 30 | +37.86% | **5.30%** | **2.92** | **0.336** | **63.3%** | 10/20 |
| `eth_supertrend_rs_sizing` | 21 | +17.33% | 3.86% | 3.05 | 0.380 | 66.7% | 8/20 |
| `multiasset_supertrend_rs_one_position` | **39** | **+40.99%** | 9.61% | **2.48** | 0.276 | 48.7% | **12/20** |

**Two variants adopted as research candidates** — first to clear all
four gates (trades ≥ 30, PF > 2.24, DD ≤ 9.63%, fold consistency not
worse) since v2 itself:

- The multi-asset portfolio (the spec result): 39 trades, PF 2.48,
  DD 9.61% (by 0.02 pp), 12/20 folds positive. Universe-expansion
  thesis validated.
- ETH solo (surprise side finding): 30 trades exactly, PF 2.92, DD
  5.30%, 63.3% win rate. Markedly better risk-adjusted than BTC.
  SuperTrend(10, 3) is cleaner on ETH 4h than BTC 4h on this window.

Counterintuitive sub-finding: the symmetric ETH RS overlay *hurts*
ETH (cuts return +37.86% → +17.33%) because the "ETH stronger than
BTC" condition is the minority over this window — the overlay chokes
off actually-winning ETH trades. The RS overlay is BTC-favoring by
construction; symmetry does not equal generality.

See `research/multiasset_supertrend_rs_report.md`.

## ETH vs BTC SuperTrend diagnostic (Issue #13)

Research-only analysis on the Issue #12 result. Asked: why did ETH
beat BTC, was it luck or structural, would a selector help?

Findings:

- **Trend structure is identical.** BTC 205 flips vs ETH 209; mean
  run 42.6 vs 41.7 bars; ADX 28.1 vs 27.7; % time trending 97.4% vs
  97.1%. SuperTrend behaviour is indistinguishable between the two
  assets.
- **The win-rate gap is the whole mechanism.** BTC 45.7% vs ETH 63.3%
  (+17.6 pp). ETH's average winner is actually *smaller* (+2.71% vs
  +3.98%); the PF gap comes from winning more often.
- **ATR percentage is the single structural driver.** ETH ATR mean
  2.01% vs BTC 1.47% — ETH is 36% more volatile per bar. Same
  SuperTrend multiplier (3.0×) gives ETH a structurally wider band
  that survives more intra-trend noise.
- **Per-fold the assets are tied.** BTC mean fold return +1.73% σ
  4.10%; ETH +1.69% σ 3.98%. BTC won 8 folds, ETH won 10, 2 tied —
  effectively a coin flip. The 18-pp win-rate gap is at the edge of
  statistical significance (z ≈ 1.4).
- **Both rotation selectors HURT vs ETH solo.** Tested two per-bar
  selectors (SuperTrend distance / ATR; RS score). Both gave worse PF
  (2.12 / 2.01 vs 2.92), nearly doubled DD (10.09% vs 5.30%) and lower
  Sharpe. There is no per-bar asset-quality signal in existing
  SuperTrend + RS information.

Read: ETH's edge is plausibly real (driven by the ATR% structural
fit) but smaller than headline metrics suggest. A rotation overlay
won't capture it without new information.

See `research/eth_vs_btc_supertrend_analysis.md`.

## Recommended next experiment

**HMM 2-state regime overlay (Issue #6).** Promoted from second
queue position to first by Issue #13 evidence. HMM tests an
orthogonal mechanism (latent EM-fit regime → exposure scaling) that
does not suffer from the selection-degrades problem found in #13.
Top-5 crypto remains a valid future experiment but as a *parallel
portfolio*, not a rotation.

Queue per `ROADMAP.md`:

1. HMM 2-state regime overlay (Issue #6)
2. Top-5 crypto parallel portfolio (new issue if pursued)
3. Funding-rate stress filter
