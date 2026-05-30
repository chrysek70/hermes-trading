# Phase 1 — Current Trading Logic Audit

Reading-only audit of what the bot actually decides, where the rules live in
code, and where the live worker diverges from the backtester. No code changed
in this phase.

---

## 1. Current LIVE strategy

- **Strategy file**: `state/strategy.yaml` — `version: "10"`, `schema: "v2-long-short"`.
- **Decision timeframe**: 4h (`state/goal.yaml`: `timeframe: "4h"`).
- **Engine**: `hermes_trading/signals.py` (same module the backtester runs —
  so live mechanics mirror backtest mechanics for the signal layer).
- **Worker loop**: `hermes_trading/loop.py` `run()` — polls every 10 s
  (`HERMES_POLL_SECONDS` default 10), pulls 300 bars at the strategy TF
  (`indicator_limit: 300` in `goal.yaml`), evaluates the last row.
- **Position sizing**: `risk.position_size_r = 0.5` (50% of a notional unit
  defined by `HERMES_PAPER_NOTIONAL_USD` for $-display only — paper, not real).
- **Markov filter**: **OFF** by default. The walk-forward proved hard-filter
  Markov hurts OOS (PF 0.65 → 0.60 on 1h, 1.25 → 0.89 on 4h). The wiring exists
  via `HERMES_MARKOV_ENABLE=1` but reads only v1 yaml keys (`allowed_long_states`,
  `min_prob_same_or_up`) — see §15.

## 2. Current BACKTESTED strategy

- Same `signals.py` engine.
- `state/strategy_v2_long_short.yaml` is the file the walk-forward uses for the
  Markov research sweep. It is byte-identical to the live `strategy.yaml`'s
  setups/exits/risk block — the only diff is the version string. So **backtest
  and live use the same trading rules**.
- Costs the backtester models that live does NOT (see §14):
  fee 0.001/side, slippage 0.0005, both injected at fill price.

## 3. Exact entry rules

Defined in `hermes_trading/signals.py`. The decision is "is this bar at close
an entry?" using indicators computed on bars 0..i (causal).

### Regime gate (`_bullish_regime` / `_bearish_regime`)

- `bullish` ⇔ `ema_fast > ema_slow` (ema_fast = EMA50, ema_slow = EMA200).
- `bearish` ⇔ `ema_fast < ema_slow`.
- Neither true if equal.

### Long entries (`signals.long_entry`) — bullish regime only

Returns a setup string `"pullback"` or `"breakout"` or `None`.

- **`pullback`** fires when ALL of:
  - `setups.pullback.enabled: true` (default true)
  - `_bullish_regime(row)` is true
  - `rsi < 32` (`setups.pullback.rsi_threshold`)
  - `close <= ema_pull * (1 + 0.002)` (within 0.2% of the 21-EMA — `ema_tol`)
  - `close >= ema_slow` (don't buy under the 200-EMA)
- **`breakout`** fires when ALL of:
  - `setups.breakout.enabled: true`
  - `_bullish_regime(row)` is true
  - `three_bar` column is True at this row (3-bar play fired)
  - if `require_above_vwap: true`: `close > vwap` (session VWAP, UTC-reset)
- Priority: pullback is checked first, breakout second. If pullback fires,
  breakout is not considered this bar.

### Short entries (`signals.short_entry`) — bearish regime only

Returns `"pullback_short"`, `"breakout_short"`, or `None`. Requires
`shorts.enabled: true` (yes in v10).

- **`pullback_short`**: bearish regime AND `rsi > 68` AND `close >= ema_pull*(1-0.002)`
  AND `close <= ema_slow`.
- **`breakout_short`**: bearish regime AND `three_bar_short` (bearish 3-bar
  play: down ignition + inside bar + breakdown) AND (if `require_below_vwap: true`)
  `close < vwap`.

### Backtest entry priority

In `backtest.py:_run_state_machine`, when `position is None`:
1. Try long if `markov_long_allowed` (default True) AND `size_mult > 0`.
2. If no long fires, try short (gated only by `size_mult > 0` — **bug noted
   in code review: short ignores `markov_long_allowed`**).
3. Only one entry per bar; first hit wins; routing's `allowed_setups` filter
   applies if Markov sets one.

## 4. Exact exit rules

Defined per-direction in `signals.py`.

### Long exits (`signals.long_exit`)

Checked every bar while the position is open. **Order matters** — first hit wins:

1. **Intrabar stop**: `low <= position["stop"]` → reason `"stop"`.
2. **Regime flip**: if `risk.regime_flip_exit: true` AND `ema_fast < ema_slow`
   → reason `"regime_flip"`.
3. **Time stop**: if `risk.max_hold_bars > 0` AND bars_held >= 240
   → reason `"time_stop"`.
4. **Per-setup exit**:
   - If setup's `exit.type: "mean_revert"` (pullback): when `rsi >= 55`
     (`target_rsi`) → reason `"target_rsi"`.
   - If setup's `exit.type: "trail"` (breakout): update `position["stop"]` to
     `max(position["stop"], ema_pull)` (ratchet up only); then if
     `close < ema_pull` → reason `"trail_exit"`.

### Short exits (`signals.short_exit`)

Mirror logic with inversions:

1. `high >= position["stop"]` → `"stop"`.
2. `ema_fast > ema_slow` → `"regime_flip"`.
3. Time stop same.
4. `mean_revert`: `rsi <= 45` (short `target_rsi`).
   `trail`: ratchet `position["stop"]` down to `min(stop, ema_pull)`, exit when
   `close > ema_pull`.

### End-of-data mark-out

In the backtester only — any position open at the last bar is closed at the
last bar's close (with slippage), reason `"end"`. Live never hits this.

## 5. Stop-loss rules

- **Initial stop** at entry (`signals.initial_stop` / `initial_stop_short`):
  - Long pullback: `entry_close - 1.2 × ATR(14)` (`stop_atr_mult: 1.2`)
  - Long breakout: `entry_close - 1.5 × ATR(14)` (`stop_atr_mult: 1.5`)
  - Short pullback: `entry_close + 1.2 × ATR(14)`
  - Short breakout: `entry_close + 1.5 × ATR(14)`
- **ATR** is Wilder's (`signals.atr`), period 14.
- **Fill on stop** in backtest: `position["stop"] × (1 ± slippage)` — assumes
  fill AT the stop level (no gap modelled).
- **Live** does NOT compute fills the same way; the live worker uses the
  `position["stop"]` set at entry but the trade is closed at `last_price`, not
  at the stop price. So live PnL on stop hits is whatever the next 10 s tick's
  close was — likely past the stop.

## 6. Trailing-stop rules

Only applies to `trail`-type exits (breakout setups by default).

- Long: `if ema_pull > position["stop"]: position["stop"] = ema_pull`.
  Stop ratchets UP only (never down). Exit when `close < ema_pull`.
- Short: mirror — stop ratchets DOWN, exit when `close > ema_pull`.
- `ema_pull` is the 21-EMA (`setups.{pullback,breakout}.pullback_ema: 21` —
  shared default; even breakout's "trail_ema: 21" is informational; code
  trails on `ema_pull`).

## 7. Position-sizing rules

- **Base size**: `risk.position_size_r = 0.5` (fraction of unit notional;
  paper).
- **Effective size**: `base × markov_size_multiplier`. Live uses 1.0
  unconditionally; the backtester applies whatever `decisions_df` supplies.
- **No volatility targeting**, no Kelly, no equity-curve sizing. Size is
  flat per trade across the entire history.
- **One position at a time**. No pyramiding, no scale-ins.

## 8. Fees / slippage

In `backtest._run_state_machine.close_trade`:

```
gross = (exit_fill - entry_fill) / entry_fill           # long
gross = (entry_fill - exit_fill) / entry_fill           # short
effective_size = base_size × position["size_multiplier"]
net = (gross - 2 × fee) × effective_size
```

- `fee` default `0.001` (10 bps) per side → 20 bps round trip applied as a
  return reduction (NOT a cash deduction proportional to notional; for
  paper-mode this is a simplification but consistent).
- `slippage` default `0.0005` (5 bps) applied at fill price (entry pays up,
  exit gets hit down — symmetric).
- Entry slippage adverse: long buys at `close × (1 + slip)`, short sells at
  `close × (1 - slip)`.
- Exit slippage adverse: long sells at `close × (1 - slip)` or `stop × (1 - slip)`,
  short buys back at `close × (1 + slip)` or `stop × (1 + slip)`.

## 9. How Markov affects trades (backtest only — not live)

Five modes in `markov_regime.compute_decisions`:

| Mode | Per bar emits |
|---|---|
| `disabled` | size=1.0, long_allowed=True, allowed_setups=None |
| `hard_filter` | size=1.0, long_allowed = `stable in favorable AND P(allowed_next) ≥ min_prob` |
| `soft_sizing` | size ∈ [min_score, max_score] from `w_cs·1{fav} + w_tr·P(next→fav)`; long_allowed=True |
| `bad_regime_avoidance` | size=`reduce_size_to` (0.25) if state ∈ train-derived bad_set, else 1.0 |
| `strategy_routing` | size = route's `size_multiplier`, allowed_setups = route's list |

Backtester reads `markov_long_allowed`, `markov_size_multiplier`,
`markov_allowed_setups` columns and applies them at entry (gate, sizing,
setup filter). Exits are NEVER gated by Markov — positions exit purely on
the signal-layer exit rules.

## 10. Which setup fires most often

Phase-2 data will give exact counts. Prior walk-forward (24 mo BTC 4h, no
Markov, 33 trades total):

- Breakout (long+short): ~24 trades (~72%)
- Pullback (long+short): ~9 trades (~28%)

Breakout dominates because the 3-bar play has a wider trigger condition
than the narrow `RSI<32 AND at 21EMA AND above 200EMA` pullback filter.

## 11. Which setup makes money

From prior 24-mo OOS walk-forward by-state breakdown (no Markov, all setups):

| State | n | PF | exp |
|---|---|---|---|
| up_high_vol | 10 | 2.68 | +0.63% |
| up_low_vol | 10 | 2.14 | +0.53% |
| down_high_vol | 7 | 0.54 | -0.19% |
| down_low_vol | 5 | 0.77 | -0.08% |

So:
- Longs in up regimes are the profit engine.
- Shorts in down regimes are roughly net-negative.
- `strategy_routing` blocked the down-state entries → +9.23% / PF 2.17 vs
  baseline +8.97% / PF 1.69.

Per-setup is what Phase 2 will quantify with the detailed trade log — until
then I only know aggregate state-grouped numbers.

## 12. Which setup loses money

Same data — losers are concentrated in:
- Down regimes, especially `down_high_vol` (PF 0.54).
- Likely the short setups themselves (need Phase-2 confirmation), since they
  only fire in bearish regimes.

## 13. Which exit reason is best / worst

Prior aggregate (24 mo OOS, no Markov):
- `stop`: dominant exit, ~80% of trades, average loss small but frequent.
- `target_rsi`: pullback's RSI≥55, smaller share, average win moderate.
- `trail_exit`: breakout trailing 21-EMA exit; this is where the BIG winners
  live — `avg_win +0.93%` overall is driven by trail exits in trends.
- `regime_flip`: rare (1 in 8-fold walk-forward).
- `time_stop`: not observed in any 24-mo OOS run — `max_hold_bars=240` is
  ~40 days of 4h bars, never reached.
- `end`: rare; only triggers if a position was open at the last bar.

Exact counts by exit_reason × setup × direction in Phase 3.

## 14. Live worker vs backtest — matches and mismatches

| Concern | Live (`loop.py`) | Backtest (`backtest.py`) |
|---|---|---|
| Indicators | `signals.compute_indicators` | same |
| Entry/exit logic | `signals.{long,short}_{entry,exit}` | same |
| Fee modelling | **none** | 0.001/side |
| Slippage | **none** (fills at `last` close) | 0.0005 |
| Stop fill | at `last` close (10 s later) | at `stop × (1 ± slip)` exact |
| Size multiplier | always 1.0 (Markov v2 wiring incomplete) | `decisions_df` |
| Setup filter (routing) | not honored | honored |
| Exits | same logic | same logic |
| `bars_held` | from wall-clock vs opened_at | from index diff |

**Net effect**: live paper PnL will look *better* than backtest of the same
trades by ~25 bps per round-trip (10 fee + 5 slip both sides). When the worker
shows a +0.50% trade, the backtester would have logged ~+0.25% net. So the
live tick log overstates the strategy's net edge.

## 15. Config-vs-code mismatches found

1. **Live Markov path reads v1 keys only.** `loop.py:_init_markov_live` reads
   `cfg["allowed_long_states"]` and `cfg["min_prob_same_or_up"]`. With the
   migrated v2 yaml shape (`sizing.favorable_states`, no top-level
   `allowed_long_states`), these are empty/0.5 → `regime_allowed_long`
   permanently False → no longs ever open with `HERMES_MARKOV_ENABLE=1`.
2. **Yaml keys never read by code.** `state/markov_regime.yaml`:
   `model.type`, `model.order`, `state.method`, `validation.walk_forward_only`,
   `validation.train_months`, `validation.test_months`, `validation.embargo_bars`,
   `use_as_filter`. The `validation.*` keys look load-bearing but
   `run_markov_research.py` reads embargo/train/test from argparse defaults,
   never from yaml.
3. **`strategy_routing.enabled` is a no-op** in `compute_decisions` —
   routes apply whenever `mode == "strategy_routing"`, ignoring the
   `enabled` flag.
4. **`shorts.{pullback,breakout}.trail_ema`** in v2-long-short.yaml is
   informational only — `signals.long_exit/short_exit` trail under
   `ema_pull` regardless. The trail_ema yaml key has no effect.
5. **`one_variable_only: true`** in `goal.yaml` is documentation — enforced
   structurally by reflect.py rather than read at runtime.
6. **Hysteresis (`state.hysteresis_bars: 3`)** is wired in
   `markov_regime.compute_decisions` but NOT used by the v1 `long_permission_score`
   path that the live worker reaches via `_init_markov_live`.
7. **Routing `size_multiplier=0` blocks both longs and shorts** because the
   short branch checks `size_mult > 0.0` as its gate. Routing was likely
   intended to be long-side; shorts piggyback unintentionally.

Phase 2 will add diagnostics to a detailed trade log and Phase 3 will
quantify the per-setup, per-state, per-regime profitability.
