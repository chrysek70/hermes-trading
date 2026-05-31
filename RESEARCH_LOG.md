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

## HMM 2-state regime overlay (Issue #6)

Optional EM-fit Gaussian HMM on 5 causal features (log-return,
realised vol 24, ATR%, EMA50 slope, SuperTrend distance). 2 states.
Per-fold fit on train; train-only state mapping (favorable = lower
realised vol with SuperTrend expectancy as soft tiebreaker); test
decisions plug into the existing `decisions_df` overlay.

48-month walk-forward, 20 folds:

| variant | n | OOS return | max DD | PF | Sharpe | win % |
|---|---:|---:|---:|---:|---:|---:|
| `supertrend_only_btc` | 35 | +38.66% | 9.63% | 2.24 | 0.266 | 45.7% |
| `supertrend_hmm_filter_btc` | **24** | +49.98% | **3.79%** | **4.01** | **0.434** | 54.2% |
| `supertrend_only_eth` | 30 | +37.86% | 5.30% | 2.92 | 0.336 | 63.3% |
| `supertrend_hmm_filter_eth` | **17** | +27.80% | **4.13%** | **4.27** | **0.402** | 70.6% |

**Result: NOT ADOPTED on either asset — trade-count gate fails.**

The HMM mechanism is real and strong: PF +79% on BTC, +46% on ETH;
DD -61% on BTC, -22% on ETH. But the filter cut trades from 35 → 24
(BTC) and 30 → 17 (ETH), both below the 30 gate.

Key sub-findings:

- **Sizing and filter modes produced literally identical results.**
  The 2-state Gaussian HMM probabilities are bimodal — when
  P(favorable) clears the 0.55 half-size threshold it almost always
  clears the 0.70 full-size threshold too. Sizing degenerates to
  filter.
- **State mapping is stable in the right direction:** the higher-vol
  state is "adverse" in all 40 fold mappings (20 BTC + 20 ETH).
  Vol ratio adverse/favorable ranges 1.04 to 2.24 per fold.
- **Realised volatility dominates the regime separation.** Adverse
  state has ~2× the realised vol of favorable (and substantially
  more negative mean log return). The other features contribute to
  state membership decisions but the *separation* is vol-driven.
- **Per-fold trade counts per state were too sparse** (< 5) for the
  primary train-expectancy mapping to apply — the volatility-based
  fallback was used on all folds.

This is the **third independent regime mechanism** to clear the PF
and DD criteria and fail the 30-trade gate, after RS filter (Issue
#5) and routing (Issue #12). The pattern is informative: the
30-trade gate was calibrated against an unfiltered baseline (35
trades on BTC); once a useful regime overlay cuts ≥30% of trades, the
gate fails by construction. The mechanism is genuine — a random
filter that removed 30% of trades would NOT lift PF 79%; only an
actually-informative filter does that.

See `research/hmm_regime_report.md`.

## Top-5 parallel portfolio (Issue #14)

Fixed universe (BTC, ETH, SOL, BNB, XRP — all 48mo data available).
Each asset trades SuperTrend(10, 3) independently with equal risk
budget (1/N). No rotation, no per-bar selection. Up to 5 concurrent
positions allowed. HMM overlay variants tested per-asset.

48-month walk-forward, 20 folds:

| variant | n | OOS return | max DD | PF | Sharpe | win % | max conc. |
|---|---:|---:|---:|---:|---:|---:|---:|
| `top5_supertrend_parallel` | 155 | +40.70% | **2.49%** | 2.19 | 0.256 | 51.6% | 4 |
| `top5_hmm_filter_parallel` | 95 | +26.74% | 1.86% | 2.49 | 0.288 | 51.6% | 4 |
| `top5_hmm_sizing_parallel` | 95 | +25.17% | 1.86% | 2.41 | 0.281 | 51.6% | 4 |
| **`btc_eth_reference_parallel`** | **65** | **+39.72%** | **5.54%** | **2.50** | **0.296** | **53.8%** | 2 |

**Spec-defined `top5_supertrend_parallel` FAILS adoption by 0.05 PF
(2.19 vs 2.24 required).** Four of five gates cleared comfortably;
PF is the only failure. The added assets (SOL, BNB, XRP) dilute the
BTC/ETH edge: XRP contributed -1.43% (net negative), SOL +11.81%
and BNB +10.56% (positive but at slightly lower per-trade PF than
BTC/ETH). Universe choice was fixed at experiment start; per the
hard rule "do not optimize asset list after seeing results", XRP
stays in the reported numbers.

HMM variants on top-5 clear PF (2.49) and DD (1.86%) but cut
return to +26.74% / +25.17% — fail the 38.66% return gate.

**The reference variant `btc_eth_reference_parallel` (2-asset
parallel) clears ALL FIVE gates** — first variant to do so in this
project. Adopted as a research candidate. It is a strict upgrade
over the Issue #12 adopted one-position multi-asset variant:

| | Issue #12 BTC/ETH one-position | Issue #14 BTC/ETH parallel |
|---|---:|---:|
| trades | 39 | **65** (+67%) |
| OOS return | +40.99% | +39.72% (-1.27 pp) |
| max DD | 9.61% | **5.54%** (-42%) |
| PF | 2.48 | 2.50 (≈) |

Same engine, no overlay. Dropping the one-position constraint and
letting BTC and ETH trade in parallel is mechanically better:
- No forced selection between concurrent signals.
- Diversification benefit on DD.
- Per-asset half-size means total exposure at max concurrency =
  single-asset full size (no leverage).

Side findings:

- RS context variant was SKIPPED per hard rules. The Issue #5 RS
  construct is fundamentally pairwise (BTC vs ETH return diff and
  BTC/ETH ratio EMA); there is no clean 5-asset generalization
  without new design choices.
- Concurrency reduces DD, doesn't increase it (correlation < 1
  between asset moves).
- The parallel form is now the canonical research framework for any
  future multi-asset overlay tests in this repo.

See `research/top5_parallel_portfolio_report.md`.

## Funding-rate filter (Issue #7)

Three-phase research: (1) data audit, (2) diagnostics, (3) filter
experiment. Binance Vision CDN hosts monthly funding archives (8h
cadence) from 2020-01 onwards — fully covers the 48mo window.
Forward-fill alignment to 4h decision bars; causal rolling 30-day
percentile rank. Locked thresholds from spec: block at p95, half-size
at p90.

Phase 2 diagnostics — funding has essentially no linear predictive
value at any tested horizon (correlation ≤ 0.03 against forward 4h /
24h / 5d returns). Bucket analysis shows a U-shape: extreme negative
*and* extreme positive funding both precede *higher* forward returns
than the median bucket — the opposite of the "overheated = bad"
filter hypothesis. Critically, SuperTrend entries rarely coincide
with extreme funding (BTC: 0 of 39 trades in p95+; ETH: 2 of 35).
SuperTrend enters near the start of trends; extreme funding marks
late stages.

48-month walk-forward, 20 folds:

| variant | n | OOS return | max DD | PF | win % |
|---|---:|---:|---:|---:|---:|
| `eth_supertrend_baseline` | 30 | +37.86% | 5.30% | 2.92 | 63.3% |
| `eth_supertrend_funding_filter` | 28 | +38.46% | 5.30% | 3.17 | 64.3% |
| `btc_eth_parallel_baseline` | 65 | +39.72% | 5.54% | 2.50 | 53.8% |
| `btc_eth_parallel_funding_filter` | 63 | +40.01% | **4.68%** | **2.57** | 54.0% |
| `btc_eth_parallel_funding_sizing` | 63 | +39.28% | 4.68% | 2.55 | 54.0% |

**Result: marginal pass.** `btc_eth_parallel_funding_filter` improves
PF (+0.07) and DD (-15.5%) over the Issue #14 baseline. Only 2 of 65
trades affected. The 2 blocked trades happened to be losers, so PF /
DD ticked up — but with effect size this small, the result is
plausibly sample noise. Phase 2 diagnostics correctly predicted this
outcome.

Adopted as **marginal research candidate** per the literal criterion
("must improve PF or DD without destroying trade count"), with the
explicit caveat that the improvement is within the fold-to-fold
noise band. Not a primary strategy. Not wired into live trading.

Filter and sizing modes gave identical results (same pattern as HMM
Issue #6: when percentile crosses 90 it usually crosses 95 quickly).

See `research/funding_rate_filter_report.md`, `funding_rate_diagnostics.md`,
and `funding_rate_data_audit.md`.

## Online walk-forward adaptive learning simulator (Issue #32) — research

Goal: test the bot's behaviour under *truly online* adaptation,
where each historical 4h bar is treated as if it were live and the
worker can only see the past. No future leakage. No parameter
tuning. No live wiring.

Phase 1 audit: `research/current_adaptation_audit.md`. Headline
finding: the adopted multi-asset live worker
(`hermes_trading/multi_loop.py`) is **not learning** in any
meaningful sense. Every strategy / risk parameter is fixed in
yaml; the only intra-trade adaptation is the SuperTrend trailing
stop (which is indicator behaviour, not learning). Reflection
(`reflect.py`) is wired into `loop.run` but not into
`multi_loop.run`. The decay monitor is report-only. Issue #33's
`LiveVolSizingOverlay` is opt-in and stays off in the adopted yaml.

Simulator: `scripts/run_online_walk_forward.py`. CLI:
`--config <yaml> --months <N> --adaptive-rule <name>`. Six rules:
`none`, `rolling_decay_size`, `consecutive_loss_size`,
`stop_cluster_size`, `vol_sizing`, `ensemble`. All multipliers
locked at the Issue #32 spec values (no tuning). Sizing locks at
entry; never resizes open trades. Reuses
`signals.long_entry/long_exit/short_entry/short_exit/initial_stop`,
`multi_loop.LiveFundingOverlay` and `evaluate_funding_gate`, and
the Issue #29 `RESEARCH_FEE_PER_SIDE` / `RESEARCH_SLIPPAGE`
constants — fill convention is bit-for-bit the live worker's.

Headline numbers (24mo OOS, 2024-05-01 → 2026-04-30, BTC+ETH 4h,
75 closed trades — identical across all 6 rules because adaptive
rules in this study only resize, they never gate):

| rule | total return | max DD | PF | mean mult | ret/exp |
|---|---:|---:|---:|---:|---:|
| `none` | +29.36% | 16.23% | 1.37 | 1.000 | +29.36% |
| `rolling_decay_size` | +6.77% | 16.23% | 1.13 | 0.817 | +8.29% |
| `consecutive_loss_size` | +14.27% | 14.11% | 1.23 | 0.887 | +16.10% |
| `stop_cluster_size` | +14.12% | 8.72% | 1.51 | 0.350 | +40.35% |
| `vol_sizing` | **+20.78%** | **7.78%** | 1.51 | 0.553 | +37.55% |
| `ensemble` | +13.60% | **5.63%** | 1.55 | 0.313 | **+43.39%** |

Window comparison: vol_sizing's ranking is stable across the 3 /
6 / 12 / 24 mo windows. `ensemble` always has the lowest DD;
`vol_sizing` always has the highest absolute return among the
adaptive rules. `none` wins absolute return on the latest 3-month
window but at 16.23% DD vs vol_sizing's 7.78%. `rolling_decay_size`
loses on every measure — too lagged to protect, costs return on
recoveries.

Conclusions (full text in `research/online_walk_forward_report.md`):

- The bot is **not learning today** and any first live adaptation
  should be the safest possible.
- **vol_sizing is the best single rule.** Halves DD, improves PF,
  keeps 71% of return. Already proven in Issue #27 offline; this
  experiment confirms it survives an *online* feedback loop.
- `ensemble` is best on risk-adjusted basis but spends too much
  time at the 0.25 floor for it to be a reasonable default.
- `rolling_decay_size` and `consecutive_loss_size` are decay-by-
  outcome rules and underperformed. They lag the regime.
- `stop_cluster_size` works but is also a low-floor (0.35 mean
  mult) rule.

Next implementation issue should be: turn on `vol_sizing` (Issue
#33's `LiveVolSizingOverlay`) in the adopted yaml after a 4-week
forward-paper test. Do NOT enable rolling_decay_size,
consecutive_loss_size, stop_cluster_size, or ensemble in live —
they all over-throttle.

Outputs:
- `research/current_adaptation_audit.md`
- `research/online_walk_forward_report.md`
- `results/online_walk_forward_decisions_*_20260531_073952.csv`
- `results/online_walk_forward_trades_*_20260531_073952.csv`
- `results/online_walk_forward_comparison_20260531_073952.csv`

Tests: 21 new checks in Section 15 of
`scripts/test_multiasset_worker.py`. 260/260 pass.

## Replay vol_sizing parity (Issue #34) — shipped

Bug fix: `scripts/replay_live.py --config state/live_multiasset_long_short_funding_vol.yaml`
was running the funding overlay correctly but ignoring the
`vol_sizing:` block Issue #33 added — so the replay reported
identical losses to the no-vol config. Root cause: replay's
`_run_config_replay` predates Issue #33 and was never updated.

Fix wires the existing `LiveVolSizingOverlay` (from
`hermes_trading.multi_loop`) into the replay's config-mode entry
path. No new vol math — uses the same class the live worker uses
with the same locked Issue #27 parameters (window_bars=24 /
train_months=12 / mult=1.0/0.5/0.25). At entry,
`final_size = size_per_asset × funding_allow × vol_multiplier`. The
vol multiplier locks at entry; never resizes open positions.

Replay boot header now includes `vol_sizing=ENABLED ...` when the
yaml's `vol_sizing.enabled: true`. ENTER lines show
`size=… vol_mult=…`. Per state-change bars now log a
`vol BTC/USDT rv24=…% bucket=Q… mult=… q=[…]` line matching the
live worker's verbose format. End-of-run summary adds
`vol_sizing mean mult`, `vol_sizing mean size`, `vol_sizing by
bucket`. Trade-output CSV gains 8 columns appended after the
original Issue #26 12-column prefix (preserved for backward
compat): `base_size`, `vol_multiplier`, `final_size`,
`realized_vol_24`, `vol_bucket`, `vol_q1`, `vol_q2`, `vol_q3`.

Before / after on the 3-month window:

| config | trades | return | DD | mean vol mult |
|---|---:|---:|---:|---:|
| `funding.yaml` (no vol) | 6 | -9.61% | 9.61% | n/a |
| `funding_vol.yaml` (Issue #34) | 6 | **-6.04%** | **6.04%** | 0.583 |

Same 6 entries / exits (sizing doesn't gate signals); ~37%
smaller realized loss because chop entries sized to 0.5× the base.
Bucket distribution: 5 trades Q2_Q3, 1 trade Q1.

Tests added as Section 17 of `scripts/test_multiasset_worker.py`
(20 checks: import surface, locked defaults, CSV schema preserves
Issue #26 prefix + appends Issue #34 fields, source-level wiring
confirms each spec contract, opt-in yaml has vol_sizing enabled,
existing yaml has no vol_sizing block). 280/280 total multi-asset
checks pass (was 260). 14/14 decay-monitor checks pass.
`py_compile hermes_trading/*.py scripts/*.py` clean. Legacy
`--strategy` replay path bit-for-bit identical (4 trades,
+1.79%, DD 1.61%, PF 2.13).

Live worker unchanged. `signals.py` unchanged. All `state/*.yaml`
unchanged.

## Vol-sizing overlay wired opt-in to live (Issue #33) — shipped

After Issues #27 (research), #27 follow-up (`research/recent_adaptation_sizing_report.md`)
and #29 (live fill parity) closed the prerequisites, vol_sizing
is now available in the live paper worker as an OPT-IN additive on
a new yaml `state/live_multiasset_long_short_funding_vol.yaml`.

Implementation:
- `LiveVolSizingOverlay` class in `hermes_trading/multi_loop.py` mirrors
  `LiveFundingOverlay`. Loads ~15 months of 4h close data per asset
  at boot; computes 24-bar rolling realised vol of log returns;
  per-tick lookup computes quartile thresholds from the trailing
  12-month train window STRICTLY BEFORE the current bar (no future
  leak); returns multiplier 1.00 / 0.50 / 0.25 by Q-band, fail-open
  to 1.00 with heartbeat warning on insufficient history or loader
  failure.
- `run()` reads optional `vol_sizing:` config block (disabled by
  default — existing yaml unchanged). When enabled, opens new
  positions with `size = base × vol_mult` while funding stays a hard
  gate. Locks `vol_multiplier` at entry; never resizes an open
  position.
- Trade rows gain `base_size`, `vol_multiplier`, `final_size`,
  `realized_vol_24`, `vol_bucket`, `vol_q1`, `vol_q2`, `vol_q3`.
  Decay monitor schema preserved (it only requires `return`).
- Heartbeat gains `vol_sizing_enabled`, `realized_vol_24`,
  `vol_bucket`, `vol_multiplier`, `vol_q1`, `vol_q2`, `vol_q3` per
  asset.
- Verbose tick output adds `vol: rv24=X% bucket=QN mult=Y q=[a,b,c]`
  per asset (Issue #33 spec format).

Tests: Section 16 of `scripts/test_multiasset_worker.py` covers
locked-defaults, pure bucket / multiplier mapping, opt-in config
schema, existing config unchanged (no vol block), fail-open under
insufficient history, low / mid / high vol bucket assignment with
synthetic series, no-future-leak via train-window slicing,
source-level wiring (overlay instantiation, multiplier application
at entry, trade-row carriage, heartbeat fields, verbose emission,
funding gate independence). 240/240 multi-asset checks now pass
(was 194). Decay monitor 14/14 still passes. `py_compile` clean.

`signals.py`, `state/strategy_supertrend_long_short.yaml`,
`state/live_multiasset_long_short_funding.yaml`, and `loop.py`
all unchanged.

Adoption gate per the spec: no automatic default switchover.
Operator points `--config` at the new yaml when ready. Suggested
forward paper-test ~30 trades before any default switch.

## Adaptive regime-based position sizing (Issue #27) — research

Tested three sizing overlays on top of the currently adopted BTC/ETH
long-short SuperTrend + funding-filter candidate (Issues #20 / #21).
Question: can HMM or volatility-band sizing reduce DD or improve PF
without materially cutting trade count? **All three sizing variants
pass.** Trade count is identical across every variant (123) because
the layer multiplies rather than gates.

48-month walk-forward, 20 OOS folds, BTC + ETH parallel:

| variant | trades | OOS return | max DD | PF | mean mult | ret/exp |
|---|---:|---:|---:|---:|---:|---:|
| baseline_funding_only (= adopted live) | 123 | +139.71% | 4.64% | 3.35 | 1.000 | +139.71% |
| hmm_sizing | 123 | +78.38% | 2.45% | 3.84 | 0.652 | +120.13% |
| **vol_sizing** | 123 | +72.71% | **2.10%** | **4.63** | 0.533 | **+136.54%** |
| hmm_plus_vol_sizing | 123 | +59.69% | **1.57%** | 4.49 | 0.472 | +126.59% |

Key conceptual point: **the same HMM that failed as a hard filter
(Issue #6 / #20) becomes a useful Risk-layer overlay when applied
as a sizing multiplier instead.** The filter dropped trade count
below the 100 gate; the multiplier preserves every signal.

By-regime breakdown confirms the overlays separate cleanly:
- HMM favourable: 58 trades, 63.8% win, +50.22% (~5× the adverse
  band's +10.25% on similar count).
- Vol low-quartile: 29 trades, 72.4% win, +30.28%; high-quartile:
  42 trades, 50.0% win, +4.65% — win rate falls monotonically
  with volatility band.

Strongest candidate: `vol_sizing` — best PF, second-lowest DD,
highest return-per-exposure (+136.54%, essentially preserving the
baseline's efficiency at half the exposure), and the simplest
implementation (no model fit, no heavy dependency).

Recommendation: **do not wire to live yet.** Adding a sizing layer
on top of the known live paper-fill slippage asymmetry would
compound the live-vs-research drift. The Execution-layer slippage
fix (item #1 on the architecture roadmap) is a prerequisite. After
that ships, `vol_sizing` is the next live-candidate add-on.

Files: `scripts/run_adaptive_sizing.py`,
`research/adaptive_sizing_report.md`,
`results/adaptive_sizing_comparison_20260531_005427.{csv,md}`,
`results/trades_adaptive_sizing_20260531_005427.csv`.

`signals.py` unchanged. No live config modified. `py_compile`
clean.

## Replay multi-asset config support (Issue #26) — shipped

`scripts/replay_live.py` now accepts `--config <multi-asset yaml>`
in addition to the legacy `--strategy <yaml>`. The new path replays
the exact config the live worker reads:

```bash
uv run python scripts/replay_live.py \
    --config state/live_multiasset_long_short_funding.yaml \
    --n-months 24 --bars-per-second 20 --quiet-flat \
    --trades-out results/replay_trades_<ts>.csv
```

In config mode the script loads each `assets:` entry, iterates the
union of bar timestamps in lockstep, enforces `max_open_positions`
and `size_per_asset`, and runs the funding overlay
(`LiveFundingOverlay` — reused from `hermes_trading.multi_loop`,
no replay-specific funding code) using the same Issue #21 logic the
live worker uses. The display mirrors `multi_loop.py`:

```
[2026-04-22 04:00] tick BTC/USDT close=78012.79 st=UP line=74616.47 dist=+4.55% v=v3-supertrend-long-short-01 pos=flat
[2026-04-22 04:00] BTC/USDT ENTER long supertrend @ 78051.80 stop=73700.81
  funding BTC/USDT rate=-0.0042% pct=25.6 decision=allow
portfolio open=1/2  realized=-6.752%  unrealized=-0.025%
```

Live semantics match (Issue #24): entries and SuperTrend flip / time
exits evaluate on `signal_row = iloc[i-1]` (last closed bar);
intra-bar stops fire from `display_row` low / high. `signals.py`
unchanged. No live worker behaviour changed — only a one-line shared
import of `LiveFundingOverlay` / `can_enter` / `evaluate_funding_gate`.

`--trades-out` writes per-trade CSV with the on-disk schema:
`asset, direction, entry_time, exit_time, entry_price, exit_price,
return_pct, net_return_pct, setup, exit_reason, bars_held,
funding_decision`. End-of-run summary adds: trades by asset, by
direction, by exit reason, portfolio realized PnL, max concurrent
open positions.

Tests added as Section 13 of `scripts/test_multiasset_worker.py`
(21 new checks: helper round-trips, funding decision mapping,
mutually-exclusive argparse, CSV column / row round-trip, source-level
parity-checks confirming the script uses `signal_row` for entries
and `display_row.low/.high` for intra-bar stops). 158/158 total
checks pass; decay monitor self-test 14/14 still passes;
`py_compile hermes_trading/*.py scripts/*.py` clean.

The legacy `--strategy` path is preserved byte-for-byte (verified
against the user's reference invocation on 3 months of BTC).

## Alpha / Risk / Execution architecture map (Issue #25) — documentation

Created `ARCHITECTURE.md` and `research/alpha_risk_execution_audit.md`
to integrate the canonical quant-shop layering (Alpha → Risk →
Execution → Diagnostics → Research) into the project. Every existing
module classified into the appropriate layer. Key clarifications:

- **Markov / HMM models belong in the Risk layer** — they estimate
  when a strategy should be trusted and how exposure should be
  sized. They do not decide direction.
- **Funding filter belongs in the Risk layer** for the same reason
  — direction is decided by the SuperTrend flip; funding decides
  whether that direction is permitted at the current extreme.
- **RS belongs in the Risk layer** — RS scales an existing alpha
  signal; it does not produce direction.

A new "Alpha / Risk / Execution Roadmap" section was added to
`ROADMAP.md`. Backlogs organised by layer:

- **Alpha**: multi-TF SuperTrend confidence, 9/21 EMA exit,
  volatility-compression breakout, factor features, cointegration
  if the universe grows.
- **Risk**: HMM as half-size band (not just filter), volatility
  targeting, dynamic exposure caps, decay-monitor → exposure
  alarm, HMM + funding redundancy test.
- **Execution**: live paper-fill quality audit (current largest
  gap — backtest models slippage, live doesn't), slippage model,
  broker / market-hours / timezone abstractions for any future
  real-money or stock-market support.
- **Diagnostics**: daily / weekly health reports, no-trade
  explanation summary, multi-asset replay (Issue #26).

Phase 4 added `state/examples/` with three template yamls
(`alpha_signal_example.yaml`, `risk_overlay_example.yaml`,
`live_execution_example.yaml`) — examples only, not loaded by the
worker. They show how each layer's config keys map to the
architecture.

Phase 5 final report in `research/alpha_risk_execution_report.md`
answers the 9 spec questions and codifies the layering invariants:

1. Alpha never reads from Risk overlays.
2. Risk never produces a direction.
3. Execution never reads strategy parameters except via yaml.
4. Diagnostics never modify state.
5. Research never touches `state/live_*.yaml` directly.

No code changes. `py_compile hermes_trading/*.py scripts/*.py` OK.
137/137 self-test still passes. 14/14 decay monitor still passes.

This is documentation + architecture scaffold only. The next
issue (#26) will start applying the architecture by upgrading
replay mode to consume the multi-asset live config.

## Live signal parity fix (Issue #24) — shipped

Audit on Issue #23 identified one material live-vs-backtest drift:
the live worker evaluated entry and SuperTrend flip-exit signals on
the current in-progress 4h candle (`ind_df.iloc[-1]`), while every
backtest evaluated on closed bars only. This created a systematic
bias toward earlier entries and earlier flip-exits than research
measured.

The fix splits each tick into two rows:

- `display_row` = current in-progress bar (`iloc[-1]`) — used for the
  tick line, the heartbeat live-price field, and intra-bar stop
  monitoring so paper stops stay responsive within the running bar.
- `signal_row` = most recently closed bar (`iloc[-2]`) — used for
  entry decisions and SuperTrend flip / regime / time / trail exits
  so live matches backtest bar-close semantics.

The split lives in a 3-line helper
(`hermes_trading.display.split_display_and_signal_rows`) and is
consumed by the two orchestration files (`loop.py` for single-asset,
`multi_loop.py` for multi-asset). **`signals.py` is unchanged.** Pure
orchestration adaptation per the audit recommendation.

Stop semantics preserved:

- `signals.long_exit` / `short_exit` still check `signal_row`'s
  low / high against the (just-ratcheted) stop — catches any closed
  bar that breached but was somehow missed (e.g. worker restart).
- Orchestration additionally checks `display_row`'s running low/high
  intra-bar — gives the worker the same reactivity it had before
  the fix.

Funding overlay timing also updated: the per-asset funding lookup
now keys off `signal_row.ts` so the gate sees the same funding value
the research backtest saw for an entry decision on that bar.

Self-test extended from 115 to 137 unique invariants (22 new) — the
new section directly proves the fix:

- intra-bar SuperTrend UP flicker on `display_row` does NOT trigger
  entry (would-have-been H1)
- closed-bar SuperTrend UP flip on `signal_row` DOES trigger entry
- closed-bar low above stop: no exit (no false breach)
- in-progress `display_row` low <= stop: exit "stop" (intra-bar
  reactivity preserved)
- closed-bar UP flip on a SHORT closes the position (with reason
  "stop" or "supertrend_flip" depending on whether the freshly-
  ratcheted line was breached — both are correct closes)
- single-bar warmup fallback: `signal_row` == `display_row` (no crash)

Decay monitor still passes 14/14. Trade-row schema unchanged.
Strategy logic unchanged (`signals.py` byte-for-byte the same).

After this fix, the live worker (in either config — long-only
fallback OR adopted long-short + funding) executes exactly as the
corresponding research backtest measured: entries and SuperTrend
flip exits at bar close, stops responsive intra-bar. Without it
every future research result would have been compared against
slightly-different live behaviour.

## Live wiring of long-short + funding filter (Issue #21) — shipped

The Issue #20 adopted research candidate (`btc_eth_long_short_funding_filter`,
PF 3.35, DD 4.64%, +139.71% return, 123 trades) is now wired into the
multi-asset paper worker as an **opt-in** config. The long-only
`state/live_multiasset.yaml` is unchanged and remains the default
fallback.

Architecture:

- New config `state/live_multiasset_long_short_funding.yaml` points
  at `state/strategy_supertrend_long_short.yaml` and enables the
  `funding_filter` block (exact Issue #20 thresholds: block long ≥ p95,
  block short ≤ p5, 180-bar rolling window, fail-open on missing data).
- New `LiveFundingOverlay` class in `multi_loop.py` loads per-asset
  funding histories at boot (via the existing
  `hermes_trading.funding` loader — same Binance Vision data path
  as Issue #7 research). Computes a rolling-window percentile series
  per asset and looks up the current funding state by timestamp on
  every tick.
- New pure-function `evaluate_funding_gate(direction, percentile, ...)`
  decides allow / block_long / block_short / missing_data /
  missing_data_blocked. Direction-aware: long blocked at high
  percentile, short blocked at low percentile. The self-test
  exercises every branch.
- Heartbeat per asset gains `funding_filter_enabled`, `funding_rate`,
  `funding_percentile`, `funding_decision`, `funding_reason`.
- Trade rows gain `funding_rate_at_entry` and
  `funding_percentile_at_entry` (kept alongside legacy fields —
  decay monitor unaffected).
- Verbose mode prints `funding: rate=... pct=... decision=...`
  and `blocked_by: funding_filter extreme_positive_funding` when
  a signal is gated.

Multi-asset worker also gains symmetric short-entry handling in
`multi_loop`. Previously the orchestration only fired longs; now
the long-short strategy yaml's short side is honoured by routing
through `signals.short_entry` / `signals.short_exit` /
`signals.initial_stop_short` (each of those gained SuperTrend short
branches in Issue #19).

Operational rule: the live worker is **not auto-switched**. The
user explicitly chooses which config to run. See README for the
exact commands.

Self-test extended from 69 to 100 unique invariants (added 31 new
checks covering every funding-gate branch, missing-data policies,
custom thresholds, and verification that the long-only fallback
config is untouched). Decay monitor unaffected — 14/14 still pass.

## BTC/ETH long-short + overlay sweep (Issue #20) — funding filter ADOPTED

Issue #19's long-short variant missed the DD gate by 0.22 pp (5.76% vs
5.54% live floor). This experiment overlaid the three already-tested
mechanisms (HMM, funding, RS) on the long-short variant with
direction-aware mapping and tested whether any could pull DD below
the gate without sacrificing return/PF.

Direction-aware mapping conventions used:
- **HMM:** per-asset Gaussian HMM applied symmetrically to longs and
  shorts. Volatility-based regime affects both trend-following
  directions identically — high-vol bars hurt either side equally.
- **Funding:** symmetric inversion. Long entries blocked at funding
  percentile ≥ 95 (Issue #7 behaviour). Short entries blocked at
  percentile ≤ 5 (extreme negative funding → squeeze setup → bad for
  shorts).
- **RS:** direction-aware. Long-side of asset X uses X's own
  build_asset_decisions (asset stronger); short-side of X uses the
  *other* asset's decision (asset X weaker → favored as short).
  Existing pairwise BTC/ETH RS module re-used; no new rule designed.

48mo walk-forward, 20 folds, train 1440 / test 360 / embargo 6:

| variant | n | L | S | OOS return | DD | PF | folds+ |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline (Issue #19) | 129 | 65 | 64 | +139.47% | 5.76% | 3.26 | 16/20 |
| hmm_filter | 74 | 40 | 34 | +49.25% | **4.29%** | 3.04 | 15/20 |
| hmm_sizing | 74 | 40 | 34 | +49.25% | 4.29% | 3.04 | 15/20 |
| **funding_filter** | **123** | 63 | 60 | **+139.71%** | **4.64%** | **3.35** | **16/20** |
| funding_sizing | 123 | 63 | 60 | +133.64% | 4.71% | 3.34 | 16/20 |
| rs_sizing | 92 | 48 | 44 | +74.61% | **4.09%** | **3.64** | **17/20** |

Adoption gates:
- Primary: DD ≤ 5.54%, PF ≥ 3.26, return ≥ +139.47%, trades ≥ 100
- Secondary: DD ≤ 5.54%, PF ≥ 3.00, return ≥ +120%, trades ≥ 100

**Result: `btc_eth_long_short_funding_filter` passes the primary gate
on all four metrics. `funding_sizing` passes the secondary gate.**

`funding_filter` adopted as research candidate. **First overlay
variant in the project to clear a primary adoption gate cleanly.**

Mechanism summary:

- The funding filter affected only 6 trades out of 129 (long 65 → 63,
  short 64 → 60) but those 6 trades were apparently sitting on the
  path to the maximum drawdown. DD collapsed from 5.76% → 4.64%
  (-1.12 pp, -19% relative) while return *increased* slightly
  (+139.47% → +139.71%). PF +0.09, Sharpe +0.012, win rate +1.1 pp.
- HMM cut trade base from 129 → 74 (-43%). Largest DD reduction
  (-1.47 pp) but return collapsed +139% → +49%. Filter and sizing
  modes gave identical results (bimodal HMM probabilities — same
  pattern as Issue #6).
- RS sizing produced the best risk-adjusted result of any variant
  (PF 3.64, DD 4.09%, 17/20 folds positive) but cut trade count to 92
  (below the 100 gate) and return to +74.61%. Mechanism works; sample
  size blocks adoption — natural top-5 candidate.

Honest caveats:

- The funding filter affects only 6 trades. The DD-reduction
  confidence is medium-high, not certainty. The mechanism is
  consistent with Issue #7's prior; the effect is structurally larger
  on long-short because the symmetric inversion exposes the filter
  to both ends of the funding distribution.
- Issue #7's "within noise" conclusion was correct for long-only
  (2 of 65 trades affected). On long-short with 129 baseline trades,
  symmetric application catches both overheated-longs and
  squeeze-bottom-shorts; the structural effect is larger.

Live config: **UNCHANGED**. Per the hard rules in Issue #20, the
adopted research candidate is not auto-applied to live. Wiring it
into the live worker requires (a) pointing `state/live_multiasset.yaml`
at the long-short yaml AND (b) attaching the funding-decisions
DataFrame inside `multi_loop.run`. Neither is in scope for this
issue.

See `research/long_short_overlay_report.md`.

## SuperTrend long-short (Issue #19) — research-only, gate failed by 0.22 pp

Added symmetric short-side support to the SuperTrend(10, 3) strategy.
Short entry: bearish regime (EMA50 < EMA200) + SuperTrend flip from
UP to DOWN. Short exit: SuperTrend flips back UP / stop breach /
optional max hold. Implementation is three small branches in
`hermes_trading/signals.py` (`short_entry`, `initial_stop_short`,
`short_exit`) — the existing `_run_state_machine` already routes
between long and short via the position's `direction` field.

48-month walk-forward, 20 folds:

| variant | n | L | S | OOS return | DD | PF | Sharpe | folds+ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC long-only | 35 | 35 | 0 | +38.66% | 9.63% | 2.24 | 0.266 | 10/20 |
| BTC long-short | 65 | 35 | 30 | **+107.57%** | 9.98% | **2.87** | 0.353 | **15/20** |
| ETH long-only | 30 | 30 | 0 | +37.86% | 5.30% | 2.92 | 0.336 | 10/20 |
| ETH long-short | 64 | 30 | 34 | **+163.94%** | **5.30%** | **3.67** | **0.406** | **15/20** |
| BTC/ETH parallel long-only (live floor) | 65 | 65 | 0 | +39.72% | 5.54% | 2.50 | 0.296 | 11/20 |
| **BTC/ETH parallel long-short** | **129** | **65** | **64** | **+139.47%** | **5.76%** | **3.26** | **0.379** | **16/20** |

**Result: research-only, gate failed by 0.22 pp.**

Adoption gates (vs current adopted BTC/ETH parallel long-only):
- PF > 2.50 → 3.26 ✓
- DD ≤ 5.54% → **5.76%** ✗ (by 0.22 pp, ~4% relative)
- return > +39.72% → +139.47% ✓
- trades ≥ 65 → 129 ✓

3 of 4 cleared with large margins; DD gate fails by 0.22 pp. Per the
locked rules, this is a not-adoption.

The data is striking. Shorts contribute +68.91% on BTC, +126.08% on
ETH, and +99.75% on the parallel portfolio (raw direction-sum, not
final equity). The parallel long-short variant produces the **highest
risk-adjusted result measured in the project so far** by Sharpe and
fold-positivity, and the second-highest by PF (after ETH solo
long-short at 3.67). The DD increase is 0.22 pp on a 5.54% baseline —
within fold-to-fold noise.

Honest interpretation: the SuperTrend short side works. ETH's higher
ATR% (Issue #13: 2.01% vs BTC's 1.47%) gives the short the same
structural fit benefit it gives the long. The 2022 bear leg, 2024
chop, and late-2025 correction in the 48mo window all contributed
short profits — 16 of 20 folds positive on the long-short portfolio.

**Live config is unchanged per hard rules.**
`state/live_multiasset.yaml.strategy` still points at the long-only
`state/strategy_supertrend.yaml`. The new
`state/strategy_supertrend_long_short.yaml` is a research yaml only.

User-facing decision: the strict gate failed, but every metric except
DD improved massively. If the user accepts the 0.22 pp DD increase in
exchange for ~3× return and +30% PF, the live worker can be pointed
at the long-short yaml. I will not make that switch automatically —
the spec explicitly requires user approval and a strict gate met.

Recommended next research: overlay a single mechanism (RS sizing,
funding filter, or HMM filter) on the long-short variant to test
whether it can bring DD below the 5.54% gate without losing the
return / PF gains.

See `research/supertrend_short_report.md`.

## Live tick display auto-switch (Issue #17) — shipped

The live-worker per-tick output now auto-selects fields based on the
active strategy. With SuperTrend enabled (`setups.supertrend.enabled:
true`), the tick line shows SuperTrend direction, line, and distance
from price; RSI is hidden from the line but still computed, still in
the heartbeat JSON, and still in the closed-trade rows. With the
legacy v2 long-short strategy active (pullback / breakout), the tick
line is preserved byte-for-byte from before this change.

Implementation: a new pure-function module `hermes_trading/display.py`
holds the `is_supertrend_active` check, the tick formatters
(`format_supertrend_tick`, `format_rsi_tick`), and the
`supertrend_heartbeat_fields` builder. Both `loop.py` (single-asset)
and `multi_loop.py` (multi-asset) call into it; no trading logic was
touched.

Heartbeat additions per asset (and at top level in single-asset mode):
`supertrend_direction` (`"UP"` / `"DOWN"` / `None`), `supertrend_line`
(float / `None`), `supertrend_distance_pct` (signed percent / `None`).
Warmup bars correctly produce `None` so consumers can detect
indicator-not-ready without crashing.

A `--verbose` (`-v`) CLI flag appends `rsi=...` to the SuperTrend tick
line for debugging. Off by default.

Self-test extended from 27 to 49 invariants. Decay monitor self-test
unchanged at 14/14. Trade row schema unchanged — decay monitor still
passes because no legacy field was renamed or removed.

Motivation in one line: the previous tick line showed RSI as the
headline metric even when SuperTrend was the active setup, which made
the screen look like the strategy was stuck whenever RSI was flat. The
auto-switch surfaces the indicator that is actually driving entries.

## Multi-asset live paper worker (Issue #16) — shipped

Live worker refactored from single-asset to a parallel-portfolio
paper-trading harness, mirroring the research engine's
`btc_eth_reference_parallel` structure. **Single-asset mode is
preserved unchanged** — `uv run python -m hermes_trading.run` still
reads `state/goal.yaml` + `state/strategy.yaml` and behaves exactly
as before.

New: `uv run python -m hermes_trading.run --config state/live_multiasset.yaml`
runs the multi-asset path. Config carries the asset list, timeframe,
`max_open_positions`, shared strategy yaml, and circuit breaker
threshold. Per-asset position state at `state/positions/<KEY>.json`;
portfolio heartbeat at `state/heartbeat.json` (schema
`multiasset-v1`); extended trade rows in `state/trades.jsonl`
(asset, setup, entry_time/exit_time, return_pct, net_return_pct,
position_size, holding_bars — legacy fields also retained for
backward-compatibility with the decay monitor).

Architecture:
- `hermes_trading/positions.py` — IO + migration helpers shared by
  both modes (legacy single-file + per-asset multi-file layouts).
- `hermes_trading/multi_loop.py` — multi-asset orchestration with
  pure helper functions (`build_trade_row`, `can_enter`,
  `evaluate_tick`) that the self-test exercises without an exchange.
- `hermes_trading/run.py` — dispatches on `--config` (multi) vs no
  flag (single).

Safety: per-asset circuit breaker (default 5 consecutive failures
→ skip that asset for the rest of the session); worker only halts
when every asset is broken. Corrupt per-asset state files are
skipped with a warning and left in place for inspection. Legacy
migration is idempotent and backs up the original to
`state/position.json.bak.<UTC-iso>` before unlinking.

Reflection (`reflect.py`) is intentionally disabled in multi-asset
mode — its allowlist was designed for single-asset v2-shaped keys
and the interaction with a shared per-asset strategy yaml is
untested. Re-enabling it later is a separate issue.

No HMM / funding overlay wiring in this pass per spec. Both modules
exist (`hermes_trading.hmm_regime`, `hermes_trading.funding`) but are
not consumed by the live worker.

Self-test (`scripts/test_multiasset_worker.py`) — 27/27 invariants
pass. Tests cover: single-asset import sanity, config parse,
portfolio cap, per-asset cap, legacy migration with backup, corrupt
state tolerance, trade row schema, `evaluate_tick` end-to-end on a
synthetic SuperTrend bullish flip.

The live worker is **not yet redeployed** to multi-asset mode — that
is a user decision, not a research one. The single-asset worker that
was running at audit time continues to run as before.

## Live decay monitor (Issue #15) — shipped

`scripts/monitor_strategy_decay.py` reads `state/trades.jsonl`,
computes per-window metrics (PF, DD, win rate, total return,
consecutive trailing losses, average holding time, best / worst
trade) over configurable windows (default 10 / 25 / 50 trades) and
compares each to research-time baselines.

Default baselines match Issue #14's adopted
`btc_eth_reference_parallel` (PF 2.50, DD 5.54%, win 48.7%) — these
are CLI-overridable to track whatever strategy is actually live.

Warnings fire on:

- profit factor < 1.20
- win rate < 65% of baseline
- max drawdown > 125% of baseline
- consecutive trailing losses ≥ 4
- total return < 0 over the window

Output is human-readable by default; `--json` plus `--output` writes
a structured report for log aggregators. Exit codes: `0` OK,
`1` DEGRADED, `2` INSUFFICIENT_DATA. A self-test
(`--self-test`) covers 14 invariants against a fixture; pytest is
not yet a project dependency, but the monitor's core
(`build_report`, `compute_metrics`, `evaluate_warnings`) is plain
functions that a pytest test could import unchanged.

Sample run against the current `state/trades.jsonl` (10 closed
trades over the recent session): status `DEGRADED` on the
window-10 panel (PF 0.98 < 1.20 floor; total return -0.01%). This is
the kind of state the monitor was built to surface — a real signal
that the live strategy's recent trades are sub-baseline, with no
attempt to "fix" anything automatically.

This is **monitor / report only**. It does not modify trading
decisions, resize positions, or auto-disable strategies. Cron / Slack
/ Datadog integrations are deliberately out of scope and easy to add
later via the exit code or the JSON output.

See `research/decay_monitor_report.md`.

## Recent-window decay investigation (May 2026) — research

After a visibly bad recent 3-month live-replay window (user-reported
6 trades, -9.61%, DD 9.61% over Feb-Apr 2026), this multi-phase
investigation revalidated the adopted candidate, tested whether the
bad run was decay or noise, and tested 13 possible adaptations.
Headline: **the strategy is not decayed.** The recent window sits at
~p21 of the historical 90-day distribution; the worst windows in the
48-month history were -10.31% (late-2022 chop) and the strategy
recovered from those. Walk-forward OOS on the SAME 48 months
reproduces Issue #20 exactly: 123 trades, +139.71%, DD 4.64%, PF 3.35.

Phases:

1. **Phase 1 — forensic 3-month diagnostic** (`research/recent_regime_failure_report.md`,
   `results/recent_regime_failure_trades_20260531_072648.csv`,
   `scripts/diagnose_recent_regime.py`).
   10 trades in the window, 100% stop exits, PF 0.105, net -6.55%.
   6 of 10 entries fell in the HIGH realised-vol band (above the
   prior 6-month upper quartile). 9 of 10 had 1h SuperTrend
   agreeing with the 4h entry direction — alpha was internally
   consistent. Funding gate had nothing to block (percentile range
   9.7-72.8). The losses were straightforward chop, not signal
   decay.

2. **Phase 2 — rolling decay over 48 months** (`research/rolling_decay_report.md`,
   `results/rolling_decay_metrics_20260531_072839.csv`,
   `results/rolling_decay_trades_20260531_072839.csv`,
   `scripts/run_rolling_decay.py`).
   90-day windows: mean -0.69%, p25 -3.74%, fraction below -5% is
   20.6%, fraction below -9.61% is 1.5%. Recent 90d return is
   -2.78% (continuous replay), comfortably within historical
   distribution. The decay monitor's defaults (PF<1.20,
   trailing-CL≥4) would have fired on the recent window — and
   on 54% and 26% of historical windows respectively. The
   existing thresholds are too noisy to drive a binary "stop
   trading" alarm; the recommended pivot is to use a continuous
   sizing signal (Phase 5 R6) instead of a binary monitor verdict.

3. **Phase 3 — timeframe comparison** (`research/timeframe_comparison_report.md`,
   `results/timeframe_comparison_20260531_073039.{csv,md}`,
   `results/timeframe_trades_20260531_073039.csv`,
   `scripts/run_timeframe_comparison.py`).
   Tested 1h, 2h, 4h, 1d on the same long-short + funding
   strategy. 4h dominates on PF (3.35 vs 2.10-2.26 on faster
   TFs, 2.19 on 1d) and DD (4.64% vs 7.19-8.25%, 15.13%). On the
   recent 3mo all four TFs are positive in walk-forward OOS;
   2h is best (+18.91% / DD 1.73%) but its full-window DD is
   nearly 2× worse than 4h. **Verdict: 4h stays.**

4. **Phase 4 — MTF confirmation variants** (`research/multitimeframe_confirmation_report.md`,
   `results/multitimeframe_confirmation_{20260531_073251}.{csv,md}`,
   `scripts/run_multitimeframe_confirmation.py`).
   Tested A (1d agreement filter), B (1h agreement filter),
   C (agreement-scaled size), D (1h early-warning exit). All four
   fail Issue #20 adoption gates. D is catastrophic (PF 1.54).
   A is the best of the rest but gives up 27% of return for a
   modest DD improvement. **Verdict: no MTF confirmation.**

5. **Phase 5 — adaptive risk-layer rules** (`research/adaptive_regime_response_report.md`,
   `results/adaptive_regime_response_{20260531_073505}.{csv,md}`,
   `results/adaptive_regime_response_trades_20260531_073505.csv`,
   `scripts/run_adaptive_regime_response.py`).
   Tested 6 rules: R1 PF<1.0 pause, R2 3-consec-loss half-size,
   R3 30d-return half-size, R4 stop-freq half-size, R5 HMM-adverse
   half-size, R6 vol-quartile sizing. **R6 dominates**: 48mo PF
   3.35 → 4.63, DD 4.64% → 2.10%, return -48%; recent 3mo PF 2.36
   → 3.70, DD 4.40% → 1.44%, return -39%. R6 also reproduces the
   Issue #27 finding exactly. R5 is the runner-up. R2 is a
   nearly-free soft circuit breaker. R1 / R3 / R4 are rejected.

6. **Phase 6 — final synthesis** (`research/recent_adaptation_final_report.md`).
   The recommended action is to **switch the live worker config**
   to `state/live_multiasset_long_short_funding_vol.yaml` (the
   Issue #33 opt-in yaml). No code change. No alpha change. The
   sizing layer was already built and tested under Issues #27
   and #33; this investigation provides the empirical case for
   the user to flip the switch.

Hard rules respected throughout: no live config edits, no
strategy edits, no parameter tuning on full history, all
evaluations walk-forward (train=1440 / test=360 / embargo=6 for
4h; scaled for other TFs), fee=0.001/side + slippage=0.0005,
same SuperTrend(10,3), same BTC+ETH universe, same direction-aware
funding gate.

New research scripts (read-only):
`scripts/diagnose_recent_regime.py`, `scripts/run_rolling_decay.py`,
`scripts/run_timeframe_comparison.py`,
`scripts/run_multitimeframe_confirmation.py`,
`scripts/run_adaptive_regime_response.py`.

`signals.py` unchanged. Live configs unchanged. `py_compile`
clean. Test trio (multi-asset worker + decay monitor self-test +
py_compile) passes.

## Volume confirmation filter (Issue #35) — research

Tested a single entry-filter overlay on top of the adopted live
candidate (`state/live_multiasset_long_short_funding_vol.yaml`):
``volume_at_signal_bar >= volume.rolling(20).mean()`` on the
SuperTrend flip bar. Same rule applied to long and short. Locked
threshold; no tuning. Filter applies at the runner level
(`scripts/run_volume_confirmation.py`); `signals.py` and the live
worker untouched.

48-month walk-forward OOS, BTC + ETH 4h, train=1440 / test=360 /
embargo=6, fee=0.001/side, slippage=0.0005, vol_sizing on with the
locked Q1/Q2-Q3/Q4 ladder and trailing 12-month quartile lookup.

| variant | trades | OOS return | max DD | PF | win% | stop% | mean mult |
|---|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_vol` | 123 | +73.34% | 2.02% | 4.53 | 58.5% | 94.3% | 0.551 |
| `+ volume_conf` | 104 | +74.67% | 1.34% | 5.79 | 62.5% | 94.2% | 0.550 |

Recent 3mo: 10 → 5 trades, +3.57% → +3.43%, PF 2.87 → 5.64, DD
1.52% → 0.74%. Recent 6mo: 23 → 15 trades, return preserved
(+12.30% → +12.31%), PF 5.84 → 11.89.

Verdict: **YES on the adoption question**. The filter removes 19
of 123 trades (15.4%); preserves total return at +74.67% (Δ
+1.33pp); cuts DD by 0.68pp; lifts PF from 4.53 to 5.79; lifts
win-rate by 4 pp. All filtered-out windows reduce risk; none
materially reduce return. Passes the ROADMAP adoption gate
(PF > 1.69 AND trades >= 30).

Files:
`scripts/run_volume_confirmation.py`,
`scripts/_supertrend_overlay_lab.py`,
`research/volume_confirmation_report.md`,
`results/volume_confirmation_comparison_<ts>.{csv,md}`,
`results/trades_volume_confirmation_<ts>.csv`.

## ADX trend-strength gate (Issue #36) — research

Tested ``ADX(14) >= 20`` as an entry gate on the same adopted
candidate. ADX implemented inline in `scripts/run_adx_gate.py`
(Wilder's smoothed True Range + DI±, alpha=1/14). Same threshold
applied to long and short. Locked; no tuning.

| variant | trades | OOS return | max DD | PF | win% | stop% | mean mult |
|---|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_vol` | 123 | +73.34% | 2.02% | 4.53 | 58.5% | 94.3% | 0.551 |
| `+ adx` | 63 | +35.32% | 1.21% | 4.62 | 57.1% | 92.1% | 0.504 |

Recent 12mo: 37 → 18 trades, +22.05% → +12.28%, PF 6.15 → 5.60
(lower!). Recent 3mo: 10 → 8 trades, +3.57% → +3.14%.

Verdict: **NO on the adoption question**. ADX(14)>=20 prunes
60/123 trades (-48.8%) — almost half the strategy is filtered out.
PF moves marginally (4.53 → 4.62, +0.09) but absolute return
collapses (-38.02pp, -51.8%) and ret/exp halves (+133.14% →
+70.08%). The core edge is NOT preserved; the SuperTrend flip
itself already captures the trend strength signal ADX is
attempting to measure, so ADX is redundant and over-restrictive
at the standard 20 threshold. Passes the bare ROADMAP gate
(PF > 1.69, trades >= 30), but fails Issue #36's
"preserving the core edge" half of the adoption question.

Files:
`scripts/run_adx_gate.py`,
`research/adx_gate_report.md`,
`results/adx_gate_comparison_<ts>.{csv,md}`,
`results/trades_adx_gate_<ts>.csv`.

## Body-to-range confirmation (Issue #37) — research

Tested a candle-quality entry filter: at the signal bar require
``abs(close-open) / max(high-low, 1e-12) >= 0.50`` AND
direction-consistent body sign (long: close>open; short: close<open).
Locked; no tuning.

| variant | trades | OOS return | max DD | PF | win% | stop% | mean mult |
|---|---:|---:|---:|---:|---:|---:|---:|
| `baseline_funding_vol` | 123 | +73.34% | 2.02% | 4.53 | 58.5% | 94.3% | 0.551 |
| `+ body_range` | 104 | +67.68% | 1.33% | 5.54 | 61.5% | 94.2% | 0.550 |

Recent 12mo: 37 → 32 trades, +22.05% → +23.40%, PF 6.15 → 9.05.
Recent 3mo: 10 → 9 trades, +3.57% → +3.78%, PF 2.87 → 3.19.

Verdict: **YES on the adoption question**. The filter removes 19
of 123 trades (15.4%); gives up 5.65pp of total return (-7.7%);
cuts DD by 0.69pp (-34%); lifts PF from 4.53 to 5.54; lifts
win-rate by 3 pp. The trim is concentrated in 48mo (-5.65pp); on
all recent windows return is preserved or improved. Passes the
ROADMAP adoption gate (PF > 1.69 AND trades >= 30) and the spec's
"without over-filtering the strategy" check.

Files:
`scripts/run_body_range_confirmation.py`,
`research/body_range_confirmation_report.md`,
`results/body_range_confirmation_comparison_<ts>.{csv,md}`,
`results/trades_body_range_confirmation_<ts>.csv`.

Common methodology notes for Issues #35 / #36 / #37:

- All three runners share `scripts/_supertrend_overlay_lab.py`,
  a private support module that implements the walk-forward
  parallel coordinator, the vol_sizing overlay (Issue #27 / #33
  trailing-12mo quartile lookup matching `LiveVolSizingOverlay`),
  and the funding hard gate (Issue #20 / #21).
- The baseline numbers above (123 trades, +73.34%, DD 2.02%, PF
  4.53) differ from the Issue #27 `run_adaptive_sizing.py`
  numbers (123 trades, +72.71%, DD 2.10%, PF 4.63) because this
  runner uses the LIVE worker's trailing-12mo quartile lookup
  rather than the per-fold-train-slice variant. Trade count and
  signal universe are identical; only the per-bar multiplier
  assignment differs slightly because the quartile boundaries
  are computed over a 12-month rolling window vs the 7.6-month
  per-fold-train window. This choice keeps the baseline
  directly comparable to the operator's live config
  (`state/live_multiasset_long_short_funding_vol.yaml`).
- All three filters share the same 48-month span, same
  fees/slippage, same fold geometry, and same Issue #29 fill
  model. No yaml writes; no live worker changes; `signals.py`
  unchanged.
- `py_compile`, `scripts/test_multiasset_worker.py`, and
  `scripts/monitor_strategy_decay.py --self-test` all pass.

## Recommended next experiment

The diminishing-returns pattern across Issues #5-#7 still holds —
no clear research next step argues for itself. Two reasonable
candidates from `ROADMAP.md`:

1. **Volatility-compression breakout** — Phase-3 audit found the
   `med-low` ATR bucket had PF 2.84 on the v2 strategy. A focused
   experiment on entries restricted to that bucket is the highest-prior
   research candidate not yet tested.
2. **Stacking HMM + funding** as a single experiment to formally
   confirm or refute redundancy. Likely confirms redundancy, but
   the result is publishable either way.

Both are lower priority than user-driven decisions about live
deployment of the existing adopted candidates.
