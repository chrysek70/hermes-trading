# Current adaptation audit (Issue #32, Phase 1)

This is the honest pre-implementation audit of how much "learning"
and online adaptation the bot actually does today, before the Issue
#32 online walk-forward simulator is introduced. Code references
were verified against the repo at commit `76fed21`.

The goal is to set a clean baseline: list every place the bot might
appear to adapt, then state whether it actually does, so the
simulator built in Phases 2+ can be measured against an unambiguous
"no live adaptation" reference.

## TL;DR

**The bot is not currently learning in any meaningful sense in the
adopted multi-asset live path.** The strategy parameters
(SuperTrend(10, 3), EMA50/200, RSI(14), funding-filter thresholds),
the per-asset size, and the portfolio cap are all fixed at the
yaml-config level. The only thing the live loop changes on its own
during a session is the SuperTrend trailing stop, which ratchets
intra-bar in `signals.long_exit` / `signals.short_exit`. That is
indicator behaviour, not adaptation.

There is an older single-asset reflection module (`reflect.py`)
that is wired into `loop.run` and writes back into the strategy
yaml, but the **multi-asset live path (`multi_loop.run`) does not
call it**. The decay monitor (`scripts/monitor_strategy_decay.py`)
exists and detects degradation, but it is a **report-only tool** —
it does not feed back into the worker.

**Update during write-up**: while this audit was being written, a
parallel agent shipped Issue #33 — a `LiveVolSizingOverlay` class
inside `hermes_trading/multi_loop.py` that can apply Issue #27's
vol-quartile sizing to the live worker. It is **opt-in via a
separate yaml** (`state/live_multiasset_long_short_funding_vol.yaml`)
and is **disabled in the currently adopted live config**
(`state/live_multiasset_long_short_funding.yaml`). When enabled it
multiplies `size_per_asset` by 1.00 / 0.50 / 0.25 based on the
trailing 12-month per-asset realised-vol quartile. That is the
first real adaptive Risk-layer feature in the live path, but it
remains off in the adopted candidate, so the substantive answers
in this audit are unchanged: today's adopted live configuration
runs at fixed size.

## Question-by-question

### 1. Is the current bot actually learning?

No. The multi-asset live worker (`hermes_trading/multi_loop.py`,
which is the path `state/live_multiasset_long_short_funding.yaml`
boots) re-reads the strategy yaml each loop iteration but **never
modifies it**. There is no parameter update step. There is no
performance memory carried forward into the next decision. The
indicator state (SuperTrend line, EMA, ATR) is stateless across
ticks — it is recomputed from the raw OHLCV window every poll.

The only across-tick state the worker carries is:

- the open per-asset positions in `state/positions/<KEY>.json`
- the realised-PnL counter for the session
- the funding-overlay rolling-percentile series loaded at boot
- the consecutive-failure counter for the per-asset circuit breaker

None of those are "learned" — they are accounting state.

### 2. Which parts are fixed rules?

Everything that determines what trade gets taken, at what size, and
when to exit. From the adopted config
(`state/live_multiasset_long_short_funding.yaml`) and the strategy
it points at (`state/strategy_supertrend_long_short.yaml`):

- SuperTrend period (10) and multiplier (3.0)
- EMA fast (50) / slow (200) regime gate
- RSI period (14)
- ATR period (14)
- Funding-filter block thresholds (long ≥ 95th pct, short ≤ 5th pct)
- Funding rolling-percentile window (180 bars = 30 days)
- `size_per_asset` (0.5)
- `max_open_positions` (2)
- `position_size_r` (0.5)
- `max_hold_bars` (240) — global, but unused in SuperTrend mode
  because `max_holding_bars: 0` on the SuperTrend setup disables it.
- Per-side fee (10 bps) and slippage (5 bps) — Issue #29 constants.

All of these are read from disk each loop and applied verbatim.
None is updated by the worker.

### 3. Which parts are adaptive?

The intra-bar SuperTrend trailing stop, and that's about it.
Specifically in `hermes_trading/signals.py`:

- `long_exit` (line 388) ratchets `position["stop"]` up to the
  current bar's `supertrend_line` whenever the line is higher than
  the prior stop.
- `short_exit` (line 339) ratchets `position["stop"]` down to the
  current bar's `supertrend_line` whenever the line is lower than
  the prior stop.
- The donchian exit (line 405) similarly ratchets a trail.

These are indicator-driven trailing stops. They react to price
movement within a trade but do not learn or change strategy params
between trades.

The funding overlay rolling-percentile is loaded once at worker
boot (`LiveFundingOverlay.__init__`) and never updated during a
session. It is not learning; it is just a static lookup table over
historical funding rates.

### 4. Is `reflect.py` still relevant?

In the single-asset path it is invoked by `loop.run` (see
`hermes_trading/loop.py` lines 25, 160-174, 198-204 and the
`_trigger_reflection` call site). It can rewrite a small allowlist
of strategy yaml keys after every N closed trades.

For the currently adopted multi-asset SuperTrend long-short
candidate the answer is **no, it is not relevant**:

- `multi_loop.py` does not import or call `reflect`.
- The reflect allowlist was designed for v2 single-asset long-short
  keys (`rsi_period`, `breakout.exit.trail_ema`, etc). It was never
  validated for SuperTrend(10, 3), the funding filter, or the
  multi-asset coordinator.
- The shared strategy yaml is read by both BTC and ETH — letting
  reflection rewrite a key for one asset's outcomes would silently
  rewrite both, which is unsafe.

So reflection currently has no effect on the adopted live path,
even though it is still present and wired for the legacy single-
asset modes.

### 5. Is reflection disabled in multi-asset mode?

Yes, by omission. The module-level docstring at the top of
`hermes_trading/multi_loop.py` explicitly states reflection is
intentionally disabled in multi-asset mode (lines 18-21), and no
call site in that file invokes the module. `grep reflect
hermes_trading/multi_loop.py` returns only one hit, in an Issue #24
comment, which is unrelated. So the disabling is structural, not
runtime-toggled.

### 6. Does the live worker change parameters automatically?

No. The strategy yaml (`state/strategy_supertrend_long_short.yaml`)
and the live yaml (`state/live_multiasset_long_short_funding.yaml`)
are both **read-only** from the live worker's perspective. The
worker calls `load_yaml(strategy_path)` each tick (line 455 in
`multi_loop.py`) but never `save_yaml`. There is no API in the
multi-asset path that would mutate the yaml on disk.

### 7. Does the live worker change sizing automatically?

No. `size_per_asset` is read at boot (line 387) and used verbatim
on every entry (lines 565, 615). No conditional reduces it after
losses, after a regime change, or after a drawdown. The Issue #27
research showed vol_sizing can profitably reduce exposure on
adverse vol bands, but that is research-only and was not wired
into the live path. Issue #29 only changed the fill convention
(fee + slippage), not sizing logic.

### 8. Does the live worker pause after bad performance?

No. There is no daily-stop, monthly-stop, drawdown-based pause, or
cooldown after consecutive losses in `multi_loop.run`. The
`circuit_break_after` knob (default 5) exists but it counts
**fetch failures**, not strategy losses — it pauses an asset when
its price feed keeps erroring, not when its trades lose money.

### 9. Does the live worker detect regime decay automatically?

No automatic detection in the live worker itself. The detection
exists as a standalone CLI: `scripts/monitor_strategy_decay.py`.
That script:

- reads `state/trades.jsonl` (the live worker's append-only trade log)
- computes rolling profit factor / drawdown / win rate over the
  last 10 / 25 / 50 trade windows
- prints a verdict (degrading / stable / improving)
- writes `research/decay_monitor_report.md`

It does NOT call into the live worker, does NOT mutate any config,
and does NOT feed back into trading. It is a manual diagnostic
the operator runs. From `multi_loop.py` line 199 the worker's own
trade-row builder simply mentions the decay monitor as a
downstream consumer of the trade-log fields, nothing more.

### 10. What is currently missing for true online adaptation?

A list, ordered roughly by safety to implement live (safest first):

1. **A closed-trade rolling memory in the live worker** — today the
   worker has no in-memory representation of recent closed-trade
   PnL or exit reasons. The trade rows are appended to
   `state/trades.jsonl` and forgotten. Any adaptive rule needs at
   minimum a rolling window of the last N closed trades available
   to the entry-decision code.

2. **Regime-aware live sizing.** Issue #27 already proved
   vol_sizing (per-asset rolling realised vol, train-window
   quartile thresholds) cuts max DD by ~50% without changing trade
   count. The live worker has none of that — sizing is constant
   regardless of volatility regime.

3. **Decay-driven exposure reduction.** The decay monitor already
   computes a "performance is degrading" verdict. Nothing
   automatically reduces position size or pauses entries when that
   verdict fires.

4. **Consecutive-loss throttling.** Common risk hygiene; not
   present anywhere — neither in the live worker nor in any
   research overlay. After N consecutive losses, halve the size.

5. **Stop-cluster detection.** When most recent exits are stops
   (not target / flip), that is information; today nothing acts on
   it. Stop clustering tends to precede regime flips.

6. **A simulator that replays bars one-at-a-time, exposes a
   "closed-trade memory" object, and gives candidate adaptive
   rules access to it WITHOUT future leakage.** Currently the
   only walk-forward tools (`run_adaptive_sizing.py`,
   `replay_live.py`) either size from train-window statistics
   (fold-level, not online) or do not size at all. Neither tests
   a true online feedback loop.

The Issue #32 simulator (`scripts/run_online_walk_forward.py`)
addresses item 6 directly, and uses items 1-5 as the candidate
rules it evaluates.

## Cross-check against the architecture map

`ARCHITECTURE.md` (Alpha / Risk / Execution / Diagnostics /
Research layers) classifies sizing as a Risk-layer responsibility.
The adopted live candidate has Risk = "fixed per-asset size, hard
funding gate, intra-bar trailing stop". Everything else in the
Risk layer (regime-aware sizing, decay throttle, consecutive-loss
throttle) is unbuilt in the live path even though pieces have been
researched offline.

## Implications for the simulator

The simulator should treat the current adopted candidate as the
`none` adaptive rule — same trade count and same PnL distribution
the live worker would produce. Every other adaptive rule should
plug into the Risk layer (sizing multiplier), leave the Alpha
layer (entry / exit signals) untouched, and decide its multiplier
purely from a closed-trade memory or a causal volatility band
that is strictly past-only at every decision point.

That is what Phases 2-6 of Issue #32 build.
