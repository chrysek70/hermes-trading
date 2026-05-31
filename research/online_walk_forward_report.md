# Online walk-forward adaptive learning — final report (Issue #32)

This is the Phase 6 report for the Issue #32 online walk-forward
simulator. It answers the 10 spec questions plainly with quoted
numbers from the 24-month run.

## Setup

- Simulator: `scripts/run_online_walk_forward.py`
- Config: `state/live_multiasset_long_short_funding.yaml`
- Strategy: `state/strategy_supertrend_long_short.yaml`
  (SuperTrend(10, 3), EMA50/200, long+short, funding gate at p≥95 long
  block / p≤5 short block).
- Universe: BTC/USDT + ETH/USDT, 4h bars.
- Span: **2024-05-01 → 2026-04-30** (24 months, 4 380 4h bars × 2
  assets, 8 758 decision rows per rule).
- Fee 10 bps/side; slippage 5 bps; matches the Issue #29 live
  fill convention.
- Vol-sizing: 24-bar rolling realised vol; quartiles refit on the
  prior 12 months of vol values every 180 bars (~30 days).
- Comparison artifact: `results/online_walk_forward_comparison_20260531_073952.csv`.
- Per-rule decision and trade CSVs:
  `results/online_walk_forward_decisions_<rule>_20260531_073952.csv`
  and `results/online_walk_forward_trades_<rule>_20260531_073952.csv`.

All 6 rules close exactly **75 trades** over the 24-month window —
identical to `none`. That confirms what the spec requires: adaptive
rules in this study **only resize**; they never gate a signal.
The minimum multiplier is 0.25, never 0.

## Headline metrics (24-month window)

| rule | trades | total return | max DD | PF | win% | mean mult | ret / exposure | worst 3mo | latest 3mo |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `none` (baseline) | 75 | +29.36% | 16.23% | 1.37 | 38.7% | 1.000 | +29.36% | -16.70% | +4.71% |
| `rolling_decay_size` | 75 | +6.77% | 16.23% | 1.13 | 38.7% | 0.817 | +8.29% | -16.70% | +0.15% |
| `consecutive_loss_size` | 75 | +14.27% | 14.11% | 1.23 | 38.7% | 0.887 | +16.10% | -13.96% | +1.03% |
| `stop_cluster_size` | 75 | +14.12% | **8.72%** | 1.51 | 38.7% | 0.350 | **+40.35%** | -5.23% | +1.18% |
| `vol_sizing` | 75 | **+20.78%** | **7.78%** | 1.51 | 38.7% | 0.553 | +37.55% | -5.20% | +2.83% |
| `ensemble` | 75 | +13.60% | **5.63%** | 1.55 | 38.7% | 0.313 | **+43.39%** | -4.18% | +1.18% |

(Mean mult = average final adaptive multiplier across all 75
trades; ret/exposure = total_return ÷ mean mult, a capital-
efficiency proxy.)

## Headline result

The current 24-month window is materially worse than the Issue
#20 / Issue #27 4-year window everyone has been quoting (Issue #27
baseline = +139.71% / 4.64% DD over 48 months; here baseline =
+29.36% / 16.23% DD over the most recent 24). The recent two years
contain at least one period that pierces the historical DD by 3.5×.
**Every adaptive rule reduces drawdown.** Only `vol_sizing`
materially preserves return — it gives up about 8.6 pp of return
to cut DD in half (16.23% → 7.78%), and on a return-per-exposure
basis it leads the field at +37.55%.

`rolling_decay_size` is the weakest performer in this window — it
gives up 23 pp of return without meaningfully cutting drawdown
(16.23% → 16.23%). The "halve on PF<1.0" rule fires AFTER a
losing run has already done its damage; by then the regime has
often already turned. This matches the static walk-forward
intuition.

## Answers to the spec questions

### 1. Is the current bot learning now?

No — as documented in `research/current_adaptation_audit.md`. The
adopted live worker (`hermes_trading/multi_loop.py`) does not call
`reflect`, never mutates its config or strategy yaml, does not
change `size_per_asset`, and does not pause after bad performance.
Its only intra-trade adaptation is the SuperTrend trailing stop,
which is indicator behaviour, not learning.

### 2. Did online adaptive sizing help?

Yes — three rules clearly help on this 24-month window:

- **`vol_sizing`**: DD 16.23% → **7.78%** (-52%); return +29.36% →
  +20.78% (-29%); PF 1.37 → 1.51. Capital-efficient: 0.55 mean
  multiplier produces +37.55% return-per-exposure.
- **`ensemble`** (MIN of decay + stop_cluster + vol): DD 16.23% →
  **5.63%** (-65%); return +29.36% → +13.60% (-54%); PF 1.55.
  Highest return-per-exposure at **+43.39%**.
- **`stop_cluster_size`**: DD 16.23% → 8.72%; return +29.36% →
  +14.12%; PF 1.51; mean mult 0.35.

Two rules help less or not at all on this window:

- `consecutive_loss_size` cut DD by ~13% and return by ~51%. Worse
  in both directions than vol_sizing.
- `rolling_decay_size` gave up ~77% of return for ~0% DD
  improvement. The rule is too reactive — by the time PF over the
  last 10 trades drops below 1.0, the regime is usually about to
  turn back, so the rule punishes you on the recovery.

### 3. Which adaptive rule handled the bad recent 3 months best?

The "latest 3 months" column (trades whose `exit_time` is within
the last 90 days, i.e. since ~2026-01-30) shows:

| rule | latest 3mo cumulative net | max DD (24mo) | trades in window |
|---|---:|---:|---:|
| `none` | **+4.71%** | 16.23% | 13 |
| `vol_sizing` | +2.83% | **7.78%** | 13 |
| `stop_cluster_size` | +1.18% | 8.72% | 13 |
| `ensemble` | +1.18% | 5.63% | 13 |
| `consecutive_loss_size` | +1.03% | 14.11% | 13 |
| `rolling_decay_size` | +0.15% | 16.23% | 13 |

**On absolute return, `none` won the last 3 months** by riding the
recovery at full size. On a risk-adjusted basis, **`vol_sizing`**
was the best: it captured 60% of `none`'s 3-month return while
running at roughly half the exposure, with DD over the 24-month
sample less than half of `none`. If "best" means "biggest equity
gain regardless of risk" the answer is `none`; if it means
"protected the account while still earning", the answer is
**`vol_sizing`**.

For the deep-DD scenario the question is really aimed at — the
PEAK drawdown date (worst 3mo column) — `ensemble` is the clear
winner: -4.18% trough vs `none`'s -16.70%, a 4× reduction.

### 4. Did any rule reduce damage without killing long-term return?

Yes. **`vol_sizing`** is the only single rule that hit both
targets:

- 24mo DD: 16.23% → **7.78%** (cut in half).
- 24mo PF: 1.37 → **1.51** (improved).
- 24mo return: +29.36% → +20.78% — gave up 8.6 pp.
- Return-per-exposure: +29.36% → **+37.55%** (improved
  capital efficiency).

`vol_sizing` ran at 0.55 mean multiplier yet captured 71% of the
baseline return. The other rule that "didn't kill return" is
`consecutive_loss_size`, which kept 49% of return (+14.27%) at
~89% mean multiplier — but it didn't reduce DD enough to justify
the return haircut.

### 5. Did any rule create false positives?

Yes — two cases worth flagging:

- `rolling_decay_size` overreacted: it spent 18% of the window at
  reduced size (mean 0.82) yet failed to reduce DD at all
  (16.23% → 16.23%). The rolling-PF signal was too noisy / too
  lagged: every time it dropped below 1.0 and forced a half-size
  trade, the trade tended to be a winner on the regime reversal
  — so the half-size cost real return for no protection.
- `stop_cluster_size` and `ensemble` both spent most of the window
  at the 0.25 floor (mean mults 0.35 and 0.31). That is heavy
  capital under-deployment. The high return-per-exposure
  (+40.35%, +43.39%) makes them attractive on a Sharpe basis, but
  in absolute dollars they earn less than `vol_sizing`. If the
  worker's account is small, capital efficiency matters less than
  absolute return and `vol_sizing` is the better choice.

### 6. Should the live worker eventually support online adaptive sizing?

Yes, but only `vol_sizing` and only after a forward-paper test.
The reasoning:

- Out of the 6 rules tested, only `vol_sizing` clearly improves
  the risk profile (DD halved, PF up) while keeping enough return
  on the table that the account still grows meaningfully (+20.78%
  over 24 months).
- `vol_sizing` is also the rule with the most defensible logic:
  realised vol is a directly observable causal feature, the
  thresholds are quartile bands from train-window data (no
  fitting), and the rule was already vetted offline in Issues
  #27 and #31.
- Reflection-style yaml mutation should remain disabled in the
  multi-asset path. Sizing-only feedback is a much safer first
  adaptation point than allowing the worker to rewrite strategy
  parameters.

### 7. What is the safest first live adaptation?

`vol_sizing` with the locked Issue #27 / Issue #32 parameters:

- 24-bar rolling std of log returns per asset.
- Quartile thresholds fitted on the prior 12 months of vol, refit
  every 30 days.
- Map: Q1 → 1.00 × base; Q2/Q3 → 0.50 × base; Q4 → 0.25 × base.
- Never overrides the funding hard gate.
- Never opens a position with multiplier 0 — minimum sizing is
  0.25 × base.
- Sizing locks at entry and never changes for an open trade.
- Fully observable: each tick logs the rolling vol value, the
  active quartile band, and the resulting multiplier, so an
  operator can audit any sizing decision after the fact.

A safe rollout would replicate this in the live worker behind a
`risk.adaptive_sizing.enabled` toggle (off by default), surface
its decisions on the heartbeat, and pass a forward-paper test
before being adopted.

### 8. What should NOT be automated yet?

- **Strategy-parameter mutation**. Letting the worker rewrite
  SuperTrend period / multiplier, RSI threshold, or funding-gate
  thresholds at runtime is the kind of "learning" that classically
  destroys live PnL. Keep these in yaml, version-controlled, with
  human gates.
- **Trade pausing / drawdown-based exposure cuts that look at
  realised PnL** (`rolling_decay_size`, the strict consecutive-
  loss rule). Both proved too lagged in this study and would
  reduce expected return more than they protect against drawdown.
- **Stop-cluster auto-throttle** (`stop_cluster_size` /
  `ensemble`). Strong on risk metrics, but the floor is so heavy
  (0.25 × base × 0.5 = 0.125 of capital) that the worker would
  spend most of its time barely deployed. Operator needs to
  consciously choose that trade-off; it should not be the default.
- **Reflection**. Already disabled in multi-asset mode; should
  stay disabled.

### 9. What would a hedge-fund-style version of this look like?

The core elements (each pluggable into the Issue #32 simulator's
existing rule slots):

1. **Walk-forward HMM regime detection** with per-state size
   multipliers re-fit monthly on the trailing 12 months. The HMM
   version already exists in `run_adaptive_sizing.py`; making it
   online instead of fold-train-only is a 1-issue rewrite using
   the same `ClosedTradeMemory` + `VolSizingState` pattern.
2. **Per-asset Bayesian update** of a posterior "is this strategy
   alive?" probability after each closed trade, with size =
   `base × P(alive)`. This is the principled version of
   `rolling_decay_size`.
3. **Volatility-targeting** (cap notional exposure such that
   forecast 1-day VaR is constant). vol_sizing's quartile bands
   are a discrete approximation; the continuous version would
   typically use `target_vol / realized_vol` capped at 1.0.
4. **Cross-asset hedge layer**. Treat BTC + ETH exposures as a
   2-asset book and net long-vs-short to a target gross / net
   exposure. The current coordinator caps at 2 concurrent
   positions but does not hedge correlated longs.
5. **Decay-monitor-in-the-loop**: the existing
   `monitor_strategy_decay.py` already produces a verdict; route
   that verdict into a "max gross exposure" cap rather than
   leaving it as a report.
6. **Cost model with slippage scaled by notional × ADV.** The
   simulator's flat 5 bps slippage is a stand-in; production
   would have a curve.
7. **Latency-aware fills**: live worker should record fill
   latency on every order and surface that to the operator
   alongside the funding-gate decision.

All of these can be added one at a time; the Issue #32 simulator
gives a measurable framework for each.

### 10. What exact next implementation issue should follow?

**"Online vol_sizing in the live multi-asset worker — toggle
off by default; forward-paper test gate."** Specifically:

- Add `risk.adaptive_sizing: { enabled: bool, rule: 'vol_sizing',
  refit_train_months: 12, refit_every_bars: 180 }` block to
  `state/live_multiasset_long_short_funding.yaml`, default off.
- Add a `VolSizingState`-equivalent class in `multi_loop.py`
  (or extracted to a new `risk.py` module) that updates per asset
  off the in-memory price history the worker already has.
- Multiply `size_per_asset` by the adaptive multiplier on entry,
  log the multiplier on heartbeat and the closed trade row, never
  resize an open position.
- Add a Section 16 to `scripts/test_multiasset_worker.py` that
  exercises the live wiring end-to-end with synthetic data.
- Adoption gate: 4 weeks paper-forward, must not cut net return
  by more than the simulator predicted (≤30% return haircut) and
  must hold DD ≤ 10% across the paper window.

A secondary follow-up (lower priority): wire
`monitor_strategy_decay.py`'s verdict into a hard "halve all
sizes" override that the operator can opt into. Don't ship as
default; surface as a `risk.decay_halve: enabled: false` knob.

## Cross-references

- Phase 1 audit: `research/current_adaptation_audit.md`
- Issue #27 vol_sizing logic this simulator reuses:
  `research/adaptive_sizing_report.md`, `scripts/run_adaptive_sizing.py`
- Recent-window comparison precedent:
  `research/recent_adaptation_sizing_report.md`
- Live fill convention: Issue #29 (`hermes_trading/multi_loop.py`
  constants `RESEARCH_FEE_PER_SIDE`, `RESEARCH_SLIPPAGE`).
- Closed-bar live semantics: Issue #24 (`signal_row` = `iloc[i-1]`).
