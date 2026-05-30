# Multi-asset SuperTrend + BTC/ETH Relative-Strength — Report

Issue #12. Tests whether expanding the SuperTrend(10, 3) universe to
BTC and ETH together (one-position-at-a-time portfolio) clears the
30-trade gate that the BTC-only RS experiment fell short on (Issue #5).
Same code, same parameters, same fold geometry, same costs.

**Adoption criteria (locked at experiment start):**

- trade count ≥ **30**
- PF > **2.24** (beat BTC `supertrend_only`)
- max DD ≤ **9.63%** (no worse than BTC `supertrend_only`)
- fold consistency not worse than BTC `supertrend_only` (10/20)

**Result: ADOPTED as research candidate.**
`multiasset_supertrend_rs_one_position` clears all four gates:
**39 trades, PF 2.48, max DD 9.61% (by 0.02pp), 12/20 folds positive.**
Live worker remains unchanged.

There is also a **surprise side finding the spec did not ask about but
the data makes loud**: `eth_supertrend_only` (ETH alone, no RS overlay)
also clears every gate, with materially better risk-adjusted profile
than either BTC variant. Recorded as a parallel adopted research
candidate.

---

## Walk-forward results (48mo BTC+ETH 4h, 20 folds, embargo 6)

| variant | folds | n | OOS return | max DD | PF | Sharpe | win % | folds+ | fold σ | by asset |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `btc_supertrend_only` | 20 | 35 | +38.66% | 9.63% | 2.24 | 0.266 | 45.7% | 10/20 | 4.10% | BTC:35 |
| `btc_supertrend_rs_sizing` | 20 | 27 | +38.03% | 6.29% | 3.01 | 0.338 | 48.1% | 10/20 | 3.85% | BTC:27 |
| **`eth_supertrend_only`** | 20 | **30** | **+37.86%** | **5.30%** | **2.92** | **0.336** | **63.3%** | 10/20 | 3.98% | ETH:30 |
| `eth_supertrend_rs_sizing` | 20 | 21 | +17.33% | 3.86% | 3.05 | 0.380 | 66.7% | 8/20 | 2.04% | ETH:21 |
| **`multiasset_supertrend_rs_one_position`** | 20 | **39** | **+40.99%** | 9.61% | **2.48** | 0.276 | 48.7% | **12/20** | 4.16% | BTC:26; ETH:13 |

Return contribution by asset (multi-asset variant):
- BTC: +30.64% (26 trades)
- ETH: +6.00% (13 trades)
- (sum differs slightly from stitched +40.99% because per-trade returns
  compound through the shared equity curve, not sum linearly.)

## Adoption decision per the locked criteria

| variant | trades ≥30 | PF > 2.24 | DD ≤ 9.63% | folds+ ≥ 10/20 | decision |
|---|---|---|---|---|---|
| `multiasset_supertrend_rs_one_position` | 39 ✓ | 2.48 ✓ | 9.61% ✓ | 12/20 ✓ | **adopted (research candidate)** |
| `eth_supertrend_only` | 30 ✓ | 2.92 ✓ | 5.30% ✓ | 10/20 ✓ | **adopted (research candidate)** |
| `btc_supertrend_rs_sizing` | 27 ✗ | 3.01 ✓ | 6.29% ✓ | 10/20 ✓ | not adopted (count) |
| `eth_supertrend_rs_sizing` | 21 ✗ | 3.05 ✓ | 3.86% ✓ | 8/20 ✗ | not adopted (count, folds) |
| `btc_supertrend_only` (reference) | 35 | 2.24 | 9.63% | 10/20 | the baseline being beaten |

## Answers to the report's required questions

### 1. Did multi-asset RS solve the sample-size problem?

**Yes.** Trade count went from 27 (BTC + RS sizing on Issue #5) to 39
(multi-asset) — first time the RS-overlay branch crosses the 30 gate.
The universe-expansion thesis (Issue #11's recommended next step)
validated.

### 2. Did ETH add real edge or just more noise?

**Real edge — substantially more than expected.** ETH solo (no RS) is
the strongest *risk-adjusted* result in the experiment: PF 2.92 (vs
BTC 2.24), DD 5.30% (vs BTC 9.63%, ~45% lower), win rate 63.3% (vs BTC
45.7%, +18pp), Sharpe 0.336 (vs BTC 0.266). SuperTrend(10, 3) appears
to be a cleaner signal on ETH than on BTC over this window, possibly
because ETH 4h trends extend longer between false flips. This is the
single most interesting finding of the experiment.

### 3. Did one-position selection improve or hurt returns?

**Mixed but mostly neutral.** Total return +40.99% slightly exceeds
either solo (+38.66% BTC / +37.86% ETH), so combining the universes
*did* add something. PF degrades to 2.48 vs ETH's 2.92 because the
selection rule picked BTC twice as often as ETH (26 vs 13 trades) and
BTC's PF is materially worse than ETH's. The one-position constraint
costs us cleaner ETH entries to chase BTC's frequency. Running BTC and
ETH as **independent legs** would likely improve risk-adjusted return
further — but that wasn't the spec, and would change the headline
adoption decision.

### 4. Did PF stay above BTC `supertrend_only`?

**Yes**, PF 2.48 > 2.24. By a clear margin; not borderline.

### 5. Did drawdown stay controlled?

**Yes**, just barely: 9.61% ≤ 9.63%. The 0.02 pp margin is essentially
tied. The selection rule preserves BTC's drawdown profile because BTC
drives most of the trades. If the multi-asset variant had picked more
ETH-leaning entries the DD would have come down further — see ETH solo
at 5.30%. So there's a clear DD-improvement lever available, just
unused in this configuration.

### 6. Which asset contributed most?

**BTC**, by a 5:1 ratio of return contribution and a 2:1 ratio of
trade count (26 trades / +30.64% vs 13 trades / +6.00%). The selection
rule (RS score → SuperTrend distance → skip) favors BTC during periods
where BTC was the strong asset, which was most of recent history. ETH's
contribution is positive but small. This skew is partly intentional
(the RS sizing rule was designed BTC-first) and partly a fact about
the 2022-2026 window (BTC outperformed ETH overall).

### 7. Did RS sizing help both assets or only BTC?

**Only BTC. It hurts ETH.**

| variant | n | OOS return | DD | PF |
|---|---:|---:|---:|---:|
| BTC solo | 35 | +38.66% | 9.63% | 2.24 |
| BTC + RS sizing | 27 | +38.03% | 6.29% | 3.01 |
| ETH solo | 30 | +37.86% | 5.30% | 2.92 |
| ETH + RS sizing | 21 | +17.33% | 3.86% | 3.05 |

For BTC the RS overlay trades a tiny return loss (-0.63%) for a large
DD improvement (-3.34 pp) and a clear PF lift (+0.77). For ETH it cuts
the return in half (+37.86% → +17.33%) for marginally lower DD and
roughly the same PF as the BTC case. The natural explanation: the RS
sizing rule is fundamentally a "trade BTC when BTC is winning" gate. By
symmetric definition the ETH overlay only allows ETH long when ETH is
stronger than BTC, which happens less often in this window — so it
chokes off a lot of ETH's actually-winning trades. The fix would be a
non-symmetric ETH overlay, which is a *new parameter choice* and
therefore out of scope for this experiment.

### 8. Should this be adopted, rejected, or extended?

**Adopted as research candidate**, with two variants:

- `multiasset_supertrend_rs_one_position` is the variant the spec
  asked about. Passes all four gates. Adopted.
- `eth_supertrend_only` is the surprise side finding. Passes all four
  gates with markedly better risk-adjusted metrics. Also adopted.

Both are research candidates only — live worker remains unchanged.
Before any live-wiring consideration, the natural extensions are
listed under Question 9.

### 9. Should the next step be Issue #6 HMM or a broader top-5 crypto rotation?

**Recommend top-5 rotation next, not HMM.** Three reasons:

1. The data argues for it. Universe expansion just *worked* — 27 →
   39 trades, gate crossed. The simplest hypothesis is that adding
   3 more assets (SOL, AVAX, LINK e.g.) doubles trade count again,
   builds a more diversified signal, and lets us drop the one-position
   constraint to a small concurrent budget (say 2 of 5).
2. ETH solo was an unexpected stand-out. If 5 assets in the universe
   contain another ETH-quality signal (low DD, high PF, mid-frequency),
   that's a portfolio-level improvement HMM cannot deliver.
3. HMM is orthogonal and can still be queued. It does *not* compete
   with universe expansion; it could later overlay on top of a
   top-5 SuperTrend portfolio if that adopts.

The honest counter-argument: top-5 rotation introduces multi-asset
selection and concurrency complications that we don't have in #12.
HMM is cleaner code-wise. If you want the lower-risk next experiment,
HMM is fine. Either is defensible.

## Causality / leakage check

RS features at bar `t` use closes through bar `t` only. The
multi-asset coordinator processes bars in chronological order. At
each bar:

- Exit on the held asset is evaluated first; if triggered, the
  position closes and the bar moves on (no re-entry on same bar).
- Entry signals on both assets are evaluated using only data at bar
  `t` (close, RSI, SuperTrend line, ATR, RS gates).
- Selection between two valid signals uses the RS score
  (`btc_minus_eth_return_n` at bar `t`) and, on tie, the SuperTrend
  distance / ATR at bar `t`. Both are causal.

Walk-forward slices the *test* window starting after the train + embargo
bars. No RS feature is fit on training data — the windows are
constants from yaml.

## Conventions respected

- SuperTrend (10, 3): unchanged.
- RS lookback (30), ratio EMA (30): unchanged from Issue #5.
- Fee 10 bps/side, slippage 5 bps: unchanged.
- Walk-forward train 1440 / test 360 / embargo 6: unchanged.
- One position at a time across the portfolio: enforced (max
  concurrent observed = 1).
- No per-asset tuning: identical strategy yaml used on both BTC and
  ETH.

## Artifacts

- `results/multiasset_supertrend_rs_comparison_20260530_113106.csv`
- `results/multiasset_supertrend_rs_comparison_20260530_113106.md`
- `results/trades_multiasset_supertrend_rs_20260530_113106.csv` (152
  trades across all 5 variants — gitignored as per
  `.gitignore` `results/trades_*.csv` rule)
- `hermes_trading/relative_strength.py` (extended:
  `compute_multi_asset_features`, `build_asset_decisions`)
- `state/strategy_supertrend_multiasset_rs.yaml` (new)
- `scripts/run_multiasset_supertrend_rs.py` (new)

## Closing-the-loop summary

- **Issue #12 status:** closed, criteria met.
- **Headline:** Multi-asset SuperTrend + RS clears all four locked
  gates (39 trades, PF 2.48, DD 9.61%, 12/20 folds+). Surprise side
  finding: ETH-only SuperTrend (no RS) clears every gate with better
  PF (2.92), much lower DD (5.30%), and 63.3% win rate. Both adopted
  as research candidates.
- **Live worker:** unchanged. Continues to run v2 long-short.
- **Next per recommendation:** broader top-5 crypto rotation (new
  issue), with Issue #6 (HMM) remaining queued. Either is defensible.
