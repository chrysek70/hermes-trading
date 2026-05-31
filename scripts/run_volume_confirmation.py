#!/usr/bin/env python3
"""Volume confirmation filter for SuperTrend entries (Issue #35).

Adds ONE entry filter on top of the adopted live candidate
(`state/live_multiasset_long_short_funding_vol.yaml` — BTC/ETH
long-short SuperTrend(10,3) + funding + vol_sizing Q1/Q2-Q3/Q4
ladder):

  - Compute ``vol_mean_20 = volume.rolling(20).mean()`` per asset
    at each 4h bar (causal).
  - On a SuperTrend flip entry bar (the signal bar that fires
    ``signals.long_entry`` / ``signals.short_entry``), require
    ``volume_at_signal_bar >= vol_mean_20_at_signal_bar``.
  - If the requirement fails, skip the entry. Direction does not
    change the rule, only whether long/short would have fired.

Hard rules (Issue #35 spec):

  - No live worker changes; no yaml writes; no parameter sweep.
  - signals.py UNMODIFIED — filter applied at the runner level.
  - No funding threshold changes (long block p>=95, short block p<=5).
  - No vol_sizing rule changes (24-bar rv, train_months=12,
    Q1=1.00 / Q2_Q3=0.50 / Q4=0.25).
  - Walk-forward only; 48mo span; train=1440 / test=360 / embargo=6;
    fee=0.001/side; slippage=0.0005.

Variants:
  - ``baseline_funding_vol``  : adopted live candidate, no extra filter.
  - ``baseline_funding_vol_plus_volume_conf`` : same + volume filter.

Deliverables:
  - ``results/volume_confirmation_comparison_<ts>.csv``
  - ``results/volume_confirmation_comparison_<ts>.md``
  - ``research/volume_confirmation_report.md``

Adoption question (Issue #35):
  Does volume confirmation reduce chop losses without killing trade
  count or long-term return?
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
from hermes_trading import signals

# import sibling support module (filename starts with underscore)
import importlib.util
_LAB_PATH = Path(__file__).resolve().parent / "_supertrend_overlay_lab.py"
_spec = importlib.util.spec_from_file_location("_supertrend_overlay_lab", _LAB_PATH)
lab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lab)  # type: ignore[union-attr]


# ---- Issue #35 volume filter (locked, no tuning) ----

VOL_MEAN_WINDOW = 20  # locked per spec


def precompute_vol_mean_20(asset_df: pd.DataFrame) -> pd.Series:
    """``volume.rolling(20).mean()`` per spec. Causal — at bar T this
    is the mean of the prior 20 bars (inclusive of T's volume, as the
    spec says ``volume.rolling(20).mean()`` evaluated AT the signal
    bar). Since the signal bar is itself a closed bar in the
    walk-forward model (Issue #24 semantics: the worker decides on
    ``iloc[-2]``, i.e. a closed bar), comparing the signal bar's
    volume to its trailing rolling mean is fully causal.
    """
    return asset_df["volume"].rolling(VOL_MEAN_WINDOW,
                                      min_periods=VOL_MEAN_WINDOW).mean()


def build_volume_filter(asset_df_map: dict[str, pd.DataFrame]):
    """Return an ``entry_filter_fn(signal_row, direction, asset)``
    closure that gates on volume >= rolling-20 mean. ``signal_row``
    arrives as a pandas Series — the volume column is already on it.
    Falls open during the 20-bar warmup.
    """
    # Precompute the per-asset rolling means; lookup is by timestamp.
    vol_mean_by_asset = {
        a: precompute_vol_mean_20(df) for a, df in asset_df_map.items()
    }

    def fn(signal_row: pd.Series, direction: str, asset: str) -> bool:
        # row.name is the timestamp index in pandas Series
        try:
            ts = signal_row.name
        except Exception:
            return True
        vm = vol_mean_by_asset.get(asset)
        if vm is None or ts not in vm.index:
            return True
        mean20 = vm.loc[ts]
        if pd.isna(mean20):
            return True  # warmup -> fail open (do not block)
        vol_now = signal_row.get("volume")
        if pd.isna(vol_now):
            return True
        return bool(float(vol_now) >= float(mean20))

    return fn


# ---- main ----

EXPERIMENT_BLURB = (
    "Issue #35 — volume confirmation at the SuperTrend flip bar. "
    "Rule: ``volume_at_signal_bar >= volume.rolling(20).mean()`` (locked, "
    "no tuning). Same rule applied to long and short entries. The filter "
    "applies at the runner level — `signals.long_entry` / `signals.short_entry` "
    "and the live worker remain untouched."
)

ADOPTION_QUESTION = (
    "Does volume confirmation reduce chop losses without killing trade "
    "count or long-term return?"
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
    csv_path = out_dir / f"volume_confirmation_comparison_{ts}.csv"
    md_path = out_dir / f"volume_confirmation_comparison_{ts}.md"
    trades_path = out_dir / f"trades_volume_confirmation_{ts}.csv"
    report_path = research_dir / "volume_confirmation_report.md"

    strategy = yaml.safe_load(open(args.long_short_strategy))

    btc_df, eth_df, common = lab.load_universe(args.n_months, args.timeframe)
    asset_df_map = {"BTCUSDT": btc_df, "ETHUSDT": eth_df}

    long_funding, short_funding = lab.build_funding_gates(common, args.n_months)
    vol_overlay = lab.build_vol_overlays(asset_df_map)

    # Build the entry filter closure once; reuse across folds.
    volume_filter = build_volume_filter(asset_df_map)

    variant_names = ("baseline_funding_vol",
                     "baseline_funding_vol_plus_volume_conf")
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
        entry_filter_fn=volume_filter,
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

    # adoption verdict (auto-derived from headline numbers + Issue #35 gate)
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
        verdict = "**YES** — volume confirmation clears the PF and trade-count gates and preserves return."
    elif pf_pass and not trade_kept:
        verdict = ("**NO** — PF holds but trade count drops materially; "
                   "the filter over-prunes and is rejected on the "
                   "\"without killing trade count\" half of the question.")
    elif pf_pass and not ret_kept:
        verdict = ("**NO** — PF improves but absolute return falls "
                   "more than half; the filter trims edge along with chop.")
    else:
        verdict = "**NO** — filter fails the PF or trade-count adoption gate."

    lab.render_md_comparison(
        out_path=md_path,
        title=f"Volume confirmation filter comparison (Issue #35) — {ts}",
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

    # also write the research-folder report (a duplicate of the MD
    # with extra context bullets)
    extra_blurb = (
        "## Methodology\n\n"
        "The runner reuses the locked Issue #20 / #21 funding gate and "
        "the Issue #27 / #33 vol_sizing overlay verbatim. The only "
        "added behaviour is the per-asset rolling-20-bar volume mean "
        "comparison at the signal bar. Both variants share the same "
        "walk-forward geometry; only the entry-filter callback differs."
        "\n\nVolume mean is causal (computed from the 20-bar history "
        "ending at the signal bar's close). Warmup bars (first 20 of "
        "the universe) fail open — the volume filter does not block "
        "during indicator warmup.\n\n"
        "## Filter rule\n\n"
        "```\n"
        "vol_mean_20 = volume.rolling(20).mean()\n"
        "allow_entry = volume[signal_bar] >= vol_mean_20[signal_bar]\n"
        "```\n\n"
        "Same rule for long and short. Direction does not change the "
        "rule, only whether `signals.long_entry` or `signals.short_entry` "
        "would have fired.\n\n"
        "## Counter-factual diagnostics\n\n"
        f"- longs filtered out: {results[1]['longs_filtered_out']}\n"
        f"- shorts filtered out: {results[1]['shorts_filtered_out']}\n"
        f"- detailed trades CSV: `{trades_path}`\n"
    )
    research_md = (
        f"# Volume confirmation filter — research report (Issue #35)\n\n"
        f"_Generated {ts}._\n\n"
        f"{EXPERIMENT_BLURB}\n\n"
        f"{extra_blurb}\n"
    )
    # Append the same comparison table content by re-rendering — easier
    # to read in one file.
    with open(md_path, "r") as fh:
        md_content = fh.read()
    # Skip the H1 of md_content; keep the body.
    md_body = md_content.split("\n", 1)[1] if "\n" in md_content else md_content
    report_path.write_text(research_md + "\n" + md_body)
    log(f"wrote research report -> {report_path}")

    lab.write_trades_csv(trades_path, results)
    log(f"wrote detailed trades CSV -> {trades_path}")
    log("done. No live config modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
