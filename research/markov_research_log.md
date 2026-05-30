# Markov / Regime Research Log

A running log of the disciplined investigation into making Markov-style regime
detection genuinely useful in this paper-trading system.

---

## Day 1 — kickoff

### Repo state at the start

- v1 RSI worker decommissioned; live worker now runs v2 long-short on 4h
  (walk-forward OOS +8.98%, PF 1.69, DD 4.43% — the only positive variant we
  found so far).
- Existing `hermes_trading/markov_regime.py` = first-order discrete Markov
  chain, hand-defined 6 states. Used as a hard long-entry gate.
- Existing walk-forward results: 1h baseline PF 0.65 → markov PF 0.60;
  4h baseline PF 1.25 → markov PF 0.89; regime_hold sizing lost to HODL.

### Plan execution

| Phase | Deliverable | Status |
|---|---|---|
| 1 | `research/markov_current_findings.md` | ✓ |
| 2 | `research/markov_external_research.md` — real GitHub searches | ✓ |
| 3 | `research/markov_plan.md` — seven plan-question answers | ✓ |
| 4 | `state/markov_regime.yaml` v2 schema (modes, hysteresis, multi-TF, routing) | ✓ |
| 5 | Soft sizing: `regime_score_raw`, `compute_decisions(mode='soft_sizing')` | ✓ |
| 6 | Hysteresis: `apply_hysteresis(raw_states, n_bars)` | ✓ |
| 7 | Per-state metrics: `bt.compute_by_state_metrics`, `write_state_performance_csv` | ✓ |
| 8 | Bad-regime avoidance: `identify_bad_states_from_train`, train-only flow in `walk_forward` | ✓ |
| 9 | Multi-timeframe: `multi_timeframe_score`, `_build_multi_tf_decisions` | ✓ |
| 10 | `scripts/run_markov_research.py` + CSV/MD outputs | ✓ |
| 11 | HMM (`hmm_regime.py`) | deferred — flagged in final report |
| 12 | No live wiring | respected |
| 13 | `research/markov_final_report.md` | ✓ |

### Headline result

| variant | OOS return | max DD | PF | Sharpe |
|---|---|---|---|---|
| baseline | +8.97% | 4.43% | 1.69 | 0.137 |
| **strategy_routing** | **+9.23%** | **2.29%** | **2.17** | **0.193** |
| soft_sizing | +7.48% | **2.38%** | **1.86** | 0.145 |

`strategy_routing` improved every axis; `soft_sizing` improved every
risk-adjusted axis at a modest return cost. `hard_filter` slightly hurt
(as expected from prior research). `bad_regime_avoidance` was inert at
this trade frequency. `multi_timeframe_soft_sizing` degenerated to
single-TF because 24 months isn't enough for 1d in-fold fits.

### Honest caveat

3/8 folds positive across all variants. The aggregate is dominated by
fold 7 (Dec 2025 – Feb 2026). Edge is concentrated, not robustly
distributed. See `markov_final_report.md` for the full analysis.

### Recommended next step

Combine `strategy_routing`'s setup-by-state filter with `soft_sizing`'s
dynamic exposure scaling, then walk-forward test. After that, Phase 11
HMM with multi-feature emissions.
