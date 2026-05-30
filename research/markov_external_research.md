# Phase 2 — External GitHub research

Real GitHub API searches across six queries (`hmm+trading`, `hidden+markov+model+trading`,
`regime+detection+trading`, `markov+switching+trading`, `crypto+regime+detection`,
`regime+based+position+sizing`). Top repos surveyed for architecture, not blindly cloned.

## High-signal repos worth inspecting

| Stars | Repo | What's useful |
|---|---|---|
| 70 | `jo-cho/trading-rules-using-machine-learning` | Momentum + regime detection ensemble; treats regime as a *feature*, not a gate |
| 41 | `Marblez/HMM_Trading` | S&P 500 HMM with 2–3 latent states; classic GaussianHMM emissions on returns |
| 26 | `tiagomonteiro0715/AI-Powered-Energy-Algorithmic-Trading-...` | HMM regime label fed as input feature to a downstream NN |
| 21 | `git-kevinxuhuili/KalmaN-Filter-Based-Pairs-Trading-...` | HMM used to switch *which strategy* runs (pairs-trading routing) |
| 16 | `uday-31/detecting-market-regime-changes` | HMM as labeller; multiple downstream classifiers; clean train/test |
| 13 | `0x596173736972/MarketRegimeTrader` | HMM detects regime, deploys "adaptive" strategy per regime |
| 12 | `kratu/wess_hmm` | Hybrid Wasserstein + HMM — interesting but probably overkill |
| 8 | `Abdullah-BA/RegimeSwitchingMomentumStrategy` | HMM dynamically adjusts a momentum strategy's params |
| 7 | `CoookieYou/Markov-Switching-Crypto-Portfolio` | **Crypto-specific MSGARCH**; volatility regimes used for portfolio weights |
| 5 | `denz3n/Algorithmic-Trading` | HMM regime switching + OPTICS clustering pairs |
| 2 | `Bender1011001/nautilis-trader-bot` | **Regime + Kelly Criterion sizing** — the modern recipe |
| 1 | `sinsasanderink/Regime-Aware-Multifactor_ML-LSTM-RL...` | "Regime-aware alpha engine" — regime modulates a multifactor stack |
| 0 | `anishboddu-90/Regime-Detection-Engine` | **Tiered position sizing by regime** — directly relevant to our soft_sizing mode |
| 0 | `Thordersonjg/regime-trading-bot` | Crypto bot adjusting *size* by bull/bear/chop |

## Patterns to copy

1. **Latent states, not hand-labelled buckets.** Almost every serious repo uses
   `hmmlearn.GaussianHMM` with 2 or 3 components and lets EM learn what the
   states are. State boundaries are learned, not picked.
2. **Soft probabilities feed exposure.** Regime probability is the input to a
   *continuous* sizing function, not a binary gate.
3. **Kelly-style sizing.** Position size scales with edge confidence (regime
   probability) and inverse volatility.
4. **Regime as a feature** for downstream models (the more sophisticated repos
   treat the regime label/probability as one input among many to a final
   decision model — does *not* trade the regime directly).
5. **Strategy routing.** Pairs/momentum/mean-rev each have a "preferred" regime;
   the router picks which sub-strategy is active based on the current
   regime label. (`KalmaN-Filter-...`, `RegimeSwitchingMomentumStrategy`,
   `MarketRegimeTrader`.)
6. **Walk-forward by default.** The serious ones never report in-sample only.
7. **Multi-timeframe / multi-feature inputs.** Volume, distance-from-MA, vol
   term-structure — not just price returns.
8. **Crypto MSGARCH** (`CoookieYou/Markov-Switching-Crypto-Portfolio`) — volatility
   regimes specifically, used to allocate weight to crypto in a portfolio.

## Patterns to AVOID

1. Repos that train HMM once on the whole history and report in-sample
   accuracy. Common; almost certainly overfit.
2. Repos using regime as a "next-bar up/down" predictor. Doesn't reflect
   how quants actually use these models.
3. Hard-coded "if regime == bear: short" rules without statistical validation
   of per-regime expectancy.
4. Strategies with 4+ knobs per regime — combinatorial overfit.
5. Crypto-specific bots with no transaction cost modelling (lots of these).
6. Anything that mixes the HMM *training* with the *strategy parameter
   tuning* on the same data.

## Practical implementation ideas distilled

These map directly onto your phase 4–9 spec; the GitHub corpus confirms the
direction.

1. **Continuous `regime_score` → `size_multiplier`** (soft_sizing mode).
   Instead of binary "long allowed", multiply base size by a [0.25, 1.0] score
   computed from `(current_state_weight × is_favorable) + (transition_weight ×
   Σ probs into favorable next states)`. Mirrors the Kelly-style scaling seen
   in `Bender1011001/nautilis-trader-bot`.
2. **Train-set "bad state" detection** (bad_regime_avoidance). Replicates
   `anishboddu-90/Regime-Detection-Engine`'s tiered sizing — but with the
   leakage-killing rule that the bad-state set is identified *on the train
   slice of each walk-forward fold*, never on test results.
3. **Multi-timeframe confirmation.** 1h+4h+1d weighted score, default
   `0.50/0.35/0.15`. The 4h-resampled state classifier is the same code,
   different df. Common in mid-stars repos.
4. **Strategy routing.** Per-state `allowed_setups` and `size_multiplier`.
   Even if v1 routing rules are simple, the framework lets later experiments
   try things like "only breakout in up_low_vol, only pullback in
   sideways_low_vol".
5. **Hysteresis** on state transitions. Not seen explicitly in the repo
   corpus (most use HMM smoothing instead), but a cheap-and-effective fix
   for the discrete Markov "knife-edge flip" problem.
6. **HMM as a follow-up experiment** (Phase 11). Optional dependency on
   `hmmlearn`. Feature set per the spec: log_return, rolling_vol,
   volume_zscore, atr_pct, distance_from_ema50, distance_from_ema200.
   Walk-forward only.

## What we are NOT copying

- Multi-asset portfolio allocation (out of scope; we're BTC-only).
- LSTM / RL regime classifiers (correct ML stack would dwarf the rest of
  the project).
- "Adaptive parameter" strategies that tune entry thresholds per regime —
  too many degrees of freedom for our sample size.

## Conclusion

The GitHub corpus confirms that the **failure mode of our v1 Markov filter
matches a well-known anti-pattern** (hand-labelled hard buckets + binary
gate). The improvements you've specified (soft sizing, bad-regime
avoidance, multi-timeframe, strategy routing) match how serious open-source
projects use these models. Implementation proceeds in Phases 4–10.
