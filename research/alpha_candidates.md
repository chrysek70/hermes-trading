# Phase 6 — Optional next alpha candidates (no implementation)

After Phase 5 the OOS leaderboard is:

| variant | OOS return | DD | PF |
|---|---:|---:|---:|
| strategy_routing | +9.23% | 2.29% | 2.17 |
| baseline | +8.97% | 4.43% | 1.69 |
| soft_sizing | +7.48% | 2.38% | 1.86 |
| routing_sizing_combined | +2.55% | 3.19% | 1.37 |

`strategy_routing` is the floor for future experiments. The ideas below
are proposed; none is implemented. Each goes through the existing
walk-forward harness with the same fold structure.

| # | Idea | Why it's promising | What to build | OOS metric to beat |
|---|---|---|---|---|
| 1 | **4h Donchian-20 breakout + 2×ATR trailing stop** | Phase 3 showed 16+-bar trades print PF 15.84 — most P&L lives in trend continuation. Current entries don't target this directly. | New `setups.donchian` block; reuse signals.compute_indicators + add donchian_high/low. | PF > 2.17 OOS at trade count ≥ 30 |
| 2 | **4h EMA trend-following with volatility targeting** | Sizing inversely to ATR equalises per-trade risk; Phase 3's "med_high" vol quartile (PF 0.12) suggests turning size down in that bucket. | Replace fixed `position_size_r=0.5` with `target_risk_per_trade / (ATR × multiplier)`. | DD < 2.29% at PF ≥ 1.5 |
| 3 | **BTC/ETH relative-strength rotation** | Phase 3 noted single-asset, single-period concentration; ETH on same engine ≈ doubles sample. | Add ETHUSDT loader path (data.py already supports); walk-forward both then a rotation that picks the strongest-momentum asset. | Cross-asset Sharpe + drawdown reduction |
| 4 | **HMM 2-state favourable/adverse sizing** | Discrete Markov's hand thresholds knife-edge-flip; HMM with `hmmlearn` (optional dep) learns latent states from data. | New `hmm_regime.py` with multi-feature emissions (return, vol, volume_z, atr%, dist-from-EMA50/200). | Beat discrete-Markov `soft_sizing` OOS PF 1.86 |
| 5 | **HMM specialist routing** | Same as #4 but per-state pick which sub-strategy is active (e.g. breakout in regime A, pullback in regime B). | HMM + per-state allowed_setups mapping. | Beat `strategy_routing` OOS PF 2.17 |
| 6 | **Funding-rate stress filter** | Phase-3 by-direction shows shorts win in some windows. Funding sign / extreme tells us when crowded longs are about to lose. | New `funding_rate` adapter; gate setup entries by funding threshold. | Drawdown reduction in fold 8 (the 2026-Q1 bleed) |
| 7 | **Volatility-compression breakout** | Phase 3 "med-low" ATR quartile = PF 2.84. Compression-then-breakout is a known pattern matching this. | ATR percentile (rolling) + Donchian breakout AND compression criterion. | Higher PF at lower trade count vs current breakout |

## Sequencing recommendation

**Build in this order. Stop after each step and walk-forward.**

1. #1 Donchian breakout — cheapest, highest-prior-belief upside.
2. #2 Volatility targeting — small change, addresses the `med_high` toxic bucket.
3. #3 ETH (data already loadable) — doubles statistical power before any
   strategy-tuning attempt.
4. #4 or #5 HMM — last because of the `hmmlearn` dependency footprint.
5. #6 Funding-rate filter — new data adapter; biggest infrastructure cost.

Anything that does not beat `strategy_routing` OOS on the same walk-forward
folds gets dropped, not "tuned until it does".
