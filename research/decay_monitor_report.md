# Live Strategy Decay Monitor — Report

Issue #15. Infrastructure for detecting when the live / paper strategy
is degrading versus research-time expectations.

This is **monitor / report only**. It never modifies trading decisions,
never resizes positions, never auto-disables strategies, and never
touches the exchange.

## 1. What file does it read?

Default: `state/trades.jsonl` — the JSON-lines file the live worker
appends to every time a position closes
(`hermes_trading/loop.py:append_jsonl(STATE_DIR / "trades.jsonl", trade)`).

Override with `--trades <path>`.

The reader is **defensive about format drift**. The live worker's
trade-dict shape has changed across project versions (`v1` RSI, `v2`
long-short, `v3` SuperTrend) and may change again. The monitor
tolerates:

- blank lines (skipped silently)
- lines that don't parse as JSON (line collected as parse warning)
- JSON objects missing the `return` field (collected as parse warning)
- `return` values that aren't numeric (collected as parse warning)
- extra fields it doesn't recognise (ignored)
- mixed schemas across lines (each line evaluated independently)

Parse warnings are surfaced in the report header and the JSON output
but never crash the run.

## 2. What fields are required?

Hard requirement: **`return`** (numeric net return as a fraction —
e.g. `0.012` for +1.2%). The live worker writes this on every closed
trade.

Optional, used when present:

| field | use |
|---|---|
| `status` | skip lines where `status != "closed"` |
| `opened_at` / `closed_at` | average holding time, latest-trade timestamp |
| `asset` | not consumed; useful in the JSON output |
| `exit_reason` | not consumed; useful in the JSON output |
| `entry_price` / `exit_price` | not consumed; documentation only |
| `direction` / `setup` / `strategy_version` | not consumed |

If `opened_at` / `closed_at` are absent or unparseable, the monitor
reports `avg_holding: n/a` instead of failing.

## 3. What metrics are calculated?

Per configured window size (default `10,25,50` — last N closed trades):

- **trade count** (`n`)
- **total return** (compounded equity curve over the window — 1)
- **average return** (mean of `return`)
- **median return**
- **win rate** (fraction of trades with `return > 0`)
- **profit factor** (sum of wins / |sum of losses|; `inf` if no losses,
  `nan` if no trades)
- **max drawdown** (peak-to-trough on the within-window equity curve)
- **average holding time** (seconds; formatted into the most readable
  unit: `s` / `m` / `h` / `d`)
- **worst trade** / **best trade** (single-trade extremes within window)
- **consecutive losses** (trailing — counted from the last trade
  backward, stopping at the first non-loss)
- **latest trade time** (ISO 8601)

The window slicing is `trades[-N:]` after the parse pass, so it
respects the order in which the live worker wrote them.

## 4. What triggers DEGRADED?

A window is flagged if **any** of the following hold (per the spec
and configurable on the CLI):

| condition | flag | default |
|---|---|---|
| `profit_factor < pf_warn_below` | PF | `< 1.20` |
| `win_rate < baseline_win_rate * win_rate_warn_factor` | win rate | `< 65% of baseline` |
| `max_drawdown > baseline_dd * dd_warn_factor` | DD | `> 125% of baseline` |
| `consecutive_losses >= consecutive_loss_warn` | losses | `>= 4` |
| `total_return < 0` | return | always |

Overall status:

- `OK` — at least one window had ≥ `min_trades` and **no** warnings
  fired
- `DEGRADED` — at least one reportable window had ≥ 1 warning
- `INSUFFICIENT_DATA` — no window has ≥ `min_trades`

Exit codes: `0` OK, `1` DEGRADED, `2` INSUFFICIENT_DATA / unreadable.

The baselines (PF 2.50, DD 5.54%, win-rate 48.7%) default to Issue #14's
adopted `btc_eth_reference_parallel`. Override with `--baseline-pf`,
`--baseline-dd`, `--baseline-win-rate` to match whatever strategy is
actually live.

## 5. What does it not do?

Explicit non-goals, locked at design time:

- **No trading-decision changes.** It does not pause, throttle, or
  resize positions. It writes only to stdout and an optional JSON file.
- **No automatic strategy switching.** If status is `DEGRADED`, the
  output says "review strategy" — a human reads it.
- **No exchange / API writes.** It is read-only on `state/trades.jsonl`.
- **No background process.** Run-on-demand. Not wired to cron at the
  user's request.
- **No alerting integrations** (Slack, Datadog, email). Pure CLI.
  Wiring is intentionally separate so the monitor stays simple — see
  Q7 below.
- **No secrets.** Reads a local file. Outputs go to stdout and the
  optional `--output` path.

If any of these change in the future, they need a new issue and
explicit user authorisation.

## 6. How should this be used with live paper trading?

The honest workflow given the user's current setup:

1. The live worker (`uv run python -m hermes_trading.run`) writes to
   `state/trades.jsonl` whenever it closes a paper position.
2. Run the monitor periodically (manually for now — daily or weekly,
   whatever feels right):
   ```bash
   uv run python scripts/monitor_strategy_decay.py
   ```
3. If status is `OK`, no action required.
4. If status is `INSUFFICIENT_DATA`, the strategy hasn't traded
   enough yet — keep collecting.
5. If status is `DEGRADED`, **read the warning details** and decide
   whether to:
   - reduce paper notional (manual yaml edit)
   - re-run a walk-forward to see if a regime shift is visible
   - investigate which window flagged (10 = very recent; 50 = longer
     term)

When new research candidates get adopted (Issue #14 BTC/ETH parallel,
Issue #7 funding filter) and eventually go live, the baselines passed
to the monitor should be updated to match that strategy's research-time
metrics. The defaults today match the **strongest adopted candidate**
(`btc_eth_reference_parallel`, PF 2.50 / DD 5.54% / win 48.7%).

If multiple strategies eventually run live in parallel, run the
monitor with `--trades` pointed at each strategy's trade log
separately. Aggregating across strategies hides per-strategy decay.

## 7. How could this later feed Slack or Datadog alerts?

Two clean integration points, neither implemented yet:

### A. Exit-code-driven wrapper

The monitor exits `1` on DEGRADED. A thin shell wrapper can fan that
out to anywhere:

```bash
uv run python scripts/monitor_strategy_decay.py --json \
  --output /tmp/decay_latest.json || \
  curl -X POST -H "Content-Type: application/json" \
       -d "$(cat /tmp/decay_latest.json)" \
       "$SLACK_WEBHOOK_URL"
```

This keeps the monitor itself stateless and dependency-free. The
webhook URL stays out of the repo (env var) and the monitor doesn't
need an HTTP library.

### B. JSON output to log aggregator

`--json` already emits a structured report with all metrics and
warnings per window. A scheduled job that runs the monitor and writes
its `--output` JSON to a log directory is enough for Datadog log-based
metrics or a Splunk-style ingest. No extra code needed in the monitor.

### C. (Future, larger) GitHub Issue auto-comment

Since the repo already uses `gh` heavily, a periodic CI job could
post the decay JSON as a comment on a pinned tracking issue if status
changes from OK → DEGRADED. This is out of scope for Issue #15 but is
a low-effort addition once the user wants persistent alerting.

The deliberate design is: monitor produces machine-readable output
and exit codes; alerting lives outside the monitor. That way the
monitor never needs credentials and never blocks on network calls.

## Tests

A self-test runs without pytest (pytest is not currently in the
project dependencies). Invocation:

```bash
uv run python scripts/monitor_strategy_decay.py --self-test
```

Covers:

- Fixture parses correctly (25 valid trades + 3 expected parse warnings).
- Healthy-slice metrics (`trades[:15]`) clear all warning thresholds.
- Decayed-slice metrics (`trades[-10:]`) trip PF, return and
  consecutive-loss warnings.
- Full-fixture report status is `DEGRADED`.
- Empty input gives `INSUFFICIENT_DATA`.
- All-winners edge case gives `PF = inf` and `DD = 0` (no crash).
- Terminal rendering produces expected markers.

The fixture (`tests/fixtures/trades_decay_sample.jsonl`) is laid out so
the first 15 trades are healthy and the last 10 are decayed —
deliberately constructed to exercise both reportable states with
trailing consecutive losses, parse warnings on three different
failure modes (bad JSON / missing `return` / non-numeric `return`),
and clean metric arithmetic.

If pytest is later added to the project, the test logic can be moved
into `tests/test_monitor_strategy_decay.py` with one-line shims that
call the same `build_report` / `compute_metrics` / `evaluate_warnings`
functions. The monitor's API is designed for that path.

## Artifacts

- `scripts/monitor_strategy_decay.py` (the monitor itself)
- `tests/fixtures/trades_decay_sample.jsonl` (fixture)
- `research/decay_monitor_report.md` (this file)

No changes to the live worker, the engine, or any committed result.

## Closing-the-loop summary

- **Issue #15 status:** ready to close.
- **What changed:** added the monitor, the fixture, the self-test,
  and this report. ROADMAP / RESEARCH_LOG / scripts/README updated to
  reflect that the live-decay-monitor item is no longer queued.
- **Live worker:** unchanged.
- **Not done (intentional):** no cron wiring, no Slack / Datadog
  integration, no automatic strategy switching. Those are separate
  decisions and separate issues.
- **What the monitor will look like in 30 days:** assuming the live
  worker continues to log closed trades at the current rate, the
  monitor will become more informative as window-25 and window-50
  start to have meaningful sample sizes. Today's `state/trades.jsonl`
  has 10 closed trades — enough for window-10, not enough for the
  longer windows.
