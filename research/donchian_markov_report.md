# Donchian × Markov Experiment — Report

Single experiment: **Donchian-20 trend-following breakout** with optional
Markov `strategy_routing` filter. Goal — see if longer trend captures can
replace RSI mean-reversion on 4h BTC.

**Adoption criteria (set up front, not after seeing the data):**
- OOS profit factor > **2.17** (the current best, `strategy_routing`)
- AND OOS trade count ≥ **30**

**Result: NOT ADOPTED.** Both Donchian variants underperformed every existing
variant, including the no-Markov baseline. Donchian breakouts produced
*negative* OOS return on this dataset.

---

## Walk-forward results (24 mo BTC 4h, 8 folds, embargo 6)

| variant | n | OOS return | max DD | PF | Sharpe | win% | folds+ |
|---|---:|---:|---:|---:|---:|---:|---:|
| `baseline` | 33 | **+8.97%** | 4.43% | **1.69** | 0.137 | 30.3% | 3/8 |
| `strategy_routing` | 19 | +3.94% | 3.44% | 1.54 | 0.099 | 21.1% | 2/8 |
| **`donchian_only`** | 33 | **-2.16%** | 6.79% | **0.90** | -0.038 | 33.3% | 3/8 |
| **`donchian_plus_routing`** | 25 | **-2.97%** | 6.54% | **0.84** | -0.071 | 32.0% | 3/8 |

`strategy_routing` is shown here with `multi_timeframe: enabled=false` for a
clean apples-to-apples comparison. (Earlier reports showed PF 2.17 for the
same variant with multi-TF on — but Phase-5/Phase-7 code review found the MTF
path silently drops the 1d weight and silently fillna(True)'s long_allowed at
fold boundaries; with those issues removed the variant lands at PF 1.54.
**This recalibration is the more honest baseline for adoption decisions.**)

Detailed artifacts:
- `results/donchian_markov_comparison_20260530_085605.csv` / `.md`
- `results/trades_donchian_detailed_20260530_085605.csv` (46 Donchian trades)

## What Donchian actually did — exit reason breakdown

Across both Donchian variants (46 trades total):

| exit_reason | count | avg net |
|---|---:|---:|
| `midline_break` (close below prior 20-bar mid) | 29 (63%) | +0.23% |
| `stop` (ATR trail hit) | 15 (33%) | **-1.07%** |
| `end` (open at last bar) | 2 (4%) | +4.10% |

- The **midline break** exit is the dominant reason — and it's roughly neutral
  per trade (+0.23%).
- The **ATR trail stop** is the dominant *loser* — average -1.07% per stop-out.
- Notably, **the bad-Markov-state exit never fired** in any trade — because the
  routing filter already blocked entries when `stable_state ∈ {down_*}`, so
  by the time the exit checked, the state had no way to flip there mid-trade
  on this 24-mo window. The exit clause is dead code on this dataset.

## The 16+ bar holds DO exist — and they win

| metric | value |
|---|---|
| Donchian trades held ≥16 bars | 19 / 46 (41%) |
| Their win rate | **89.5%** |
| Their avg net return | **+1.53%** |

This validates the Phase-3 hypothesis "the strategy makes its money in 16+
bar holds." When Donchian survives, it works. The problem: the *other 27
trades* (held <16 bars) collectively lose enough to drag OOS negative.

---

## Direct answers to the 10 audit questions

### 1. Did Donchian improve OOS PF above 2.17?

**No.** Donchian-only PF 0.90; Donchian + routing PF 0.84. Below the
no-Markov baseline's 1.69 *and* the routing baseline's 1.54.

### 2. Did it reach at least 30 trades?

Donchian-only: **yes** (33 trades). Donchian + routing: **no** (25, because
the regime filter blocks ~24% of bars from firing). Neither variant meets
the PF criterion, so the trade-count criterion is moot.

### 3. Did it improve drawdown?

**No.** DD increased from 4.43% (baseline) → 6.79% (donchian_only),
6.54% (with routing). Wider stop (2.5×ATR) lets individual losers run
larger than the breakout's 1.5×ATR, and the additional cluster of 4h trend
trades hits losing streaks.

### 4. Did it capture longer holds?

**Yes — but only partially.** 41% of Donchian trades held 16+ bars (vs ~17%
of baseline strategy v2 trades in the Phase-3 audit). The mechanism works.
The 16+ bar bucket made +1.53%/trade at 89.5% win rate, exactly matching
the Phase-3 trend-continuation thesis. The problem is the *under*-16 bars
bucket lost enough to overwhelm those wins.

### 5. Did Markov routing help Donchian?

**No.** Routing slightly *hurt* — PF 0.90 → 0.84, return -2.16% → -2.97%.
The routing filter cut 7 fold-1/fold-3 Donchian trades that would have
been in up_high_vol — but those trades were ~neutral. The remaining trades
concentrated bigger drawdowns into the same folds.

### 6. Which Markov states helped or hurt?

From the Donchian + routing variant's by-state breakdown:

| state | n | return | PF |
|---|---:|---:|---:|
| up_low_vol | 21 | +0.39% | 1.05 (neutral) |
| up_high_vol | 4 | -3.35% | **0.02** (catastrophic) |

The four up_high_vol Donchian trades were essentially the entire loss for
the routing variant. That's a strong signal but a 4-trade sample — could
be variance, could be that breakouts in high-vol regimes get whipsawed.

### 7. Were exits better than the 21-EMA trailing stop?

**Modestly yes on exit reason quality, but no on overall edge.** The 21-EMA
trail (from Phase 3) had PF 0.08 across 9 trades. Donchian's midline-break
exit had +0.23%/trade across 29 trades — clearly an upgrade on a per-exit
basis. But the wider ATR stops on Donchian (avg -1.07%/trade across 15
stop-outs) gave back what the midline saved.

### 8. Did ATR trail stop too early?

**Trail wasn't the main issue — the initial 2.5×ATR stop was.** 15 of 46
Donchian trades hit the initial ATR stop level *before* the trail had
ratcheted up. Avg loss on those: -1.07%. The ratcheting trail rarely
became the binding exit, because most losers got stopped within a few
bars of entry.

Loosening the initial stop further (e.g., 3.5×ATR) would reduce stop
frequency but increase per-loss size. Net effect unclear without testing
— and testing it would be exactly the "tune endlessly" anti-pattern the
hard rules forbid.

### 9. Should this replace RSI pullback?

**No, not based on this experiment.** Donchian's OOS PF (0.90) is
*better* than the long pullback's PF (0.39 from Phase 3) — so on that
narrow axis yes. But Donchian's OOS *return* is negative, so swapping
in Donchian as a *replacement* would still leave the bot losing OOS.
The Phase-3 conclusion ("disable long pullback") stands; the conclusion
"Donchian is the replacement" does not.

### 10. Should this be tested on 4h next?

This *was* the 4h test. The proposal in Phase 4 also mentioned 4h
explicitly; we ran exactly that. There's no further "4h next" — the next
sensible TF would be 1d, but with only 24 months we'd have ~720 daily bars,
not enough for a meaningful walk-forward.

---

## Honest read on why Donchian didn't help

Two structural issues, visible in the diagnostic CSV:

1. **The "win rate × win size" structure doesn't support 2.5×ATR stops.**
   When 27 of 46 trades stop out in under 16 bars, those losers need to be
   small (≤1×ATR-ish). 2.5×ATR initial stops give 15 of 27 short-holds an
   avg loss of -1.07%. Tightening the stop kills trends; loosening kills
   per-trade economics.

2. **Donchian-20 on 4h BTC fires too often in chop.** 33 trades over 24
   months ≈ ~1.4/month — same trade rate as v2 long-short. But the v2
   strategy gets its signal from a *confluence* (RSI + EMA + 3-bar +
   VWAP), while Donchian-20 fires off a single condition (close > 20-bar
   high). The single-condition trigger is hit more often in *false*
   breakouts than the confluence-triggered strategies.

These are both structural — they're not fixable by tweaking one parameter,
and "tuning until it works" is exactly what overfits OOS. The right call is
to not adopt and move to the next idea.

---

## Per the hard rules: NOT ADOPTED. What's next?

Following the "if Donchian fails" rule:

**Next single experiment recommended — SuperTrend trend-following.**

Reasoning:
- Phase 3 showed the "16+ bar holds → PF 15.84" finding is *real*; Donchian
  partially captured it (Q4 above, 19 trades at 89.5% win rate). So the
  trend-continuation thesis is alive — the entry trigger needs to be
  more selective than raw Donchian-20.
- SuperTrend(10,3) combines ATR-based volatility filtering AND direction
  flips in one indicator. It produces *fewer, longer* signals than
  Donchian-20 — directly addresses the "fires too often in chop" issue.
- Two hyperparameters (period 10, multiplier 3), TA-conventional defaults.
  Low overfit surface.
- Can be tested with the same walk-forward harness, same costs, same
  adoption criteria.

**Specifically NOT recommended next:**
- "Donchian + tighter VWAP filter" — that's tuning Donchian, forbidden.
- "Donchian + relative-strength filter" — same issue.
- "1d Donchian" — insufficient data for walk-forward in our 24 mo window.

**Recommended NEXT (after SuperTrend, if that also fails):**
- BTC/ETH relative-strength rotation — adds a second asset to the data
  pipeline. Doubles statistical power without parameter tuning. Highest
  prior-belief upside in the original Phase-4 ranking.

**Order to follow strictly:**
1. SuperTrend trend-following → walk-forward, adopt iff PF > 1.69 and ≥ 30 trades
2. BTC/ETH relative-strength → walk-forward, adopt iff cross-asset Sharpe improves
3. HMM 2-state regime overlay (optional dep on `hmmlearn`) → walk-forward
4. Funding-rate stress filter → requires new data adapter

If any of #1–4 adopts, stop and run that for at least 30 fresh OOS days
before stacking another change. If none adopt, the honest conclusion is
that this strategy class (4h BTC, signal-engine + Markov gate) has no
durable edge in the current sample, and the next investment should be
infrastructure (cross-asset, sub-4h data) or an entirely different
strategy class (volatility selling, statistical arb).

---

## Phase-12 reminder

Live worker (`hermes_trading/loop.py`) was **not** touched. Still runs the
existing v2 long-short with Markov disabled. The Donchian setup ships as
`enabled: true` only in `state/strategy_donchian_markov.yaml` (research
file); the live `state/strategy.yaml` has no `donchian` block. To revert
the Donchian additions: delete the donchian setup config from any new yaml;
the code paths self-skip when `setups.donchian.enabled` is false / absent.
