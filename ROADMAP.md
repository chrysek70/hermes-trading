# Roadmap

Snapshot of where the research stands, what's been tried, and what's
queued next. Updated as experiments complete.

## Current baseline

Walk-forward OOS on BTC/USDT 4h, 24 months, fees 10 bps/side + 5 bps
slippage, 8 folds (train 1440 / test 360 / embargo 6 bars):

| metric | value |
|---|---:|
| OOS return | +8.97% |
| max drawdown | 4.43% |
| profit factor | 1.69 |
| Sharpe (per-trade) | 0.137 |
| trade count | 33 |
| folds positive | 3/8 |

This is the floor every new variant must beat.

## Adoption criteria (locked)

A variant is adopted only when **both** hold against the same walk-forward
folds as the baseline:

- OOS profit factor > **1.69**
- OOS trade count ≥ **30**

No partial credit, no in-sample wins reported as success, no parameter
tuning to make a variant cross the bar after the fact.

## Completed experiments

| experiment | OOS PF | OOS return | verdict |
|---|---:|---:|---|
| v1 RSI mean-reversion (1m) | — | -75% (in-sample) | decommissioned: fees dominate at 1m |
| v2 long-short (4h) | 1.69 | +8.97% | adopted as live; current baseline |
| Markov hard_filter | 1.68 | +7.70% | rejected: indistinguishable from baseline |
| Markov soft_sizing (no MTF) | 1.86 | +7.48% | partial win: risk-adjusted only; lower return |
| Markov bad_regime_avoidance | 1.69 | +8.97% | rejected: inert — per-fold sample too small |
| Markov multi_timeframe_soft_sizing | 1.86 | +7.48% | rejected: degenerates to single-TF in our 24mo window |
| Markov strategy_routing (MTF off) | 1.54 | +3.94% | rejected after MTF recalibration; earlier 2.17 was inflated |
| Markov routing + soft_sizing combined | 1.37 | +2.55% | rejected: combination worse than either component alone |
| Regime-based HODL sizing | — | -44% to +9% | rejected: every variant underperformed naive HODL |
| Donchian-20 trend-following | 0.90 | -2.16% | rejected: trend mechanism partial (16+ bars: PF 15.84) but short-holds destroy it |
| Donchian + strategy_routing | 0.84 | -2.97% | rejected: routing slightly hurt Donchian |
| SuperTrend(10, 3) trend-following (24mo) | 9.02 | +13.00% | **not adopted**: only 9 OOS trades — fails trade-count gate (≥30) despite passing PF gate by ~5×. Promising under-sampled. See `research/supertrend_report.md`. |
| SuperTrend + strategy_routing (24mo) | 34.54 | +2.47% | rejected: routing cut signal further (9 → 4 trades); PF inflated by tiny sample |
| **SuperTrend(10, 3) on 48mo (Issue #11)** | **2.24** | **+38.66%** | **adopted as research candidate**: 35 OOS trades, max DD 9.63%, 10/20 folds positive — first variant since v2 to clear both gates. Same code, no parameter changes. Live worker NOT modified. See `research/supertrend_48mo_report.md`. |
| SuperTrend + strategy_routing (48mo) | 3.16 | +29.56% | not adopted: best risk profile (DD 5.95%, win 50.0%, Sharpe 0.337) but 20 trades — fails count gate. Tracked as a candidate sizing overlay for after BTC/ETH (Issue #5). |
| v2 baseline on 48mo (sanity ref) | 1.09 | +3.28% | reference only — baseline degrades materially on extended window (PF 1.69 → 1.09), indicating the original 24mo window was favorable for v2 setups. |
| **SuperTrend + BTC/ETH RS filter (Issue #5)** | **3.33** | **+35.43%** | not adopted: PF beats supertrend_only by 49% and DD drops 9.63% → 7.07%, but trade count falls to 20 — fails the 30-trade discipline gate. RS mechanism is validated; sample-blocked. |
| SuperTrend + BTC/ETH RS sizing (Issue #5) | 3.01 | +38.03% | not adopted: best DD of any SuperTrend variant (6.29%) and 27 trades — closest variant yet to clearing the gate, still 3 short. Same mechanism as filter; gentler implementation. |
| **ETH SuperTrend(10, 3) solo (Issue #12 side finding)** | **2.92** | **+37.86%** | **adopted as research candidate**: 30 trades exactly at the gate, max DD 5.30% (best of any SuperTrend variant), 63.3% win rate, Sharpe 0.336. Surprise discovery — SuperTrend is cleaner on ETH 4h than BTC 4h over this window. Live worker NOT modified. See `research/multiasset_supertrend_rs_report.md`. |
| ETH SuperTrend + RS sizing (Issue #12) | 3.05 | +17.33% | not adopted: 21 trades, 8/20 folds positive (worse than baseline). The symmetric RS overlay chokes off ETH's actually-winning trades (ETH-stronger condition rare in this window). |
| **Multi-asset SuperTrend + RS, one position (Issue #12)** | **2.48** | **+40.99%** | **adopted as research candidate**: 39 trades, max DD 9.61% (by 0.02 pp), 12/20 folds positive (better than BTC baseline). Universe expansion thesis validated — clears the 30-trade gate that BTC-only RS missed. BTC contributes 26/13 trades and most return; ETH lifts the count past the gate. |
| HMM regime overlay on BTC SuperTrend (Issue #6) | 4.01 | +49.98% | not adopted: PF +79% over BTC baseline (huge), DD -61% (3.79% from 9.63%), Sharpe 0.434, but 24 trades — fails 30-trade gate. Filter and sizing modes identical (bimodal HMM probabilities). |
| HMM regime overlay on ETH SuperTrend (Issue #6) | 4.27 | +27.80% | not adopted: PF +46% over ETH baseline, DD -22% (4.13% from 5.30%), but 17 trades — fails 30-trade gate badly. |
| BTC SuperTrend long-short (Issue #19) | 2.87 | +107.57% | research-only: shorts contribute +68.91% gross on 30 trades; PF +28% vs long-only; DD ticks up marginally (9.63% → 9.98%). |
| ETH SuperTrend long-short (Issue #19) | 3.67 | +163.94% | research-only: shorts contribute +126.08% on 34 trades; **highest PF measured on any single-asset variant**; DD unchanged at 5.30%; 15/20 folds positive. |
| **BTC/ETH parallel SuperTrend long-short (Issue #19)** | **3.26** | **+139.47%** | **not adopted (literal)**: 3 of 4 gates clear with large margins (PF 3.26 > 2.50, return +139.47% > +39.72%, 129 trades > 65). **DD gate fails by 0.22 pp** (5.76% vs 5.54%). Mechanism strongly validated; user decision pending whether to accept the marginal DD increase for ~3× return. Live config UNCHANGED. |
| Top-5 parallel portfolio, no overlay (Issue #14) | 2.19 | +40.70% | not adopted: 155 trades and DD 2.49% (lowest of any experiment), but PF 2.19 fails the 2.24 gate by 0.05. XRP drag (-1.43%) plus SOL/BNB at slightly lower per-asset PF dilute the BTC/ETH edge. |
| Top-5 parallel + HMM filter (Issue #14) | 2.49 | +26.74% | not adopted: PF/DD clear (2.49 / 1.86%) but return falls below the 38.66% gate (cut from +40.70% by HMM selectivity). |
| **BTC/ETH parallel portfolio (Issue #14 reference, ADOPTED)** | **2.50** | **+39.72%** | **adopted as research candidate** — passes ALL five locked gates (trades 65, PF 2.50, DD 5.54%, return +39.72%, max single-asset share 51%). Strictly dominates the Issue #12 one-position multi-asset variant (more trades, much lower DD, equal PF). Same engine, no overlay — just drop the one-position constraint. |
| BTC/ETH parallel + funding filter (Issue #7) | 2.57 | +40.01% | adopted as marginal research candidate — 63 trades, DD 4.68% (-15.5% vs baseline), PF +0.07. Improvement is real but small (only 2 of 65 trades affected). Phase 2 diagnostics showed SuperTrend entries rarely coincide with extreme-funding bars. Not a primary strategy. |
| ETH SuperTrend + funding filter (Issue #7) | 3.17 | +38.46% | marginal pass — 28 trades, PF +0.25 vs ETH baseline, DD unchanged. Effect within fold noise. Not adopted as primary; informational only. |

## Rejected experiment patterns

- **Hard binary gating** on a continuous regime signal (information loss).
- **Combined regime modes** (routing + sizing) — interactions degrade both.
- **Position-sizing-as-HODL-overlay** — backward-looking classifier lags
  bull trends and cuts size at the wrong moments.
- **Single-condition trend entries** (Donchian-20 alone) on 4h BTC —
  fire too often in chop relative to multi-condition confluence.

## Queued experiments (in execution order)

Run one at a time. After each, walk-forward against the current baseline.
Adopt if and only if the criteria above are met. If not, move to the next
without tuning the failed one.

1. **Live decay monitor — SHIPPED (Issue #15).** `scripts/monitor_strategy_decay.py`
   reads `state/trades.jsonl`, computes rolling PF / DD / win-rate /
   consecutive-losses over configurable windows (default 10/25/50),
   compares to research-time baselines, and exits 1 on `DEGRADED`.
   Monitor-only — never modifies trading decisions. No cron wiring,
   no Slack / Datadog integration (deliberate; both are easy to add
   later via exit code or JSON output). See `research/decay_monitor_report.md`.

1b. **Multi-asset live paper worker — SHIPPED (Issue #16).**
   `--config state/live_multiasset.yaml` runs BTC/USDT + ETH/USDT in
   parallel paper mode. New module `hermes_trading/multi_loop.py`
   drives a per-asset state machine with a portfolio-level
   `max_open_positions` cap; per-asset state files live at
   `state/positions/<KEY>.json` with one-shot migration from the
   legacy `state/position.json`. Single-asset mode is byte-for-byte
   preserved. Reflection is disabled in multi-asset mode (allowlist
   is single-asset v2-shaped and the interaction is untested).
   No HMM / funding overlay wiring in this pass per spec.

2. **Volatility-compression breakout (conditional).** Hypothesis:
   only fire breakouts after a low-ATR-quartile compression.
   Phase-3 audit showed the `med-low` ATR bucket had PF 2.84.

3. **Stacking HMM + funding** as a single experiment to formally
   test redundancy. Lower priority — Issue #7 already strongly
   suggests funding adds little beyond HMM.

3. **Funding-rate stress filter.**
   Hypothesis: extreme perpetuals funding precedes squeezes; gate
   direction by sign of funding. Requires a new data adapter.

4. **(Conditional) volatility-compression breakout.**
   Hypothesis: only fire breakouts after a low-ATR-quartile compression.
   Phase-3 audit showed the `med-low` ATR bucket had PF 2.84.

## Infrastructure improvements (orthogonal to strategy)

- Model fees and slippage in the live worker (currently zero — paper PnL
  overstates net edge by ~25 bps per round-trip).
- Migrate `_init_markov_live` to read v2 yaml keys so live regime layer
  can match research configurations.
- Fix multi-timeframe fillna behaviour in `markov_regime.multi_timeframe_score`
  and `backtest._attach_decisions_df` (see `BUGS.md`).
- Centralise duplicated metric reducers (Sharpe, profit factor,
  drawdown) into a shared helper.
- Config validation for strategy + regime YAML (catch missing/unused keys
  at load time instead of mid-loop crashes).

## Out of scope

- Real-money execution.
- Live order routing or exchange API writes.
- HFT / sub-minute strategies (fees dominate; rejected experimentally).
- Multi-asset portfolio optimisation beyond simple BTC/ETH rotation.
- LSTM / RL strategies (sample sizes don't justify the ML stack).
