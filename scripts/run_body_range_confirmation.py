#!/usr/bin/env python3
"""Body-to-range confirmation for SuperTrend entries (Issue #37).

Adds ONE entry filter on top of the adopted live candidate
(`state/live_multiasset_long_short_funding_vol.yaml` — BTC/ETH
long-short SuperTrend(10,3) + funding + vol_sizing Q1/Q2-Q3/Q4
ladder):

  - On the signal bar, compute
    ``body_to_range = abs(close - open) / max(high - low, 1e-12)``.
  - Require ``body_to_range >= 0.50``.
  - For LONG entries: additionally require ``close > open`` (bullish body).
  - For SHORT entries: additionally require ``close < open`` (bearish body).

Hard rules (Issue #37 spec):

  - No live worker changes; no yaml writes; no parameter sweep.
  - signals.py UNMODIFIED — filter applied at the runner level.
  - No funding threshold changes (long block p>=95, short block p<=5).
  - No vol_sizing rule changes (24-bar rv, train_months=12,
    Q1=1.00 / Q2_Q3=0.50 / Q4=0.25).
  - Walk-forward only; 48mo span; train=1440 / test=360 / embargo=6;
    fee=0.001/side; slippage=0.0005.

Variants:
  - ``baseline_funding_vol``           : adopted live candidate, no filter.
  - ``baseline_funding_vol_plus_body_range`` : same + body-to-range filter.

Deliverables:
  - ``results/body_range_confirmation_comparison_<ts>.csv``
  - ``results/body_range_confirmation_comparison_<ts>.md``
  - ``research/body_range_confirmation_report.md``

Adoption question (Issue #37):
  Does candle-quality confirmation reduce weak flip entries without
  over-filtering the strategy?
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hermes_trading import STATE_DIR, log

import importlib.util
_LAB_PATH = Path(__file__).resolve().parent / "_supertrend_overlay_lab.py"
_spec = importlib.util.spec_from_file_location("_supertrend_overlay_lab", _LAB_PATH)
lab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lab)  # type: ignore[union-attr]


# ---- Issue #37 body-to-range filter (locked, no tuning) -------------------

BODY_RATIO_MIN = 0.50          # locked per spec
EPS = 1e-12


def body_to_range(row: pd.Series) -> float:
    """``abs(close - open) / max(high - low, eps)`` for a single row."""
    o = float(row["open"])
    h = float(row["high"])
    l = float(row["low"])
    c = float(row["close"])
    return abs(c - o) / max(h - l, EPS)


def build_body_range_filter():
    """Return an ``entry_filter_fn`` closure that gates on
    ``body_to_range >= 0.50`` AND direction-consistent body sign:
    long requires ``close > open``, short requires ``close < open``."""

    def fn(signal_row: pd.Series, direction: str, asset: str) -> bool:
        try:
            o = float(signal_row["open"])
            c = float(signal_row["close"])
            h = float(signal_row["high"])
            l = float(signal_row["low"])
        except Exception:
            return True  # missing OHLC -> fail open
        if pd.isna(o) or pd.isna(c) or pd.isna(h) or pd.isna(l):
            return True
        rng = max(h - l, EPS)
        ratio = abs(c - o) / rng
        if ratio < BODY_RATIO_MIN:
            return False
        if direction == "long" and not (c > o):
            return False
        if direction == "short" and not (c < o):
            return False
        return True

    return fn


EXPERIMENT_BLURB = (
    "Issue #37 — candle body-to-range confirmation at the SuperTrend flip "
    "bar. Rule: ``body_to_range = abs(close-open) / max(high-low, eps); "
    "ratio >= 0.50`` AND direction-consistent body sign (long: close > open; "
    "short: close < open). Locked, no tuning. Filter applies at the "
    "runner level — `signals.py` and the live worker remain untouched."
)

ADOPTION_QUESTION = (
    "Does candle-quality confirmation reduce weak flip entries without "
    "over-filtering the strategy?"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-months", type=int, default=48)
    ap.add_argument("--timeframe", default="4h")
    ap.add_argument("--long-short-strategy",
                    default=str(STATE_DIR / "strategy_supertrend_long_short.yaml"))
    ap.add_argument("--train-bars", type=int, default=lab.TRAIN_BARS_DEFAULT)
    ap.add_argument("--test-bars", type=int, default=lab.TEST_BARS_DEFAULT)
    ap.add_argument("--embargo-bars", type=int, default=lab.EMBARGO_BARS_DEFAULT)
    ap.add_argument("--fee", type=float, default=lab.FEE_DEFAULT)
    ap.add_argument("--slippage", type=float, default=lab.SLIPPAGE_DEFAULT)
    ap.add_argument("--out-dir", default="results")
    ap.add_argument("--research-dir", default="research")
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    research_dir = Path(args.research_dir); research_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"body_range_confirmation_comparison_{ts}.csv"
    md_path = out_dir / f"body_range_confirmation_comparison_{ts}.md"
    trades_path = out_dir / f"trades_body_range_confirmation_{ts}.csv"
    report_path = research_dir / "body_range_confirmation_report.md"

    strategy = yaml.safe_load(open(args.long_short_strategy))

    btc_df, eth_df, common = lab.load_universe(args.n_months, args.timeframe)
    asset_df_map = {"BTCUSDT": btc_df, "ETHUSDT": eth_df}

    long_funding, short_funding = lab.build_funding_gates(common, args.n_months)
    vol_overlay = lab.build_vol_overlays(asset_df_map)

    body_filter = build_body_range_filter()

    variant_names = ("baseline_funding_vol",
                     "baseline_funding_vol_plus_body_range")
    results = []
    results.append(lab.run_walk_forward(
        name=variant_names[0],
        btc_df=btc_df, eth_df=eth_df, strategy=strategy,
        long_funding=long_funding, short_funding=short_funding,
        vol_overlay=vol_overlay,
        entry_filter_fn=None,
        train_bars=args.train_bars, test_bars=args.test_bars,
        embargo_bars=args.embargo_bars,
        fee=args.fee, slippage=args.slippage,
    ))
    results.append(lab.run_walk_forward(
        name=variant_names[1],
        btc_df=btc_df, eth_df=eth_df, strategy=strategy,
        long_funding=long_funding, short_funding=short_funding,
        vol_overlay=vol_overlay,
        entry_filter_fn=body_filter,
        train_bars=args.train_bars, test_bars=args.test_bars,
        embargo_bars=args.embargo_bars,
        fee=args.fee, slippage=args.slippage,
    ))

    span_hi = pd.Timestamp(common[-1])
    recent_metrics = lab.compute_window_metrics(
        results, span_hi, full_window_months=args.n_months,
    )
    lab.render_csv(csv_path, results, recent_metrics,
                   full_window_months=args.n_months)
    log(f"wrote CSV -> {csv_path}")

    base = recent_metrics[variant_names[0]][args.n_months]
    filt = recent_metrics[variant_names[1]][args.n_months]
    pf_gate = 1.69
    n_gate = 30
    pf_filt = filt["profit_factor"]
    n_filt = filt["trades"]
    pf_pass = (pf_filt > pf_gate) and (n_filt >= n_gate)
    ret_kept = (filt["total_return"] >= 0.5 * base["total_return"]
                if base["total_return"] > 0 else filt["total_return"] >= 0)
    trade_kept = n_filt >= int(0.85 * base["trades"]) if base["trades"] else False
    if pf_pass and ret_kept and trade_kept:
        verdict = ("**YES** — body-to-range confirmation clears the PF and "
                   "trade-count gates and preserves return.")
    elif pf_pass and not trade_kept:
        verdict = ("**NO** — PF holds but trade count drops materially; "
                   "the filter over-prunes the strategy.")
    elif pf_pass and not ret_kept:
        verdict = ("**NO** — PF improves but absolute return falls more "
                   "than half; body-quality filter trims edge along "
                   "with weak flips.")
    else:
        verdict = "**NO** — filter fails the PF or trade-count adoption gate."

    lab.render_md_comparison(
        out_path=md_path,
        title=f"Body-to-range confirmation comparison (Issue #37) — {ts}",
        experiment_blurb=EXPERIMENT_BLURB,
        variant_names=variant_names,
        variant_results=results,
        recent_metrics=recent_metrics,
        span_lo=pd.Timestamp(common[0]),
        span_hi=span_hi,
        n_bars=len(common),
        n_months=args.n_months,
        train_bars=args.train_bars,
        test_bars=args.test_bars,
        embargo_bars=args.embargo_bars,
        fee=args.fee,
        slippage=args.slippage,
        adoption_verdict=verdict,
        adoption_question=ADOPTION_QUESTION,
    )
    log(f"wrote MD -> {md_path}")

    extra_blurb = (
        "## Methodology\n\n"
        "The filter is a pure function of the signal bar's own OHLC. "
        "It is therefore trivially causal — no rolling window, no "
        "future leakage, no train-window dependence.\n\n"
        "Two clauses are tested in conjunction: (1) body magnitude "
        "(``ratio >= 0.50``) and (2) body sign consistent with the "
        "entry direction. A long entry on a bar that closes below its "
        "open is rejected even if the ratio is large; a short entry "
        "on a bar that closes above its open is rejected the same way. "
        "Doji bars (range = 0) are mapped through ``max(range, 1e-12)`` "
        "to avoid division by zero — a true doji with body == 0 falls "
        "below the 0.50 threshold and is rejected, which is the spec's "
        "intended behaviour.\n\n"
        "## Filter rule\n\n"
        "```\n"
        "body_to_range = abs(close - open) / max(high - low, 1e-12)\n"
        "allow_entry = (body_to_range >= 0.50)\n"
        "              AND (direction == 'long' implies close > open)\n"
        "              AND (direction == 'short' implies close < open)\n"
        "```\n\n"
        "## Counter-factual diagnostics\n\n"
        f"- longs filtered out: {results[1]['longs_filtered_out']}\n"
        f"- shorts filtered out: {results[1]['shorts_filtered_out']}\n"
        f"- detailed trades CSV: `{trades_path}`\n"
    )
    research_md = (
        f"# Body-to-range confirmation — research report (Issue #37)\n\n"
        f"_Generated {ts}._\n\n"
        f"{EXPERIMENT_BLURB}\n\n"
        f"{extra_blurb}\n"
    )
    with open(md_path, "r") as fh:
        md_content = fh.read()
    md_body = md_content.split("\n", 1)[1] if "\n" in md_content else md_content
    report_path.write_text(research_md + "\n" + md_body)
    log(f"wrote research report -> {report_path}")

    lab.write_trades_csv(trades_path, results)
    log(f"wrote detailed trades CSV -> {trades_path}")
    log("done. No live config modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
