# BTC/ETH Relative-Strength Experiment — Report

Issue #5. Tests whether ETH used as **market context only** (not a traded
asset) improves SuperTrend(10, 3) BTC trading decisions on 48-month
walk-forward.

**Hard rules respected:**

- No live wiring; `hermes_trading/loop.py` untouched.
- No parameter tuning — RS windows fixed at the spec's
  `lookback=30, ratio_ema=30, min_btc_minus_eth_return=0.0,
  require_ratio_above_ema=true`. Not swept.
- Walk-forward OOS only. RS features are causal (close[i]/close[i-30]).
- Same fees/slippage as all prior experiments (10 bps/side + 5 bps slip).
- Same fold geometry as Issue #11 (train 1440 / test 360 / embargo 6, 20 folds).
- ETH 48mo loaded from Binance Vision, aligned 1:1 with BTC bars (8766 bars).

**Adoption criteria (from spec):**

- (a) Must beat `supertrend_only` PF 2.24, **OR**
- (b) Must meaningfully reduce max DD while keeping PF > 2.24 **and**
  trade count ≥ 30.

**Result: NOT ADOPTED.** Both RS variants beat clause (a) on PF
(3.33 / 3.01 vs 2.24) and clause (b) on DD (7.07% / 6.29% vs 9.63%),
but **neither maintains the 30-trade gate** (20 / 27 trades vs 35).

The project-wide trade-count discipline — the same gate that
correctly refused adoption of supertrend_only on 24-month history
(PF 9.02 on 9 trades) and supertrend_plus_routing on 48-month
(PF 3.16 on 20 trades) — is the blocker. The locked criteria do not
permit waiving the count gate just because PF cleared the new floor.

The RS thesis itself is materially supported by the data, however
(see "What worked" below). The honest classification is "validated
mechanism, sample-blocked from adoption", not "rejected".

---

## Walk-forward results (48 mo BTC 4h, 20 folds, embargo 6)

| variant | folds | n | OOS return | max DD | PF | Sharpe | win % | folds+ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_v2` | 20 | 103 | +3.28% | 12.74% | 1.09 | 0.027 | 25.2% | 8/20 |
| `supertrend_only` | 20 | **35** | **+38.66%** | 9.63% | 2.24 | 0.266 | 45.7% | 10/20 |
| `supertrend_with_btc_eth_rs_filter` | 20 | 20 | +35.43% | **7.07%** | **3.33** | **0.384** | **55.0%** | 8/20 |
| `supertrend_with_btc_eth_rs_sizing` | 20 | 27 | +38.03% | **6.29%** | 3.01 | 0.338 | 48.1% | **10/20** |

By RS state (post-warmup, 8736 of 8766 aligned bars):
`rs_strong: 4193 (48.0%)`, `rs_weak: 3070 (35.1%)`, `rs_partial: 1473 (16.9%)`.

By trade-time RS state (each variant):

- **filter mode** allowed 20 entries, all in `rs_strong` (gate is binary).
- **sizing mode** allowed 27 entries — 20 in `rs_strong`, 7 in `rs_partial`
  (sized 0.5×), 0 in `rs_weak` (sized 0 → blocked).

So roughly 43% of SuperTrend's 35 baseline signals fired in `rs_strong`
(20 of 35), 20% (7 of 35) in `rs_partial` (one gate passing), and 23%
(8 of 35) in `rs_weak` (neither gate passing).

## Answers to the report's required questions

### 1. Did BTC/ETH RS improve SuperTrend OOS PF?

**Yes — by a large margin in both modes.** Filter mode lifted PF
2.24 → 3.33 (+49%). Sizing mode lifted it 2.24 → 3.01 (+34%). Win rate
also improved 45.7% → 55.0% (filter) and → 48.1% (sizing). The signal
quality of the trades that pass the RS gate is materially better than
the unfiltered SuperTrend stream.

### 2. Did it improve drawdown?

**Yes, substantially.** Max DD dropped 9.63% → 7.07% (filter, -27%) and
→ 6.29% (sizing, -35%). Sizing mode produces the lowest DD of any
SuperTrend variant in this repo. Sharpe rose in lockstep (0.266 →
0.384 filter / 0.338 sizing).

### 3. Did it reduce trade count too much?

**Yes — and this is the blocker.** Filter mode dropped 35 → 20 trades
(-43%). Sizing mode dropped to 27 (-23%, since `rs_partial` entries are
kept at half size, only `rs_weak` is blocked). Neither variant clears
the 30-trade gate. This is exactly the discipline rule that protects
against adopting a high-PF filtered subset on too-small a sample.

### 4. Did filter mode or sizing mode work better?

Different tradeoffs, neither dominates:

- **Filter** has the highest PF (3.33), highest win rate (55.0%), and
  highest Sharpe (0.384). It also cuts the most trades (20).
- **Sizing** has the lowest DD (6.29%), highest fold positivity (10/20),
  best preservation of total return (+38.03% vs supertrend_only +38.66%),
  and the most trades (27). It is gentler.

For research purposes sizing is the more honest variant: it expresses
the RS information as a continuous overlay (1.0 / 0.5 / 0.0) rather than
a binary cut, and it preserves more sample. Filter is sharper but
throws away information. **For live consideration (if ever), sizing
would be the safer recommendation.** Neither is adopted yet.

### 5. Did it help during weak BTC regimes?

The 8 trades that SuperTrend would have fired in `rs_weak` (both gates
failing) are exactly the ones the RS layer blocks. Comparing the full
35-trade SuperTrend stream to the 20-trade filter stream:

- Returns dropped only 38.66% → 35.43% (i.e. the 15 filtered trades
  net to roughly +3.2% — essentially flat / slightly positive in
  aggregate).
- But DD dropped 9.63% → 7.07%, meaning the filtered trades were
  contributing meaningfully to the drawdown path even though they
  net out small.
- Win rate jumped 45.7% → 55.0%, meaning the kept trades have a much
  cleaner profile.

The pattern is consistent with the thesis: SuperTrend fires on BTC
flips, but BTC flips that occur **without** crypto-wide confirmation
(ETH weak or ratio below trend) are lower-quality — they still produce
some winners but with worse risk profile.

### 6. Did it hurt during BTC-only breakouts?

By construction, yes — that's exactly the subset RS blocks. The 15
filtered trades from filter mode (and 8 fully blocked from sizing
mode) include BTC moves where ETH didn't confirm. Some of those would
have been winners (the +3.2% net residual return). The question is
whether the DD reduction is worth the lost upside.

On this data: the DD reduction (-2.6pp filter, -3.3pp sizing) is
worth more than the return loss (-3.2pp filter, -0.6pp sizing) on a
risk-adjusted basis — Sharpe goes up cleanly in both cases. So the
answer is "yes it costs some BTC-only upside, but the risk-adjusted
trade is favorable". This is not the kind of damage that should
disqualify the mechanism.

### 7. Should it be adopted, rejected, or extended?

**Extended.** Not adopted (fails the trade-count discipline gate), not
rejected (the mechanism is clearly real and the risk-adjusted result
is favorable). The natural extension is the one the spec already
points at: apply the SuperTrend + RS framework to trade ETH as well,
which roughly doubles the sample and would let the routing/RS overlays
clear the count gate cleanly.

That is a **new issue** (call it #12: "SuperTrend + RS on BTC and ETH
combined"), not a continuation of #5 which closes per its own
criteria.

### 8. What is the next issue after this?

Per spec, on failure the queue advances to **Issue #6 — HMM 2-state
regime detector**. That remains valid.

However, the data argues that the **stronger** next experiment is the
multi-asset extension above (SuperTrend + RS on BTC and ETH). Doing
that first would either confirm the RS thesis to adoption level (sample
roughly doubles → trade count clears 30) or refute it cleanly on a
proper sample. HMM is a separate mechanism that doesn't share this
benefit.

**Recommendation:** queue order becomes #5 follow-up (multi-asset
SuperTrend + RS) → #6 HMM → #7 funding-rate filter. The two new
mechanisms (RS context, HMM latent regimes) are orthogonal and can be
researched in either order; doing the RS follow-up first is favored
because it has the stronger prior at this point.

## Why this is not a parameter tune

Per Issue #5 hard rules, the spec's fixed config was used verbatim:
`lookback_bars=30`, `ratio_ema=30`, `min_btc_minus_eth_return=0.0`,
`require_ratio_above_ema=true`. No sweeps over window length, no
re-fitting on train data per fold, no asymmetric thresholds. The RS
state distribution sanity-check (`rs_strong 48% / rs_partial 17% /
rs_weak 35%`) is in the expected ballpark for a symmetric ratio-vs-EMA
gate, indicating the windows are reasonable defaults rather than
boundary-cases.

## Causality / leakage check

RS features at bar `t` use closes through bar `t` only:

- `btc_return_n[t] = close_btc[t] / close_btc[t-30] - 1` — strictly past.
- `btc_eth_ratio_ema[t]` — standard EMA with `min_periods=30`, recursive
  on past values only.

The RS decisions DataFrame is computed on the **full** aligned BTC index
**once**, then sliced per-fold. This is OOS-safe because no parameter is
fit on train data — the windows are constants. No fold's RS decisions
depend on bars from the following fold.

## Artifacts

- `results/btc_eth_rs_comparison_20260530_093632.csv`
- `results/btc_eth_rs_comparison_20260530_093632.md`
- `results/trades_btc_eth_rs_detailed_20260530_093632.csv` (gitignored;
  82 trades across the two SuperTrend + RS variants and supertrend_only)
- `hermes_trading/relative_strength.py` (new module)
- `state/strategy_supertrend_rs.yaml` (new config; RS block only)

## Closing-the-loop summary

- **Issue #5 status:** closed, criteria not met (trade-count gate).
- **Headline:** BTC/ETH RS materially cleans the SuperTrend signal —
  PF +49% / DD -27% (filter) or PF +34% / DD -35% (sizing) — but at the
  cost of dropping below the 30-trade discipline gate (20 / 27 vs 35).
  Validated mechanism, sample-blocked from adoption.
- **Live worker:** unchanged. Continues to run v2 long-short.
- **Next per spec:** Issue #6 HMM 2-state regime detector.
- **Recommendation (separate from strict spec):** prioritise a new
  follow-up issue applying SuperTrend + RS to ETH-as-traded-asset
  (multi-asset). That sample-doubling step is the cleanest way to
  resolve the count-gate question this experiment leaves open.
