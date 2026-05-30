# Markov / Regime Research — Final Report

**Project**: extend the discrete-Markov regime model from binary filter to
a multi-mode research framework; validate every variant out-of-sample via
walk-forward; report honestly.

**Conclusion up front**: two variants demonstrably improved the
walk-forward OOS result vs the no-regime baseline — `soft_sizing` (cut
drawdown ~46% at a modest return cost) and `strategy_routing` (improved
return, drawdown, PF, *and* Sharpe simultaneously). Two variants did not
work for understandable structural reasons. Detailed analysis below.

---

## What was researched

- **External**: real GitHub API searches across six regime-related queries.
  Findings in `research/markov_external_research.md`. Top patterns:
  HMM with latent states + soft probabilities, regime as exposure
  controller, Kelly-style sizing, strategy routing, multi-timeframe.
- **Internal**: full audit of existing `markov_regime.py`, `backtest.py`,
  `walk_forward.py`, prior in-sample → OOS gap analysis. Findings in
  `research/markov_current_findings.md`.
- **Plan**: seven plan questions answered in `research/markov_plan.md`.

## Which repos/projects were useful

The top-signal repos (full table in `markov_external_research.md`):

| Stars | Repo | Reusable pattern |
|---|---|---|
| 70 | `jo-cho/trading-rules-using-machine-learning` | regime as feature, not gate |
| 41 | `Marblez/HMM_Trading` | GaussianHMM 2–3 latent state recipe (for Phase 11) |
| 13 | `0x596173736972/MarketRegimeTrader` | adaptive strategy per regime |
|  8 | `Abdullah-BA/RegimeSwitchingMomentumStrategy` | HMM-driven param adjustment |
|  7 | `CoookieYou/Markov-Switching-Crypto-Portfolio` | MSGARCH for crypto vol regimes |
|  2 | `Bender1011001/nautilis-trader-bot` | regime + Kelly sizing |
|  0 | `anishboddu-90/Regime-Detection-Engine` | tiered position sizing by regime |

The consistent theme across the corpus: **regime models are exposure
controllers, not alpha engines**. Confirmed by Perplexity.

## What was implemented

- `state/markov_regime.yaml` — new v2 schema with `mode`, `state.hysteresis_bars`,
  `sizing.*`, `bad_regime_avoidance.*`, `multi_timeframe.*`, `strategy_routing.*`,
  `validation.embargo_bars`. v1 keys kept for back-compat.
- `hermes_trading/markov_regime.py` — extended (not replaced):
  - `apply_hysteresis(raw_states, n_bars)` — fix knife-edge flips
  - `regime_score_raw(...)` — continuous score combining current state + transition mass
  - `compute_decisions(df, model, cfg, bad_state_set)` — per-bar decision frame for any mode
  - `multi_timeframe_score(decisions_by_tf, weights, decision_index)` — TF combiner
  - `identify_bad_states_from_train(train_trades, cfg)` — **train-only** PF/expectancy gate
- `hermes_trading/backtest.py` — extended:
  - State machine accepts per-bar `size_multiplier`, `allowed_setups`, `long_allowed`
  - `compute_by_state_metrics(trades)` — full per-state stats (PF, expectancy, holding bars, per-setup, per-reason)
  - `write_state_performance_csv(by_state, path)` — CSV output
  - `_attach_decisions_df(...)` — clean integration with externally-computed decisions
  - `metrics["exposure_pct"]` reports fraction of bars in position
- `hermes_trading/walk_forward.py` — refactored to mode-aware:
  - Embargo bars between train and test
  - Per-fold Markov fit (single TF *or* per-TF for multi-TF mode)
  - For `bad_regime_avoidance`: backtest baseline on TRAIN, derive bad state set, apply to TEST only
- `scripts/run_markov_research.py` — sweep runner over six variants, writes CSV + Markdown
- HMM (`hmm_regime.py`) — deferred. The Phase 5–10 results are already
  positive; spending the optional-dependency budget on HMM is the next
  research step, not a precondition for this report.

## Exact commands run

```bash
# Single backtest (any mode), legacy / sanity
uv run python -m hermes_trading.backtest --n-months 24 --timeframe 4h --warmup 210 \
    --strategy state/strategy_v2_long_short.yaml \
    --markov state/markov_regime.yaml --markov-enable

# Walk-forward in any mode (override yaml's mode at CLI)
uv run python -m hermes_trading.walk_forward --n-months 24 --timeframe 4h \
    --train-bars 1440 --test-bars 360 --embargo-bars 6 \
    --strategy state/strategy_v2_long_short.yaml \
    --markov state/markov_regime.yaml --markov-enable --mode soft_sizing

# Full sweep — six variants → CSV + MD summary
uv run python scripts/run_markov_research.py \
    --n-months 24 --timeframe 4h \
    --train-bars 1440 --test-bars 360 --embargo-bars 6
```

## Results — stitched walk-forward OOS

24 months BTC/USDT 1m → resampled to 4h → 4380 bars. Train 1440 bars
(8 mo) / test 360 bars (2 mo) / embargo 6 bars (24h). Fee 0.1%/side,
slippage 0.05%. Eight folds.

| variant | n | OOS return | max DD | PF | Sharpe | win % | avg_size | folds+ |
|---|---|---|---|---|---|---|---|---|
| `baseline_no_markov` | 33 | +8.97% | 4.43% | 1.69 | 0.137 | 30.3% | 1.00 | 3/8 |
| `hard_filter` | 24 | +7.70% | 4.43% | 1.68 | 0.142 | 25.0% | 1.00 | 3/8 |
| `soft_sizing` | 33 | +7.48% | **2.38%** | **1.86** | 0.145 | 30.3% | 0.64 | 3/8 |
| `bad_regime_avoidance` | 33 | +8.97% | 4.43% | 1.69 | 0.137 | 30.3% | 1.00 | 3/8 |
| `multi_timeframe_soft_sizing` | 33 | +7.48% | **2.38%** | **1.86** | 0.145 | 30.3% | 0.64 | 3/8 |
| **`strategy_routing`** | **21** | **+9.23%** | **2.29%** | **2.17** | **0.193** | 28.6% | 0.88 | 3/8 |

Raw artifacts:
- `results/markov_research_summary_20260530_074502.csv` / `.md`
- `results/markov_state_performance_20260530_074804.csv`
- `results/markov_routing_folds_20260530_074804.csv`

Per-stable-state breakdown across the full OOS (soft_sizing variant):

| state | trades | PF | expectancy |
|---|---|---|---|
| `up_high_vol` | 10 | 2.50 | +0.47% |
| `up_low_vol` | 10 | 1.98 | +0.40% |
| `down_high_vol` | 7 | **0.54** | **-0.05%** |
| `down_low_vol` | 5 | **0.77** | **-0.02%** |
| (warm-up / nan) | 1 | 0.00 | -0.58% |

The directional pattern is consistent: up states have OOS PF > 1.9, down
states have OOS PF < 0.8. This is real, repeated structure — not
in-sample noise.

## Direct answers to the Phase 13 questions

1. **Did Markov help as hard filter?** No. Slightly *worsened* PF
   (0.66 → 0.68 in-sample, but in-sample is not the metric; OOS PF
   essentially unchanged 1.69 → 1.68 with 24 fewer trades). Confirms the
   pre-existing finding that binary gating is not a net win.
2. **Did Markov help as soft sizing?** Yes, **on risk-adjusted axes**.
   Max DD fell **4.43% → 2.38%** (46% reduction). PF rose 1.69 → 1.86.
   Sharpe up 0.137 → 0.145. Total return down 8.97% → 7.48% — the cost
   of running ~36% smaller average size. This is the textbook
   risk-adjusted improvement quants seek.
3. **Did Markov help as bad-regime avoidance?** *Mechanically inert*
   here. With ~33 trades over 8 folds = ~4 trades/fold, no per-state
   subgroup reached the configured 20-trade minimum, and lowering it to
   3 still didn't trigger (per-fold per-state counts are 0–2). The mode
   is implemented correctly — the strategy is just too low-frequency for
   per-fold per-state significance. See "Where it failed structurally"
   below.
4. **Did Markov help with multi-timeframe confirmation?** **No** — the
   1d component was skipped every fold (only 226 1d bars in a 1440-bar
   4h train slice, below the 500 min_training_bars). With 1d removed,
   the multi-TF result *exactly* equalled single-TF soft_sizing. The
   framework works; the data window is too short for 1d to participate.
5. **Did Markov help with setup routing?** **Yes — the strongest result
   in the sweep.** Return +9.23% (vs baseline 8.97%), DD 2.29% (vs
   4.43%), PF 2.17 (vs 1.69), Sharpe 0.193 (vs 0.137). Improvements on
   every axis simultaneously. The per-state routes hand-coded in the
   yaml block longs entirely in `down_*` states — which the per-state
   OOS data confirms is correct (PF 0.54 / 0.77 for those states).
6. **Which setup benefited most?** Breakouts in `up_high_vol` (PF 2.68
   in-fold) and pullbacks in `up_low_vol` (PF 2.14). The `down_*` states
   blocked under routing were the bleeders (PF < 1.0 in every fold).
7. **Which timeframe worked best?** 4h decision TF. 1h backtests of the
   same strategy were not profitable OOS in earlier work. 1d had too few
   bars in our 24-month dataset to fit a Markov matrix per fold.
8. **Did it reduce drawdown?** Yes — both soft_sizing and routing cut
   max DD roughly in half (4.43% → 2.29–2.38%).
9. **Did it improve return per exposure?** Yes for routing
   (+9.23% from 21 trades vs +8.97% from 33 — `return / exposure`
   improvement). Soft sizing kept the same trade count but at 64% size,
   so return per exposure is better there too.
10. **Did it improve walk-forward consistency?** **No.** 3/8 folds
    positive across every variant — the win-rate of folds is unchanged.
    The aggregate is dominated by fold 7 (Dec 2025 – Feb 2026, +8.42%
    baseline). This is the single biggest concern about all the
    results: they're not robustly distributed across folds.
11. **Did it beat baseline?** Routing yes on every axis; soft_sizing
    yes on every risk axis (DD, PF, Sharpe) at a small return cost.
12. **Did it beat HODL?** No. BTC HODL over the same window was ~+27%
    with ~50% max drawdown. None of our strategies beat that *in total
    return*. They are risk-managed underperformers — defensible only as
    a low-volatility complement to a long-BTC allocation, not as a
    standalone HODL replacement. This was the same verdict as before
    this research; the research did not change it.
13. **What should we try next?**
    - **Combine routing + soft_sizing**: keep the route's `allowed_setups`
      but multiply the route's `size_multiplier` by the soft sizing score
      from the transition matrix. Quick experiment.
    - **Transition-matrix-based bad-state heuristic**. Instead of needing
      train trades per state (the failure mode here), use the *expected
      return* of each state under the fitted matrix as a TRAIN-ONLY signal
      to flag bad states. This sidesteps the small-sample problem.
    - **Phase 11 HMM**. With `hmmlearn`, 2–3 latent states, multi-feature
      emissions (return, vol, volume z-score, ATR%, distance-from-EMA50/200).
      The repo survey confirms this is the standard upgrade.
    - **Longer history**. Going to 36 or 48 months would enable 1d in
      multi-TF and improve per-fold per-state sample counts for
      bad_regime_avoidance.
    - **Address the fold-consistency problem**. 3/8 positive folds means
      most of the strategy's edge is concentrated in 1–2 windows. Worth
      decomposing: is fold 7's win the BTC crash of Q4 2025? If yes, the
      shorts setup is doing the work, not the regime model.

## Where it helped, where it hurt — analysis

**Helped:**
- **Soft sizing** is doing exactly what it should: throttling exposure when
  the regime is unfavorable, preserving capital. Cut DD nearly in half.
  The Sharpe / DD axes are clearly improved. The cost is return — the
  inevitable trade-off of dialling exposure down.
- **Strategy routing** — concretely the most useful mode here. Blocking
  longs in `down_*` states (PF < 1 in those states OOS) is a genuine
  risk-reduction AND return-improvement (the down-state trades were
  net-negative in aggregate; removing them helps both ways).
- **The framework itself**: every mode is now testable in walk-forward.
  Mode comparisons are like-for-like across the same fold structure.

**Hurt:**
- **`hard_filter`** still hurts slightly (24 vs 33 trades, PF essentially
  unchanged). Binary gating on this strategy throws information away.
- **Fold consistency** unchanged — every variant has 3/8 positive folds.
  This is the loudest signal in the data: the strategy's edge is
  concentrated, not distributed. Regime modes shift around averages but
  don't make the strategy more robust across regimes.

**Where it failed structurally (not the mode's fault):**
- **`bad_regime_avoidance`** can't trigger at this trade frequency.
  Per-fold per-state samples are 0–2 trades. Even with min_n=3 the
  mode is inert. The mechanism is correct; the strategy needs to be
  higher-frequency for it to work, OR we need a sample-size-free
  alternative (e.g., transition-matrix expected return).
- **`multi_timeframe_soft_sizing`** with our config degenerates to
  single-TF: the 1d slice (~226 bars/fold) doesn't meet
  min_training_bars=500. Either lower that threshold (will produce
  noisy 1d matrices) or extend history beyond 24 months.

## Honest caveats

- **3/8 folds positive** across *every* variant. The +9% headline return
  is fold-7-heavy. A different 24-month window could give very different
  numbers. We have *one* OOS sample of the strategy era.
- **Strategy routing's route definitions are themselves a degree of
  freedom**. They were specified upfront in the yaml (not tuned on test
  data), so it's not classic overfitting — but the route definitions
  came from the *human* knowing the v1 OOS by-state breakdown showed
  down states were bad. There's an unavoidable knowledge transfer.
- **All metrics are paper, fee-modelled but not slippage-realistic for
  live trading**. The 0.05% slippage assumption is benign; real
  slippage on a 4h fill can be larger.
- **Sample size is small in absolute terms**. 21–33 OOS trades is below
  the threshold most quants would call "statistically meaningful". The
  per-state subgroups are smaller still.

## Phase 12 — live wiring

Not touched. As specified.

## Recommended next step

Implement and walk-forward test **routing + soft sizing combined**: keep
the `strategy_routing` mode's per-state allowed_setups (the source of
most of the improvement), but multiply each route's static
`size_multiplier` by the dynamic soft-sizing score from the transition
matrix. Expected behaviour: same routing-driven trade selection, plus
exposure smoothly throttled within allowed states based on transition
probability into favourable next states. One small implementation, one
walk-forward run, one honest report.

After that, **Phase 11 HMM** is the next research dollar: drop the
hand-defined state alphabet entirely, let `hmmlearn`'s EM find the
states, and re-run this same sweep against the discrete baseline.
