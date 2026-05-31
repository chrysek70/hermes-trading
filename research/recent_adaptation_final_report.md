# Final synthesis — strategy decay diagnosis and adaptation recommendation

Author: research agent (autonomous run, 2026-05-31)
Investigation scope: is the adopted live candidate
(`state/live_multiasset_long_short_funding.yaml`, Issue #20) still
valid after the bad recent 3-month window, and what — if anything —
should change?

This is the synthesis of Phases 1 – 5. See:

- `research/recent_regime_failure_report.md` — Phase 1 diagnostic
- `research/rolling_decay_report.md` — Phase 2 historical distribution
- `research/timeframe_comparison_report.md` — Phase 3 TF sweep
- `research/multitimeframe_confirmation_report.md` — Phase 4 MTF variants
- `research/adaptive_regime_response_report.md` — Phase 5 risk-layer rules

---

## TL;DR

**The adopted strategy is NOT decayed.** The recent 3-month bad window
is statistical noise that sits comfortably within the historical
distribution of bad runs the strategy has produced in 48 months. The
walk-forward OOS estimator that earned Issue #20 adoption still
returns the SAME numbers on the SAME data: 123 trades, +139.71%
return, 4.64% DD, 3.35 PF.

What changed is not the strategy. What changed is the user's
*subjective* tolerance for continuous-equity drawdown, after watching
a chop streak unfold live.

The right response is a **risk-layer adaptation** that detects high
volatility and reduces exposure, NOT an alpha-layer change. The
specific recommendation is the Phase-5 rule **R6 (volatility-quartile
sizing)**, which is the same overlay Issue #27 already validated as
the strongest sizing candidate. Wiring it to live would cut the
recent 3-month DD from 4.40% to 1.44% (in walk-forward OOS) while
preserving the alpha decision set.

---

## Answers to the 9 spec questions

### 1. Is the adopted strategy still valid?

**Yes.** Walk-forward OOS on the same 48-month window the strategy
was adopted on returns the same numbers it returned at adoption:
123 trades, +139.71%, 4.64% DD, PF 3.35. The recent 3-month
sub-window shows +8.56% / DD 4.40% / PF 2.36 on 13 walk-forward
trades — positive, well above the adoption gate floor.

### 2. Did recent market conditions invalidate it?

**No, but they exposed an unfair-exposure problem.** The recent
chop drove 10 closed-equity-replay trades in continuous mode, 8 of
which were stops, all of which were taken at full size. The
strategy did exactly what it does in chop. The market did exactly
what markets do (whipsaw). The mismatch is that the strategy has
no exposure governor on chop. Issue #27 already identified this
and recommended vol-quartile sizing; the recent window is the
empirical case for finally wiring it.

### 3. Is 4h still the right timeframe?

**Yes.** Phase 3 tested 1h, 2h, 4h, 1d on the SAME long-short funding
strategy. 4h has the highest PF (3.35 vs 2.10-2.26 on 1h/2h, 2.19
on 1d) and the lowest DD (4.64% vs 7.19-8.25% on 1h/2h, 15.13% on
1d). No other timeframe cleanly dominates 4h on both axes.

### 4. Should we add 1h or 15m?

**No.** 1h overtrades for the same return-per-trade gain (561 vs 123
trades) at materially worse DD. 15m / 5m were already rejected by
the v1 RSI experiment (fees dominate edge). The Phase 5 rules give
better DD reduction than a TF change without losing alpha.

### 5. Should we use multi-timeframe confirmation?

**No.** Phase 4 tested four MTF variants (A: 1d agreement filter,
B: 1h agreement filter, C: agreement-scaled size, D: 1h early
exit). All four fail the Issue #20 adoption gate. The best
variant (A_1d_agree) costs 27% of total return for a 2.97% DD
vs 4.64% baseline — a worse trade-off than R6 (vol sizing) which
only costs 48% of return on 48mo but cuts DD to 2.10%.

Variant D (1h early-warning exit) actively destroys the strategy
(48mo PF 1.54 vs 3.35). Never use 1h to early-exit a 4h trade.

### 6. Should the bot adapt by changing signals or by changing size?

**By changing size.** The architecture in `ARCHITECTURE.md`
explicitly maps "regime detection that scales exposure" to the
Risk layer, not the Alpha layer. Phase 5 R6 (vol-quartile sizing)
delivers the largest DD improvement of any tested rule (4.64%
→ 2.10% on 48mo, 4.40% → 1.44% on recent 3mo) by adjusting
exposure causally on realised volatility. All 123 alpha-fired
trades are kept — none are blocked. The alpha decision set is
preserved.

### 7. What exact live change should be next, if any?

**Switch the live worker from
`state/live_multiasset_long_short_funding.yaml` to
`state/live_multiasset_long_short_funding_vol.yaml`** — the
vol-sizing opt-in config already shipped under Issue #33.

The required infrastructure already exists (Issue #33: `LiveVolSizingOverlay`
class in `hermes_trading/multi_loop.py`, opt-in config yaml,
Section 16 self-tests covering low/mid/high bucket assignment with no
future leak). The locked parameters in
`state/live_multiasset_long_short_funding_vol.yaml` match this
report's R6 specification exactly:

| field | value | matches R6 |
|---|---|:---:|
| `vol_sizing.enabled` | `true` | ✓ |
| `vol_sizing.window_bars` | `24` | ✓ (4 days at 4h) |
| `vol_sizing.train_months` | `12` | ✓ (rolling refit) |
| `vol_sizing.mult_low` | `1.00` | ✓ (Q1) |
| `vol_sizing.mult_mid` | `0.50` | ✓ (Q2/Q3) |
| `vol_sizing.mult_high` | `0.25` | ✓ (Q4) |

So the user action is one yaml swap, not a code change:

```bash
# stop existing default worker
# (or run a parallel canary)

uv run python -m hermes_trading.run \
    --config state/live_multiasset_long_short_funding_vol.yaml --verbose
```

No alpha change. No timeframe change. No MTF gating. Just turn
on the existing risk-layer overlay that was shipped after
Issue #27 → #33 and has been waiting for the user's approval.

Suggested forward-test ~30 trades on the vol-sized config in
parallel with the existing default before any operator-side
"default switch". The two configs differ only by sizing, so
side-by-side comparison is straightforward.

### 8. Should the current live worker keep running while research continues?

**Yes.** The strategy is not decayed. The recent loss streak is
within the historical distribution. The cost of pausing the live
worker today is the opportunity cost of any winning streak that
follows the current chop. The recent 90-day continuous-replay
return is -2.78%, comparable to the 25th-percentile historical 90-day
window — the worst 48mo windows are at -10.31% and the strategy
recovered from those too.

The decay monitor's current defaults (PF < 1.20, CL ≥ 4, DD > 1.25×
baseline) would trigger DEGRADED on the recent window, but they
would also have triggered on ~50% of all historical 10-trade
windows (false-positive rate at PF < 1.20 is 53.7%; at trailing-CL
≥ 4 is 26.1%). So the monitor is **not** providing actionable
information at current thresholds. The Phase 2 analysis suggests
raising the action threshold to "trailing-CL ≥ 8 AND 30-day return
≤ -8%" before any auto-pause — that would have fired roughly once
in 48 months and not in the recent window.

### 9. What would a hedge-fund-style response look like here?

A serious shop seeing this drawdown would:

1. **Verify the OOS estimator is still valid.** Done — re-run the
   walk-forward, confirm 123 / +139.71% / 4.64% / 3.35 reproduce
   exactly. ✓
2. **Quantify the recent move against the historical distribution.**
   Done — Phase 2 shows the recent 90d sits at ~p21 of the
   48-month distribution, well above the historical worst of -10.31%.
   ✓
3. **Identify the root cause of the recent move.** Done — Phase 1
   shows clustered chop in HIGH realised-vol bars; alpha was
   internally consistent (9/10 had 1h SuperTrend agreement); the
   funding gate had nothing to block; the SuperTrend stops were
   correctly placed. ✓
4. **Test whether a risk-layer adaptation reduces damage in this
   regime without breaking the long-term distribution.** Done —
   Phase 5 R6 cuts recent 3mo DD by 67% and 48mo DD by 55%, with
   a 48% return give-up on the full window. ✓
5. **Do NOT change the alpha.** A shop would not respond to a
   single chop window by retuning indicators. Period. ✓
6. **Implement the risk-layer fix in a measured way.** A canary
   deploy: keep the current live config running on half of the
   nominal capital, run the new vol-sized config in parallel paper
   on the other half, compare for one quarter, then promote the
   winner. This bot has no real-money capital, but the equivalent
   for paper trading is: leave `state/live_multiasset_long_short_funding.yaml`
   as-is, add a new `state/live_multiasset_long_short_funding_vol_sized.yaml`
   variant, run both in parallel, compare. (Multi-config worker
   support is not built today — see ROADMAP / Diagnostics backlog.)
7. **Set a more realistic decay alarm.** Raise the action threshold
   per Phase 2 — don't let the monitor cry wolf on the 50% of
   chop windows. Use vol-sizing as the *continuous* response to
   regime, not a binary "stop trading" alarm.

In short: the recent window is a normal-distribution event. The
strategy passed every revalidation check. The response is the
risk-layer fix that was already on the roadmap.

---

## The user's stated philosophy

> "Do not try to make every 3-month period profitable. That is
> unrealistic. But the bot should detect bad regimes and reduce
> damage."

This synthesis matches the philosophy exactly. The bot does not
need to be retuned to win every chop window — that's impossible
without overfitting. It needs a risk-layer signal that turns
exposure down when realised volatility is high. That signal is
the vol-quartile sizing rule from Issue #27, re-confirmed in
Phase 5 here, recommended for adoption.
