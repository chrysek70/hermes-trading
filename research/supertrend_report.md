# SuperTrend(10, 3) Experiment — Report

Single experiment per Issue #4: SuperTrend(10, 3) trend-following on
BTC/USDT 4h, long-only, regime-gated by EMA50/200. Walk-forward against
the no-Markov baseline; SuperTrend + Markov routing run as an
informational third variant.

**Adoption criteria locked at experiment start:**

- OOS profit factor > **1.69**
- AND OOS trade count ≥ **30**

**Result: NOT ADOPTED.** PF criterion passed by a huge margin
(9.02 ≫ 1.69). Trade-count criterion failed (9 < 30). The result is
strikingly positive on every other axis; the honest reading is "promising
under-sampled signal", not "working strategy".

---

## Walk-forward results (24 mo BTC 4h, 8 folds, embargo 6)

| variant | n | OOS return | max DD | PF | Sharpe | win % | folds+ |
|---|---:|---:|---:|---:|---:|---:|---:|
| `baseline` | 33 | +8.97% | 4.43% | 1.69 | 0.137 | 30.3% | 3/8 |
| **`supertrend_only`** | 9 | **+13.00%** | **1.49%** | **9.02** | **0.666** | **77.8%** | **4/8** |
| `supertrend_plus_routing` | 4 | +2.47% | 0.07% | 34.54 | 0.978 | 75.0% | 3/8 |

Per-fold for `supertrend_only`:

| fold | test window | n | ret | PF |
|---:|---|---:|---:|---:|
| 1 | 2024-12-28 .. 2025-02-25 | 2 | -1.17% | 0.22 |
| 2 | 2025-02-26 .. 2025-04-26 | 0 | +0.00% | — |
| 3 | 2025-04-27 .. 2025-06-25 | 3 | +2.57% | 35.86 |
| 4 | 2025-06-26 .. 2025-08-24 | 2 | +7.14% | inf |
| 5 | 2025-08-25 .. 2025-10-23 | 0 | +0.00% | — |
| 6 | 2025-10-24 .. 2025-12-22 | 0 | +0.00% | — |
| 7 | 2025-12-23 .. 2026-02-20 | 1 | +1.60% | inf |
| 8 | 2026-02-21 .. 2026-04-21 | 1 | +2.41% | inf |

Three folds had **zero** SuperTrend signals (folds 2, 5, 6). Only fold 1
had a losing fold; the other six folds were either neutral (0 trades)
or net positive. Out of all 9 trades, 7 were winners (77.8%).

Artifacts:
- `results/supertrend_comparison_20260530_092000.csv` / `.md`
- `results/trades_supertrend_detailed_20260530_092000.csv`

## Why the adoption criteria fail

The PF (9.02) is roughly 5.3× the locked threshold. The trade-count gate
(9 < 30) exists precisely to prevent celebrating a fluke at this kind
of sample size:

- 9 trades over 8 folds means several folds contribute 0 or 1 trade.
- One bad fold (fold 1, 2 trades, -1.17%) is offset by handful of
  clean winners. Reverse those two and the result flips.
- At 77.8% win rate the standard error on a single fold's PF is
  enormous — fold 3 reported PF 35.86 off 3 trades; fold 4 reported
  PF=inf off 2 trades; these are not statistically meaningful PFs.

A PF of 9 on 9 trades is what the trade-count gate was designed to
filter out. The literal criteria are correct to refuse adoption here.

## What the signal looked like

- SuperTrend(10, 3) on 4h BTC fires roughly **4–5 times per year**.
  Very rare.
- When it does fire, the rolling 10-bar ATR-banded direction flip aligns
  with extended trends — most trades held many bars and exited on a
  later bearish flip (`stop` exit reason in all 9 cases, where the
  "stop" is the SuperTrend line itself).
- Average win +2.01%, average loss -0.78%. R-multiple is favourable.
- Max drawdown 1.49% — the lowest of any experiment in this repo.

The mechanism the Donchian experiment was *trying* to capture (long
trend holds, see Phase-3 audit's "16+ bars → PF 15.84" finding)
appears to be working here. SuperTrend's selectivity (fires only on
trend flips, not every channel-max touch) seems to be what Donchian
lacked.

## Adding Markov routing made it worse

`supertrend_plus_routing` reduced trade count further (9 → 4) and cut
return (+13.00% → +2.47%). The PF jumped to 34.54 but on 4 trades that
number means nothing. Routing's downside-state filter doesn't help a
strategy that was already only firing in well-defined uptrends — it
just chops out half the signals randomly. **Routing layer not useful
on top of SuperTrend on this sample.**

## Per the hard rules — do not tune

Issue #4 spec: "Do not tune (10, 3); if it fails, close the experiment
and move to next item in `ROADMAP.md`."

I will **not** sweep `(period, multiplier)` to find a config that
generates more trades. That's exactly the overfit anti-pattern the
criteria exist to prevent.

## What is honest and useful to try next

The natural follow-up is **not parameter tuning** — it's running the
identical experiment on a **larger data window**. This is:

- NOT a parameter change.
- NOT a cherry-pick.
- IS the standard way to test whether a "promising under-sampled"
  signal generalises.

A re-run on 48 months (instead of 24) of BTC 4h would roughly double
the expected trade count to ~18 — still under the gate, but the
direction would be clearer. 96 months would likely cross the gate.

That experiment would be **issue #11** (new), not a continuation of
issue #4, which closes per its own criteria.

The next item in the `ROADMAP.md` queue (BTC/ETH relative strength)
remains valid in parallel — adding ETH on the same engine roughly
doubles the SuperTrend trade count automatically by giving us a
second asset.

## Live worker — no changes

`hermes_trading/loop.py` was **not** touched. The live worker still
runs the v2 long-short config. SuperTrend code only fires when an
explicit yaml turns on `setups.supertrend.enabled` — currently only
`state/strategy_supertrend.yaml` does so, and that's a research yaml.

## Closing-the-loop summary

- **Issue #4 status: closed, criteria not met.**
- **Headline:** SuperTrend(10, 3) on 4h BTC produced a PF 9.02 / +13%
  / 1.49% DD result on 9 OOS trades. Under-sampled but consistent.
- **Next experiments (parallel, per `ROADMAP.md`):**
  1. SuperTrend on extended history (48mo) — new issue, validates
     the under-sampled finding without tuning anything.
  2. BTC/ETH relative-strength rotation (already issue #5).
- **`README.md` current-best table:** unchanged. Baseline (PF 1.69)
  remains the floor. SuperTrend is in `RESEARCH_LOG.md` and
  `ROADMAP.md` as a promising-but-under-sampled candidate, not as
  the adopted strategy.
