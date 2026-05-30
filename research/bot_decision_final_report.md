# Phase 7 — Bot Decision Final Report

Plain-English answers to the audit questions, grounded in real Phase-3
diagnostics and Phase-5 walk-forward numbers.

---

## 1. What is the bot doing right now?

Running a **v2 multi-signal long-short strategy on BTC/USDT 4h** (file:
`state/strategy.yaml`, version "10"). Live worker polls every 10 seconds,
re-fetches the last ~300 4h bars from Kraken, and evaluates four setups:

- **Long pullback** — bullish trend (`EMA50 > EMA200`), RSI<32, price at
  the 21-EMA.
- **Long breakout** — bullish trend, bullish 3-bar play, price above session
  VWAP.
- **Short pullback** — bearish trend, RSI>68, price at the 21-EMA.
- **Short breakout** — bearish trend, bearish 3-bar play, price below VWAP.

Markov regime is **wired but disabled** in live. Position size is a flat
50% of unit notional. Reflection (LLM-driven one-knob change per 2 closed
trades) is on. The live tick log shows current price, RSI, position state,
unrealised P&L, and `regime=off`.

## 2. Why is it buying?

It opens a **long** when (a) the 50-EMA is above the 200-EMA on the 4h
chart, **and** (b) one of two triggers fires:

- The "buy a dip" trigger: RSI has dropped under 32 (oversold) and price
  has touched the 21-EMA without breaking the 200-EMA. Logic: in an
  uptrend, the first deep dip back to the rising trend line is the
  high-probability entry.
- The "buy strength" trigger: a 3-bar bullish-momentum pattern just
  completed (ignition bar → quiet inside bar → breakout above the inside
  bar's high), AND price is above session VWAP. Logic: confirmed
  acceleration from a base.

It enters at the close of the bar (with 5 bps slippage in the backtester;
no slippage modelled live).

## 3. Why is it selling (exiting)?

Long positions exit on whichever happens first:

- **Hard stop:** price drops to `entry − 1.2×ATR` (pullback) or `1.5×ATR`
  (breakout). This is the most common exit (43 of 57 trades).
- **Target RSI:** pullback trades exit when RSI ≥ 55 (mean reversion done).
- **Trail break:** breakout trades exit when price closes below the 21-EMA
  (which the stop has been ratcheting up to).
- **Regime flip:** EMA50 crosses back below EMA200 → all longs out.
- **Time stop:** position open for ≥ 240 bars (~40 days at 4h) — never hit
  in this sample.
- **Short exits** mirror these (with inverted comparisons).

The bot does **not** decide to "hold" explicitly — flat is the default
state between trades. It is in position only ~11.7% of the time in the
backtest.

## 4. What is actually profitable?

Three concrete edges, ordered by evidence strength:

1. **State filtering** — trades in `up_low_vol` and `up_high_vol` Markov
   states earn (PF 2.16, 1.65); trades in `down_*` states bleed (PF 0.17,
   0.20). Blocking down states (`strategy_routing` variant) is the only
   change that improved OOS PF (1.69 → 2.17).
2. **Holding past ~16 bars** — the 10 trades that survived to bar 16+
   produced PF 15.84 (80% win rate, +2.13% per trade compounded to +23%).
   When the strategy actually rides a trend, it rides it well.
3. **Short pullback** (RSI > 68 in confirmed bearish trend) — PF 3.04 on
   6 trades. Tiny sample, suggestive only.

## 5. What is actually losing?

In order of damage caused:

1. **Long pullback** (RSI<32 in uptrend) — PF 0.39, 9 trades, the worst
   single setup. RSI<30 entries went **0 of 6**. The classic "buy the
   oversold dip" thesis does not work on 4h BTC in this sample.
2. **Down-regime entries** (any setup) — collectively −12% compounded
   across `down_low_vol` + `down_high_vol`.
3. **Trail-exit on breakouts** — the 21-EMA trail exited 9 trades at PF
   0.08. The trail breaks even better trades than the static stop does.
4. **Entries far below VWAP** — 0 wins out of 15. Strategy has no
   guard for this.
5. **Early stop-outs** (≤3 bar holds) — 25 trades collectively at PF 0
   to 0.03 — pure whipsaw.

## 6. Did Markov improve the bot?

**Partly — when used as a router or sizer, not as a direction predictor.**
Walk-forward 24mo OOS:

| variant | n | OOS return | max DD | PF |
|---|---:|---:|---:|---:|
| baseline | 33 | +8.97% | 4.43% | 1.69 |
| **strategy_routing** | **21** | **+9.23%** | **2.29%** | **2.17** |
| soft_sizing | 33 | +7.48% | 2.38% | 1.86 |
| routing_sizing_combined | 19 | +2.55% | 3.19% | 1.37 |
| hard_filter | 24 | +7.70% | 4.43% | 1.68 |

- `strategy_routing` improves every axis (more return, half the DD, higher PF).
- `soft_sizing` cuts DD 46% at modest return cost.
- `hard_filter` does nothing useful.
- **Combining routing + sizing actually hurts** (Phase 5 finding) —
  PF 1.37, worse than either alone.

## 7. Did Markov predict direction or just improve exposure?

**Just exposure** — and that is the *correct* role per the GitHub-corpus
research. The Markov filter never directly says "go long" or "go short";
it (a) blocks trades in historically unprofitable states, (b) scales
position size by regime confidence. The base signal layer still decides
the direction. This matches how serious quant houses actually use regime
models.

The hard filter ("buy only when state matches") under-performed OOS —
direction prediction from Markov is exactly the use that the data
rejected.

## 8. What should be changed first?

Three changes, in order of confidence:

1. **Stop using the 21-EMA trail for breakout exits.** PF 0.08 — it's the
   worst exit reason in the entire dataset. Either widen the trail
   (e.g., `EMA21 − 1×ATR`) or replace it with a momentum-based exit.
2. **Add a VWAP-distance filter** — block all entries when
   `vwap_distance_pct < -1.5%`. 0 wins out of 15 in that bucket.
3. **Stop using long pullback (RSI<32)** as currently defined. Replace
   with the proposed 50-EMA pullback (Phase 4 #2) and walk-forward.

All three are testable in walk-forward without new data or new modules.

## 9. What should NOT be touched yet?

- **The live worker.** Phase 12 of every prior research round, restated:
  no Markov / no v3 logic into `loop.py` until the next walk-forward
  beats `strategy_routing` OOS.
- **Reflection allowlist.** Currently set to v2 knobs; expanding it to
  cover Markov yaml or new setup parameters before the new design is
  walk-forward-validated risks letting the LLM overfit.
- **The Markov state alphabet.** Holding it fixed lets cross-experiment
  PFs be compared apples-to-apples.
- **`strategy_routing` route definitions.** These are the only edits that
  improved OOS results. Don't change the routes while exploring other
  signal layers — that's how you accidentally overfit two things at
  once.
- **The fee/slippage assumptions.** The 10 bps + 5 bps model is on the
  cheap end of realistic. Don't tune it down to make results look
  better.

## 10. What is the next best experiment?

**Donchian-20 trend-following breakout, gated by the Markov
`strategy_routing` filter.** Reasons (Phase 3 + Phase 4):

- The 16+-bar bucket carries the OOS edge — that bucket is trend-trade
  territory. The current 3-bar-play is an indirect proxy.
- Donchian-20 is the most direct trend-continuation entry in the
  proposal list. Two hyperparameters (period, channel width) — minimal
  overfit surface.
- Keeping `strategy_routing` as the gate inherits the known-good Markov
  filter. We are not stacking two unproven changes.

**Concrete plan:**

1. Add `setups.donchian` block to a new `strategy_v3.yaml`.
2. Add Donchian-high / Donchian-low to `signals.compute_indicators`.
3. Walk-forward 24mo BTC 4h, three variants:
   - Donchian alone, no Markov.
   - Donchian + `strategy_routing` Markov.
   - Donchian + RSI pullback combined.
4. Compare to current Phase-5 leaderboard above. **Adopt only if Donchian +
   routing beats `strategy_routing`'s OOS PF 2.17 with trade count ≥ 30.**
5. If it doesn't beat: move to candidate #2 (volatility targeting), repeat.

**Do NOT** try to tune Donchian parameters to make it beat. If 20-bar
doesn't work, 15-bar and 25-bar won't be more honest.

---

## Hard rules — restated

- **No live trading changes.** `loop.py` stays on the existing v2 long-
  short. Markov stays OFF (`HERMES_MARKOV_ENABLE` unset) until OOS
  beats the floor.
- **No full-history optimisation.** Walk-forward only. The 33-trade
  baseline OOS was computed exactly once; every variant is judged
  against it without retuning.
- **No in-sample wins reported as success.** The Phase-1 backtest was
  +0.42% on full history — that is the *floor* not the result.
- **If a change doesn't help, say so.** Phase 5's combined routing+sizing
  hurt; we report it as hurt. No spin.
