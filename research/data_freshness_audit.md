# Data freshness / staleness audit (Issue #39)

Read-only audit of every overlay's data-source and freshness behaviour.
Triggered by a live `--config
state/live_multiasset_long_short_funding_vol_volconf.yaml` run that
showed `volume: signal=811.59 mean20=2292.76 ratio=0.35
decision=block_low_volume_flip` sustained across multiple polls.

**No code fixes in this commit.** Findings and recommendations only,
committed separately from any subsequent fix per the Issue #39 hard
rules.

## Methodology

For every overlay used by the live worker AND by replay:

1. Identify the data source (Binance Vision archives vs ccxt live
   feed vs caller-provided indicator frame).
2. Identify the loader's "latest available timestamp" behaviour as a
   function of wall-clock time.
3. Identify the per-tick lookup path (`state_at` / equivalent) and
   exactly which bar's data ends up driving the live decision.
4. Reproduce the Issue #38 headline numbers from a clean replay and
   compare to the live behaviour.

## Data-source map

| overlay | source | latest-available behaviour | per-tick lookup |
|---|---|---|---|
| `LiveFundingOverlay` (Issue #21) | Binance Vision monthly `fundingRate` archives via `funding.load_funding` | published only after month-end; current calendar month returns HTTP 404 | `funding.state_at(asset, ts)` slices `rates.loc[:ts_pd].iloc[-1]` |
| `LiveVolSizingOverlay` (Issue #33) | Binance Vision monthly 1m kline archives via `data.load_klines` → `resample('4h')` | same as funding — current month returns 404; data ends prior-month last day | `vol.state_at(asset, ts)` slices `realised_vol.loc[:ts_pd]`; quartiles fitted on trailing `train_months` of vol observations |
| `LiveVolumeConfirmationOverlay` (Issue #38) | Same as vol_sizing — Binance Vision via `data.load_klines` | same staleness pattern | `vol.state_at(asset, ts)` slices `volume.loc[:ts_pd].iloc[-1]` |
| Live worker price/volume stream (multi_loop's `signal_row`) | **ccxt fetch via `price_adapter`** (Kraken primary) — completely independent feed | always real-time | `signal_row = ind_df.iloc[-2]` of the ccxt indicator frame |
| Replay price/volume stream (replay_live's `signal_row`) | Same as the overlays — `data.load_klines` → `resample` | data ends prior-month last day | iterates historical bars; `signal_row = ind.iloc[i-1]` |

The first row of this table reveals the structural issue:

> **The live worker's signal_row comes from one feed (ccxt / Kraken)
> while every overlay reads from a different feed (Binance Vision
> historical archives).**

For the replay path this asymmetry doesn't exist — replay's signal_row
and every overlay both come from the same Binance Vision data, so
queries inside the data range work correctly.

## Freshness behaviour today (audit date 2026-05-31)

`pd.Timestamp.utcnow()` = 2026-05-31. Binance Vision May 2026
archives are not yet published. Every overlay therefore loads data
through 2026-04-30 inclusive (verified from the live worker output
the operator shared: `funding overlay loaded for BTC/USDT: 4290
records, span 2022-06-01 -> 2026-04-30`).

When the live worker queries `overlay.state_at(asset, ts)` with
`ts ∈ 2026-05-…`, the overlay slices `series.loc[:ts_pd]`. Because
the data ends earlier, `iloc[-1]` returns the bar at **2026-04-30**
— the same value every poll until June's archive lands.

| overlay | staleness impact | severity |
|---|---|---|
| funding | funding rates change every 8h on Binance and move slowly across regimes. Returning April 30's rate as a proxy for May's rate is incorrect, but the percentile (windowed over 180 bars / 30 days) only shifts modestly. The user's output `pct=36.7 decision=allow` is internally consistent with that data. | **low** — gate decisions are unlikely to flip from this staleness |
| vol_sizing (Issue #33) | vol regimes change on a multi-day-to-multi-week scale. Returning April 30's vol bucket as the "current" bucket is wrong by ~30 days but the bucket label is unlikely to swap categories from a 30-day shift. Mean reversion is sluggish. | **medium** — bucket may be off by one band; mid-band Q2_Q3 is the modal answer so most realistic outcomes still produce 0.5× sizing rather than 0.25× or 1.0× |
| volume_confirmation (Issue #38) | volume changes by an order of magnitude bar-to-bar. April 30's `signal_volume` has zero predictive relation to a May 31 bar's volume. The gate compares a stale snapshot to a stale 20-bar mean and produces a deterministic answer that does not refresh until the May archive publishes. | **high — gate is effectively a no-op (returns the same decision every tick) until next archive lands** |

This is the catastrophic asymmetry the operator observed live:
`signal=811.59 mean20=2292.76 ratio=0.35 decision=block_low_volume_flip`
on every poll, unchanged across many ticks.

## Replay reproducibility check

Re-ran the Issue #38 smoke test using the operator's exact command:

```bash
uv run python scripts/replay_live.py \
    --config state/live_multiasset_long_short_funding_vol_volconf.yaml \
    --n-months 3 --bars-per-second 5000 --quiet-flat \
    --trades-out /tmp/audit39/volconf_3mo.csv
```

Output (audit date 2026-05-31):

```
REPLAY SUMMARY  config=live_multiasset_long_short_funding_vol_volconf.yaml  2026-03-08 -> 2026-04-30
  trades                 4
  total return           -3.85%
  portfolio realized     -3.891%
  max drawdown           3.85%
  win rate               0.0%
  profit factor          0.00
  max concurrent open    2/2
  trades by asset        {'BTC/USDT': 3, 'ETH/USDT': 1}
  trades by direction    {'short': 1, 'long': 3}
  trades by exit reason  {'stop': 4}
  vol_sizing mean mult   0.625
  vol_sizing mean size   0.3125
  vol_sizing by bucket   {'Q2_Q3': 3, 'Q1': 1}
```

**The Issue #38 numbers (4 trades, DD 3.85%) are reproducible.** This
is the same machine, same code, same data. The volume gate fired and
pruned chop trades on bars where the signal volume was below the
trailing mean.

Sample per-bar decisions extracted from the verbose log:

```
volume BTC/USDT signal=7325.96 mean20=3546.91 ratio=2.07 decision=allow
volume BTC/USDT signal=1673.78 mean20=2311.21 ratio=0.72 decision=block_low_volume_flip
volume BTC/USDT signal=6766.75 mean20=2557.31 ratio=2.65 decision=allow
volume BTC/USDT signal=2037.40 mean20=2716.94 ratio=0.75 decision=block_low_volume_flip
```

Real ratios. Real allow/block toggling. Replay is not affected by the
staleness bug because replay walks historical bars *inside* the
Binance Vision data range — every queried timestamp has a real bar
under it.

Side-by-side with the no-vol baseline (also reproduced):

| config | trades | return | DD |
|---|---:|---:|---:|
| `funding.yaml` | 6 | −9.61% | 9.61% |
| `funding_vol_volconf.yaml` | 4 | −3.85% | 3.85% |

## The operator-reported "6 trades / 9.61%" discrepancy

The operator reported that replay with the volconf yaml still showed
6 trades / DD 9.61%, identical to the non-vol baseline. The numbers
the operator quoted (6 trades / DD 9.61%) match exactly the
`funding.yaml` (no-vol) replay output above.

There are four hypotheses for the gap between the operator's observation
and my reproduction. After the freshness analysis above, only one is
consistent with the code:

1. **Operator ran the no-vol yaml accidentally** (most likely): the
   operator's terminal scrollback may have shown a previous
   `--config state/live_multiasset_long_short_funding.yaml` invocation
   whose summary (6 trades / 9.61%) was conflated with the volconf
   run. The replay always prints the config name in the SUMMARY
   header — recommend the operator scroll back and verify.
2. **Operator's checkout was pre-Issue #38**: if `git log` on the
   operator's machine does not include commit `013390f`, the replay
   would have no vol_sizing-or-volume awareness for that yaml.
   Verify with `git log --oneline | head -3`.
3. **Operator confused the live worker output with replay output**: the
   live worker also tested by the operator shows `block_low_volume_flip`
   on every tick (the staleness bug above) but never opens any trade,
   so it produces zero trades, not six. Not a match.
4. **Actual replay code bug**: ruled out — re-reproduced with the
   exact command and got 4 trades / 3.85%.

If the operator can rerun the exact command above and still gets 6 /
9.61%, that is a real bug that I have NOT been able to reproduce
locally. Recommend the operator paste the SUMMARY header of their
replay output so the config file shown matches.

## Future-leak audit

For each overlay, "does the gate at bar T use data from bars ≥ T?"

- **funding**: `funding.state_at` slices `rates.loc[:ts_pd]` — strictly
  causal. Percentile is a 180-bar rolling rank over that same slice.
  **No future leak.**
- **vol_sizing**: rolling realised vol is built on the full series at
  boot (causally — `.rolling().std()` produces NaN until the window
  fills). Per-tick `_train_window_quartiles` slices `rv.loc[:ts_pd]`,
  then strips the bar at `ts_pd` if present (`train_slice.iloc[:-1]`
  when last index == ts), so the quartile thresholds at bar T use
  only vol observations strictly before T. **No future leak.** Tests
  in Section 16 / 17 confirm this directly.
- **volume_confirmation**: rolling mean is built on the full series at
  boot (causal). `volume.loc[:ts_pd].iloc[-1]` is the most recent bar
  ≤ T; same for the mean window. **No future leak in the replay
  path.** Live: as documented above, returns a stale snapshot —
  still not a future leak, but a *correctness* issue.

All three overlays pass the no-future-leak audit. The Issue #38 (and
all earlier) walk-forward results remain methodologically valid.

## Recommended fixes (NOT applied in this commit)

### High priority: Issue #38 live data-source bug

The `LiveVolumeConfirmationOverlay` (and to a lesser extent
`LiveVolSizingOverlay`) should consume the live worker's
already-fetched indicator frame instead of maintaining its own
parallel Binance Vision archive.

Suggested redesign:

```python
class LiveVolumeConfirmationOverlay:
    def __init__(self, assets, window_bars=20):
        # No history preload. Per-asset window only.
        self.window_bars = window_bars

    def state_for_signal(self, asset: str, ind_df: pd.DataFrame) -> dict:
        # ind_df is the live worker's indicator frame for `asset`,
        # built from the same ccxt feed driving signal_row.
        # signal bar is iloc[-2] per Issue #24 semantics.
        if len(ind_df) < self.window_bars + 2:
            return {"available": False, "decision": "warmup", ...}
        signal_volume = float(ind_df["volume"].iloc[-2])
        # trailing 20 bars BEFORE the signal bar (strictly causal)
        window = ind_df["volume"].iloc[-(self.window_bars + 2):-2]
        mean20 = float(window.mean())
        ...  # evaluate_volume_confirmation_gate as before
```

Pros:
- Same venue / feed for every comparison — no Binance-vs-Kraken
  asymmetry.
- Always real-time — no monthly staleness.
- Strictly causal — uses bars before the signal bar.
- Symmetric for live + replay — replay's `ind_df` is already passed
  per-asset in `_run_config_replay`.
- No new dependency.

Cons:
- Slightly more coupling between `multi_loop.run` and the overlay
  (must pass `ind_df` per asset per bar instead of just `ts`).
- Replay's existing `state_at(asset, ts)` API would diverge from the
  funding overlay's. Could solve by giving every overlay a uniform
  `state_for_signal(asset, ind_df)` and migrating funding in a
  follow-up.

### Medium priority: vol_sizing staleness

`LiveVolSizingOverlay` has the same architectural issue but with
milder consequences. The same redesign (consume worker's `ind_df`,
compute realised vol from it directly) would close the asymmetry. The
quartile threshold refit would draw from the trailing
`train_months` of live data instead of Binance Vision archives.

The numerical impact in the recent operator output is small (output
shows `vol: warmup or insufficient history; fail-open mult=1.00`,
which means vol_sizing isn't actively blocking anyway — likely
because Kraken's 4h indicator window the worker fetched doesn't yet
have 24 bars of complete data). But the underlying asymmetry should
be fixed for symmetry with the volume fix.

### Low priority: funding staleness

Funding is the most resilient of the three because rates evolve
slowly. **Recommend leaving as-is**. The operator can verify their
funding gate by checking that `pct` in the verbose line is moving
slowly across days (it should be, since 8h funding cadence on
Binance is much slower than the 4h bar cadence). If a quick
live-rate confirmation becomes necessary later, ccxt's `fetch_funding_rate()`
endpoint can provide it.

## Commit ordering (per Issue #39 hard rules)

This commit (the audit report) is research only — no code changes.

A separate commit shipping in parallel cleans up the funding-archive
current-month 404 log noise (yellow → gray + clearer text). That's
also code-only and unrelated to the audit's recommendations.

**No live wiring of the recommended fixes lands in this commit, and
no automatic switch of the operator's live `--config` is performed.**
The fixes above require explicit operator approval and a new live
adoption issue once the redesign converges.

## Tests added (separate commit)

The funding-cleanup commit adds Section 19 to
`scripts/test_multiasset_worker.py` verifying:

- funding loader detects the current calendar month
- the current-month 404 path emits a gray "not yet published
  (expected for current month)" line
- non-current-month and non-404 failures still emit the yellow
  warning

No tests for the audit's recommendations are added here — those will
land with the fix.

## Hard-rule compliance

- ✅ Research first (this report is research-only).
- ✅ No future leak (audited; all three overlays causal).
- ✅ Walk-forward safe (no change to walk-forward harnesses).
- ✅ No adoption without approval (no live wiring, no config switch).
- ✅ Findings committed separately from any code fix.

## Files

- This audit: `research/data_freshness_audit.md`.
- Reference: the operator's terminal transcript (the conversation
  containing the live `volume: signal=811.59 mean20=2292.76 ratio=0.35
  decision=block_low_volume_flip` lines).
- Reproduction: `/tmp/audit39/volconf_3mo.csv` and
  `/tmp/audit39/volconf_3mo.log` on the audit machine.
