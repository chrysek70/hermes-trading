# Why did ETH SuperTrend outperform BTC SuperTrend? — Diagnostic Analysis

Issue #13. Research-only diagnostic on the 48-month 4h SuperTrend(10, 3)
walk-forward results from Issue #12. No strategy changes; the analysis
adds diagnostic measurements (ADX, run-length, autocorrelation) for
measurement purposes only, never wired into trading logic.

## Headline answer

**Trend structure is nearly identical between BTC and ETH on this
window. The headline gap is mostly a win-rate gap (45.7% vs 63.3%)
that is statistically borderline, not nailed-down. The single
structural difference that does survive scrutiny is ATR percentage:
ETH is ~36% more volatile per bar (2.01% vs 1.47%), which gives the
SuperTrend(10, 3) band a structural fit advantage on ETH. Rotation
selectors that try to capture the difference *hurt* — both tested
selectors produced worse PF and DD than ETH solo.**

**Recommendation for next experiment: HMM (Issue #6), not a broader
top-5 rotation.** The data argues against rotation, not for it.

---

## Q1 — Trade diagnostics

| metric | BTC | ETH | delta |
|---|---:|---:|---:|
| trades | 35 | 30 | -5 |
| avg winner | +3.98% | +2.71% | -1.27 pp |
| avg loser | -1.50% | -1.60% | -0.10 pp |
| win rate | 45.7% | 63.3% | **+17.6 pp** |
| profit factor | 2.24 | 2.92 | +0.68 |
| expectancy per trade | +1.01% | +1.13% | +0.12 pp |
| avg holding bars | 26.4 | 24.7 | -1.7 |
| median holding bars | 20 | 19.5 | -0.5 |
| exits via SuperTrend flip (`stop`) | 91.4% | 90.0% | similar |
| exits at fold boundary (`end`) | 8.6% | 10.0% | similar |
| time-stop / trail-stop / target | 0 | 0 | n/a |

Three things to notice:

1. **The win-rate gap is the entire mechanism.** ETH's *average winner
   is actually smaller* (+2.71% vs +3.98%) and its average loser is
   *slightly worse* (-1.60% vs -1.50%). The PF gain comes purely from
   ETH winning more often. There is no "ETH catches bigger trends"
   story in the numbers.
2. Both assets have effectively identical exit profile — ~90% via
   SuperTrend flip, ~10% closed at fold boundary. No exits triggered
   by trail / time / target. Holding times are the same.
3. Expectancy per trade differs by only 12 bps. On 30-35 trades, that
   compounds to a much smaller difference than the PF gap suggests.

## Per-fold consistency — calibrating the luck question

| metric | BTC | ETH |
|---|---:|---:|
| mean fold return | +1.73% | +1.69% |
| fold-return std | 4.10% | 3.98% |
| folds where this asset won | 8 / 20 | 10 / 20 |
| ties | 2 / 20 | 2 / 20 |

At the per-fold level the two assets are **essentially tied**: nearly
identical mean and dispersion, and out of 20 folds BTC won 8 / ETH
won 10 / 2 tied. That is a coin flip. The 8-vs-10 split has a binomial
p-value of ~0.5 under H0 of equal performance.

The headline metrics (PF, DD, win rate) look more different than this
because they accumulate the per-trade win-rate gap across the stitched
sample without dividing by per-fold dispersion. A crude two-proportion
z-test on the win rates (BTC 16/35 = 45.7%, ETH 19/30 = 63.3%) gives
z ≈ 1.4 — at the edge of significance, not over the standard 2σ bar.

**Reading this honestly: ETH's outperformance is plausibly real but
not statistically nailed down. It is at the strength one would expect
from a true small edge plus moderate luck.**

## Q2 — Trend quality (full 48mo window)

| metric | BTC | ETH | delta |
|---|---:|---:|---:|
| SuperTrend flips | 205 | 209 | +4 (≈ 2%) |
| run count | 206 | 210 | similar |
| mean run length (bars) | 42.6 | 41.7 | -0.9 |
| median run length | 34 | 33 | -1 |
| short runs (≤ 6 bars) share | 6.8% | 5.7% | -1.1 pp |
| long runs (≥ 12 bars) share | 84.5% | 83.3% | similar |
| % time in long runs (trending) | 97.4% | 97.1% | similar |
| ADX mean | 28.1 | 27.7 | -0.4 |
| ADX median | 25.2 | 24.7 | -0.5 |
| % time ADX > 25 | 50.7% | 48.9% | -1.8 pp |
| **ATR % mean** | **1.47%** | **2.01%** | **+0.54 pp (+36%)** |
| ATR % median | 1.35% | 1.83% | +0.48 pp |
| ATR % std | 0.63% | 0.88% | +0.25 pp |

**This is the most striking finding of the analysis.** Every trend-
quality metric except ATR percentage is essentially identical. BTC and
ETH trend the same number of times, for the same length, at the same
ADX level. There is no "ETH trends cleaner" — the trend structures are
indistinguishable.

The single survivor is **ATR percentage**: ETH is ~36% more volatile
per bar than BTC. This matters because SuperTrend(10, 3) places its
band at `multiplier × ATR` away from price. A higher ATR%, applied to
the same multiplier, gives a structurally wider band in percentage
terms. That wider band:

- absorbs more intra-trend noise without flipping (consistent with
  ETH's marginally lower short-runs share, 5.7% vs 6.8%)
- catches a smaller percentage of trend amplitude (consistent with
  ETH's *smaller* average winner, +2.71% vs +3.98%)
- but gets caught in *fewer* false breakouts (consistent with the
  higher win rate, 63.3% vs 45.7%)

This is the structural explanation for the PF gap: SuperTrend(10, 3)
parameters were chosen for the volatility profile that ETH happens to
match better than BTC over this window.

## Q3 — Market structure (full window)

| metric | BTC | ETH |
|---|---:|---:|
| return autocorr lag-1 | -0.001 | +0.003 |
| return autocorr lag-5 | -0.013 | -0.020 |
| return autocorr lag-24 | +0.003 | -0.014 |
| **|return| autocorr lag-1 (vol clustering)** | **0.208** | **0.221** |
| |return| autocorr lag-5 | 0.161 | 0.159 |
| |return| autocorr lag-24 | 0.091 | 0.117 |
| return skew | +0.025 | -0.106 |
| return kurtosis | 7.40 | 8.45 |
| disjoint drawdowns ≥ 5% | 71 | 30 |
| max drawdown depth | -60.5% | -69.0% |
| median ≥5% drawdown duration | 4 bars | 9 bars |

Return autocorrelation is essentially zero for both at every lag — no
mean reversion, no momentum signature at the bar level. Volatility
clustering (autocorrelation of `|return|`) is also very similar.

The drawdown structure is where the assets differ most: BTC has more
than twice as many disjoint ≥5% drawdowns (71 vs 30), but ETH's
drawdowns last longer (9 vs 4 bars median). BTC chops more frequently;
ETH dips less often but for longer when it does. This is consistent
with the win-rate explanation: BTC's higher frequency of small
drawdowns is exactly what catches a 1.5×ATR SuperTrend stop.

Note: drawdown counting here is over the raw price series, not the
strategy equity. The disjoint-drawdown count depends on threshold
ordering and shouldn't be over-interpreted; the direction (BTC has
many shallow drawdowns, ETH has fewer but longer ones) is the
meaningful finding.

## Q4 — Rotation simulation

Two simple per-bar selectors run in walk-forward (same fold geometry).
At each bar where both BTC and ETH have a SuperTrend long signal, the
selector picks the higher-scoring asset and trades it. No new
indicators added.

| variant | n | OOS return | max DD | PF | Sharpe | win % | folds+ | by asset |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| BTC solo (reference) | 35 | +38.66% | 9.63% | 2.24 | 0.266 | 45.7% | 10/20 | BTC:35 |
| **ETH solo (reference)** | 30 | +37.86% | **5.30%** | **2.92** | **0.336** | **63.3%** | 10/20 | ETH:30 |
| `rotation_supertrend_distance` | 47 | +48.54% | 10.09% | 2.12 | 0.237 | 46.8% | 10/20 | BTC:27; ETH:20 |
| `rotation_rs_score` | 47 | +45.64% | 10.09% | 2.01 | n/a | n/a | n/a | BTC:29; ETH:18 |

**Both selectors HURT vs ETH solo.** They get more trades (47 vs 30)
and higher absolute return (~+46-48% vs +37.86%), but PF degrades to
2.12 / 2.01 (vs ETH's 2.92), DD almost doubles to 10.09% (vs 5.30%),
and Sharpe drops.

The mechanism is clear from the trade-count math: BTC and ETH SuperTrend
fired together on ~18 bars (since the rotation has 47 trades vs solo
35 + 30 = 65, the overlap is 65 - 47 = 18). On those overlapping bars
the rotation forces a choice between two valid signals. Whichever
heuristic it uses (SuperTrend distance / ATR, or RS score), it ends
up picking BTC about 60% of the time (27/47 and 29/47) — and BTC's
trades have worse PF on average. So the forced selection systematically
trades ETH's good signals for BTC's worse ones.

**The cleanest reading: there is no per-bar "which asset is better
right now" signal in the existing SuperTrend or RS information.**
Both selectors degrade results vs simply running both assets in parallel
(or running ETH alone). A rotation overlay on top of SuperTrend would
need genuinely new asset-quality information (which is by definition a
new indicator — out of scope for this analysis).

## Answers to the spec's final questions

### 1. Why did ETH beat BTC?

A 36% higher ATR percentage on ETH, applied to the same SuperTrend
multiplier (3.0×), gave a structurally wider band that survived more
intra-trend noise without false flips. This shows up as a higher win
rate (+18 pp) rather than larger winners — in fact ETH's average
winner was *smaller* than BTC's. The trend structure (flip count,
run length, ADX, % time trending) was otherwise identical.

The PF gap (2.24 → 2.92) compounds from the per-trade win-rate gap;
there is no other mechanism in the diagnostics.

### 2. Was it luck?

**Partially.** Per-fold returns were essentially tied:

- BTC mean fold return +1.73% σ 4.10%
- ETH mean fold return +1.69% σ 3.98%
- BTC better on 8 folds, ETH better on 10, 2 ties

The 18-pp win-rate gap is at the edge of statistical significance on
this sample (z ≈ 1.4). It is consistent with a true small edge plus
moderate luck. The headline PF / DD differences look bigger than this
suggests only because they aggregate without per-fold scaling.

Honest call: the structural ATR% finding is real. The magnitude of
ETH's outperformance is partly that real edge and partly fold-sample
noise.

### 3. Was it structural?

**Yes, but the structure is "ATR percentage matches SuperTrend(10, 3)
better on ETH than BTC over this window" — not a deeper trend-quality
difference.** If BTC's ATR% normalised up (or ETH's down) the gap
should shrink. The trend-structure metrics (flip frequency, run length,
ADX, trending %) are statistically indistinguishable.

A direct testable prediction: tuning SuperTrend multiplier upward on
BTC (e.g. 3.0 → 4.0) should narrow the gap by giving BTC a similarly
wide band. This is an obvious experiment but **out of scope here per
hard rules — no parameter tuning**.

### 4. Would a selector have improved performance?

**No.** Both tested selectors (SuperTrend distance / ATR and RS score)
produced strictly worse PF and DD than ETH solo. The total return
went *up* (+46-48% vs +38%), but only by including more trades, with
overall worse risk-adjusted profile. The data says there is no
per-bar quality signal hidden in existing SuperTrend + RS info that
distinguishes "this BTC signal is better than this ETH signal".

This is the result that should most influence the next decision.

### 5. Top-5 rotation (A) or HMM (B) — based on evidence?

**Recommend HMM (option B).**

Reasons grounded in this analysis:

- The rotation result (Q4) directly shows that adding a selection
  mechanism on top of SuperTrend hurts on the BTC-ETH universe. A
  top-5 rotation amplifies this same selection problem onto 5 assets.
  Without a NEW asset-quality signal (which by definition is new
  parameter / indicator territory), top-5 is likely to repeat the
  pattern: more trades, lower PF, higher DD.
- The trend-structure result (Q2) shows BTC and ETH are
  near-indistinguishable on every flip / run / ADX metric. There is
  no strong reason to expect SOL / AVAX / LINK to be qualitatively
  different in *trend structure* — they may differ in ATR% (and
  therefore SuperTrend fit), but at that point we are running a
  parameter-fit experiment per asset, not a rotation strategy.
- HMM (Issue #6) tests an orthogonal hypothesis: that latent
  EM-fit market regimes inform when to be exposed at all, regardless
  of which asset. This is the mechanism the existing Markov layer was
  supposed to deliver and failed to (under-sampled hand-defined states).
  Soft probabilities over latent states would feed exposure scaling,
  not asset selection — sidestepping exactly the selection failure
  that hurt every rotation variant tested here.

Caveat: top-5 universe expansion *could* still be useful as a
trade-count-only experiment (build a portfolio of 5 assets running in
parallel, no rotation, sized down per asset). That is a different
experiment than what the spec asks; it does not contradict the HMM
recommendation. If both get queued, HMM is the higher-priority next.

## Files

- `scripts/run_eth_vs_btc_analysis.py` (reproducible runner)
- `results/eth_vs_btc_comparison_20260530_121820.csv` (raw metrics)
- `results/eth_vs_btc_comparison_20260530_121820.md` (raw-numbers MD)
- `results/.eth_vs_btc_data_20260530_121820.json` (machine-readable)

## Closing-the-loop summary

- **Issue #13 status:** closed, analysis complete.
- **Headline:** ETH's PF advantage over BTC is a win-rate effect
  driven by ETH's higher ATR% interacting with SuperTrend(10, 3)'s
  fixed multiplier. Trend structure is identical between the two
  assets. The gap is at the edge of statistical significance and
  fold-wise basically tied (8 vs 10 of 20 folds).
- **Selectors hurt.** Two rotation variants produced strictly worse
  risk-adjusted results than ETH solo.
- **Next per evidence:** Issue #6 (HMM regime detector), not top-5
  rotation. Top-5 may still be a valid future experiment as a
  parallel portfolio (not a rotation), but HMM tests the more
  promising orthogonal hypothesis.
- **Live worker:** unchanged.
