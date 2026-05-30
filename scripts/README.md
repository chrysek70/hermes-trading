# scripts/

Reproducible runners for the research experiments. Each script is
self-contained: load data, walk-forward all variants, save artifacts.

The live worker (`hermes_trading.run`) is **not** in here. Scripts in
this directory are research-only — they read market history, run
walk-forward, and write CSV / Markdown reports. They never place
trades, never write to `state/strategy.yaml`, never touch the live
worker's state files.

## Contents

| script | what it does | issues |
|---|---|---|
| `run_markov_research.py` | Six-mode Markov regime sweep on `strategy_v2_long_short.yaml`. Original disciplined Markov rebuild. | #2 |
| `run_supertrend_extended.py` | SuperTrend(10, 3) walk-forward on N months. Three variants: baseline, supertrend_only, supertrend + Markov routing. | #4, #11 |
| `run_btc_eth_rs.py` | BTC/ETH relative-strength experiment. Four variants: baseline_v2, supertrend_only, supertrend + RS filter, supertrend + RS sizing. ETH used as market context only (not traded). | #5 |
| `run_multiasset_supertrend_rs.py` | Multi-asset SuperTrend + RS with BTC and ETH as a one-position-at-a-time portfolio. Five variants including ETH-as-traded-asset solo and the multi-asset combination. | #12 |
| `run_eth_vs_btc_analysis.py` | Diagnostic comparison of ETH vs BTC SuperTrend results. Trade-level, trend-quality, market-structure metrics; two rotation selectors. Research-only, does not change the strategy. | #13 |
| `run_hmm_regime.py` | Optional 2-state Gaussian HMM regime overlay on SuperTrend(10, 3), BTC and ETH separately. Per-fold fit + train-only state mapping. Requires `hmmlearn`; falls back cleanly with an install hint if missing. | #6 |
| `run_top5_parallel.py` | Fixed-universe parallel portfolio of 5 SuperTrend(10, 3) instances (BTC, ETH, SOL, BNB, XRP). Equal risk per asset, up to 5 concurrent positions, no rotation. Optional per-asset HMM overlay; tests 4 variants. | #14 |
| `run_funding_filter.py` | Funding-rate filter / sizing overlay. Loads BTC + ETH perpetuals funding from Binance Vision, forward-fills to 4h decision bars (causal), and blocks new longs when rolling 30-day funding percentile exceeds the threshold. 5 variants. | #7 |
| `replay_live.py` | Replay mode — feed historical bars through the live engine at any speed. Educational / intuition-building tool. Does not trade, does not write to `state/`. | — |
| `test_markov_regime.py` | Unit-style validators for the Markov module. Not an experiment. | — |

## How to run

All scripts use `uv` and inherit defaults from
`state/strategy_*.yaml`. Run from the repo root:

```bash
cd ~/hermes-trading && export PATH="$HOME/.local/bin:$PATH"

# Reproduce the locked SuperTrend extended-history result (Issue #11)
uv run python scripts/run_supertrend_extended.py

# Same script, different window — for sanity checks, not for adoption
uv run python scripts/run_supertrend_extended.py --n-months 24
uv run python scripts/run_supertrend_extended.py --n-months 36

# Reproduce the BTC/ETH RS experiment (Issue #5)
uv run python scripts/run_btc_eth_rs.py

# Reproduce the multi-asset SuperTrend + RS portfolio (Issue #12)
uv run python scripts/run_multiasset_supertrend_rs.py

# The Markov sweep (Issue #2)
uv run python scripts/run_markov_research.py --n-months 48

# Replay mode — watch what the bot WOULD have done on history
# (educational; not an experiment)
uv run python scripts/replay_live.py \
    --strategy state/strategy_supertrend.yaml \
    --n-months 24 --bars-per-second 20 --quiet-flat
```

Every script supports `--help`. The flags all have sensible defaults
that match the locked criteria; you only need to override them for
ad-hoc sanity checks.

## Outputs

Every runner writes the same three artifact shapes to `--out-dir`
(default `results/`):

- `<experiment>_<n>mo_comparison_<ts>.csv` — one row per variant with
  every metric the report uses.
- `<experiment>_<n>mo_comparison_<ts>.md` — human-readable table for
  pasting into issue comments and PRs.
- `trades_<experiment>_<n>mo_detailed_<ts>.csv` — per-trade rows for
  diagnostic drilldowns (entry indicators, exit reason, regime tag,
  size multiplier, etc). Gitignored by default — see
  `.gitignore`'s `results/trades_*.csv` rule.

The narrative reports live separately in `research/`:

- `research/supertrend_report.md` — Issue #4 (24mo).
- `research/supertrend_48mo_report.md` — Issue #11.
- `research/btc_eth_relative_strength_report.md` — Issue #5.
- `research/donchian_markov_report.md` — Donchian rejection.

## Conventions (don't break these)

These rules are why we trust the numbers. Every runner here follows
them. Updates that break them must be a new experiment with a new
issue number, not an in-place change:

1. **Walk-forward OOS only.** In-sample is never reported as success.
   Folds are non-overlapping with a 6-bar embargo. `wf.walk_forward`
   enforces this.
2. **No parameter sweeps inside a runner.** Each script tests one
   hypothesis with locked parameters. To test different parameters,
   write a new runner with a new issue number — do not modify
   defaults to chase a number.
3. **Same fees/slippage everywhere.** Fee 10 bps/side, slippage 5 bps.
   These are the script defaults; do not override them in committed
   results.
4. **Same fold geometry.** train_bars=1440, test_bars=360,
   embargo_bars=6. Identical across SuperTrend, RS, Donchian, Markov
   sweep. Sanity-check runs may vary this, but the headline / adopted
   result must use these values.
5. **No live wiring from a script.** Scripts here only read data and
   write to `results/`. Live worker behaviour is controlled by
   `state/strategy.yaml` and `state/markov_regime.yaml` — adoption
   means updating those files **manually** after the criteria are met,
   never automatically from a script.
6. **Causal feature computation.** Any new feature added to a runner
   must use only bars `<= t` when computing the value for bar `t`. RS
   features in `hermes_trading/relative_strength.py` are the worked
   example.

## Adoption criteria (locked)

A variant is "adopted as research candidate" if its OOS walk-forward
clears both:

- OOS profit factor > **1.69** (current live baseline floor — v2
  long-short on 24mo). Some experiments lift this floor when an
  earlier variant gets adopted: Issue #5 used `> 2.24` because
  SuperTrend on 48mo had just become the new floor.
- OOS trade count ≥ **30**.

These thresholds are why some strong-looking experiments are not
adopted (e.g. SuperTrend on 24mo: PF 9.02 but only 9 trades; RS
filter on 48mo: PF 3.33 but only 20 trades). The trade-count gate is
non-negotiable across the project — it exists precisely to refuse
adoption of a high-PF subset on too-small a sample.

To change the criteria: edit `ROADMAP.md`, document the rationale in
a new issue, and only then update the runner thresholds (if any are
hard-coded — currently the runners just write the numbers; the
decision is made in the report).

## Updating an existing runner

Some kinds of changes are safe in-place; others require a new
experiment / new runner / new issue. The line:

**Safe to update in-place:**

- Bug fixes in the runner itself (wrong column name, miscalculated
  metric, etc).
- Adding a new diagnostic column to the output CSV/MD (does not
  change any decision).
- Plumbing changes (refactor for clarity, factor out a helper).
- Changing logging output / verbosity.
- Adding `--help` text or argparse improvements.

**Requires a new runner / new issue:**

- Changing any parameter that affects the result (window length,
  threshold, fold size, fee, slippage, RS lookback, SuperTrend
  period/multiplier, etc).
- Adding a new variant that changes the headline comparison.
- Switching the symbol or timeframe of an adopted variant.
- Changing the adoption thresholds.

The reason: scripts in this directory are the historical record of
"this is what we ran and these are the numbers we trusted". If you
edit the parameters of a runner that has produced an
already-committed result, future you cannot reproduce that result.
Write a new runner and reference the old one.

## Adding a new experiment

Template — copy `run_supertrend_extended.py` as the starting point. Steps:

1. Open a new issue in GitHub describing the hypothesis, the
   variants, and the adoption criteria. Pin the parameters before
   running anything. Reference `ROADMAP.md`.
2. Decide if the experiment needs new data, new features, or new
   decision plumbing:
   - New asset data → `hermes_trading.data.load_klines("…")`. Test
     it loads on its own first (Binance Vision has monthly archives
     for almost every USDT pair).
   - New features (e.g. funding rate, on-chain) → write a clean
     module in `hermes_trading/`, follow the `relative_strength.py`
     pattern (causal features, build_decisions returns a DataFrame
     compatible with `bt._attach_decisions_df`).
   - New strategy setup (e.g. a new entry pattern) → add to
     `hermes_trading/signals.py` and gate it via a yaml flag like
     `setups.<your_setup>.enabled`.
3. Create the strategy yaml in `state/`. Copy an existing one and
   only change what your experiment tests.
4. Write `scripts/run_<experiment>.py`. The structure should mirror
   `run_supertrend_extended.py`:
   - argparse with sane defaults (history, fold geometry, fees,
     out-dir).
   - Load data, log span.
   - Define `variants = [(name, strategy, decisions_full_or_None), …]`.
   - Loop over variants, run walk-forward, collect rows.
   - Write CSV, MD, and (optionally) a detailed-trades CSV.
5. Smoke-test it with `--help`, then run with reduced
   `--n-months` for a quick sanity check, then run the real
   adoption-criteria configuration.
6. Write the narrative report in `research/<experiment>_report.md`.
   The report must answer the questions the issue spec set, plus
   the adoption decision per the locked criteria.
7. Update `RESEARCH_LOG.md` (append the experiment block),
   `ROADMAP.md` (move it from queued → completed), and `README.md`
   (the current-best table) — only if adopted.
8. Commit everything with a neutral technical message. Close the
   issue in the commit (`Closes #N`) or with `gh issue close N`.
   Comment on the issue with the results table and decision.

## Maintaining this README

When you add a new script:

- Add it to the contents table at the top.
- If it uses an unusual pattern (new data source, new decision
  plumbing, a non-standard fold), add a one-paragraph note under
  "Conventions" pointing readers at the exception.
- If it changes the adoption criteria, update the "Adoption
  criteria" section and reference the new floor.

When you change anything about how runners are invoked:

- Update the "How to run" code block to match.
- Keep the example commands minimal — one canonical invocation per
  script. Use `--help` for the exhaustive list.

The goal is that someone (including you, in three months) can come
back, read this file, and reproduce or extend any committed result
without reading the runner source.
