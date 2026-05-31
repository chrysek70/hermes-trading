#!/usr/bin/env python3
"""ADX trend-strength gate for SuperTrend entries (Issue #36).

Adds ONE entry filter on top of the adopted live candidate
(`state/live_multiasset_long_short_funding_vol.yaml` — BTC/ETH
long-short SuperTrend(10,3) + funding + vol_sizing Q1/Q2-Q3/Q4
ladder):

  - Compute ADX(14) per asset on the 4h bars using Wilder's
    smoothed True Range + directional movement (DI+, DI-) construction.
  - Require ``ADX_14 >= 20`` on the signal bar to allow entry.
  - Same threshold for long and short.

ADX implementation is INLINE in this runner (kept out of
`signals.py` per spec, so the alpha module remains unchanged).

Hard rules (Issue #36 spec):

  - No live worker changes; no yaml writes; no parameter sweep.
  - signals.py UNMODIFIED — filter applied at the runner level.
  - No funding threshold changes (long block p>=95, short block p<=5).
  - No vol_sizing rule changes (24-bar rv, train_months=12,
    Q1=1.00 / Q2_Q3=0.50 / Q4=0.25).
  - Walk-forward only; 48mo span; train=1440 / test=360 / embargo=6;
    fee=0.001/side; slippage=0.0005.

Variants:
  - ``baseline_funding_vol``  : adopted live candidate, no extra filter.
  - ``baseline_funding_vol_plus_adx`` : same + ADX(14) >= 20 filter.

Deliverables:
  - ``results/adx_gate_comparison_<ts>.csv``
  - ``results/adx_gate_comparison_<ts>.md``
  - ``research/adx_gate_report.md``

Adoption question (Issue #36):
  Does ADX reduce false SuperTrend flips in chop while preserving
  the core edge?
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
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


# ---- ADX(14) — standard Wilder's smoothed construction ---------------------

ADX_PERIOD = 14            # locked per spec
ADX_THRESHOLD = 20.0       # locked per spec


def adx_wilder(df: pd.DataFrame, period: int = ADX_PERIOD) -> pd.Series:
    """ADX(14) using Wilder's smoothed True Range + directional movement.

    Standard textbook formulation:
      - +DM[i] = high[i] - high[i-1]    if positive and > -DM
      - -DM[i] = low[i-1] - low[i]      if positive and > +DM
      - TR[i]  = max(high[i]-low[i], |high[i]-close[i-1]|, |low[i]-close[i-1]|)
      - Wilder-smooth TR, +DM, -DM with alpha = 1/period (EWMA).
      - DI+ = 100 * smoothed_+DM / smoothed_TR
      - DI- = 100 * smoothed_-DM / smoothed_TR
      - DX  = 100 * |DI+ - DI-| / (DI+ + DI-)
      - ADX = Wilder-smooth(DX)

    Causal — each bar's ADX uses only completed bars at and before it.
    Vectorised; one allocation per Series.
    """
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=df.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=df.index,
    )

    alpha = 1.0 / period
    tr_s = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_dm_s = plus_dm.ewm(alpha=alpha, adjust=False).mean()
    minus_dm_s = minus_dm.ewm(alpha=alpha, adjust=False).mean()

    di_plus = 100.0 * plus_dm_s / tr_s.replace(0.0, np.nan)
    di_minus = 100.0 * minus_dm_s / tr_s.replace(0.0, np.nan)
    dx = (
        100.0 * (di_plus - di_minus).abs()
        / (di_plus + di_minus).replace(0.0, np.nan)
    )
    adx = dx.ewm(alpha=alpha, adjust=False).mean()
    return adx


def build_adx_filter(asset_df_map: dict[str, pd.DataFrame]):
    """Return an ``entry_filter_fn`` closure that gates on
    ``ADX(14) >= 20``. Same threshold for long and short. NaN ADX
    (warmup) fails open."""
    adx_by_asset = {
        a: adx_wilder(df, period=ADX_PERIOD) for a, df in asset_df_map.items()
    }
    for a, s in adx_by_asset.items():
        non_na = s.dropna()
        if non_na.empty:
            log(f"  [yellow]ADX series for {a} is all NaN[/yellow]")
        else:
            log(f"  ADX({ADX_PERIOD}) for {a}: "
                f"min={non_na.min():.2f} med={non_na.median():.2f} "
                f"max={non_na.max():.2f}  "
                f"fraction>={ADX_THRESHOLD}: "
                f"{(non_na >= ADX_THRESHOLD).mean()*100:.1f}%")

    def fn(signal_row: pd.Series, direction: str, asset: str) -> bool:
        try:
            ts = signal_row.name
        except Exception:
            return True
        s = adx_by_asset.get(asset)
        if s is None or ts not in s.index:
            return True
        v = s.loc[ts]
        if pd.isna(v):
            return True  # warmup -> fail open
        return bool(float(v) >= ADX_THRESHOLD)

    return fn


EXPERIMENT_BLURB = (
    "Issue #36 — ADX(14) trend-strength gate at the SuperTrend flip "
    "bar. Rule: ``ADX_14 >= 20`` (Wilder's smoothed construction; locked, "
    "no tuning). Same threshold applied to long and short entries. ADX is "
    "computed inline in the runner so that `signals.py` stays unchanged."
)

ADOPTION_QUESTION = (
    "Does ADX reduce false SuperTrend flips in chop while preserving the "
    "core edge?"
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
    csv_path = out_dir / f"adx_gate_comparison_{ts}.csv"
    md_path = out_dir / f"adx_gate_comparison_{ts}.md"
    trades_path = out_dir / f"trades_adx_gate_{ts}.csv"
    report_path = research_dir / "adx_gate_report.md"

    strategy = yaml.safe_load(open(args.long_short_strategy))

    btc_df, eth_df, common = lab.load_universe(args.n_months, args.timeframe)
    asset_df_map = {"BTCUSDT": btc_df, "ETHUSDT": eth_df}

    long_funding, short_funding = lab.build_funding_gates(common, args.n_months)
    vol_overlay = lab.build_vol_overlays(asset_df_map)

    adx_filter = build_adx_filter(asset_df_map)

    variant_names = ("baseline_funding_vol",
                     "baseline_funding_vol_plus_adx")
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
        entry_filter_fn=adx_filter,
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
        verdict = ("**YES** — ADX(14)>=20 clears the PF and trade-count "
                   "gates while preserving the core edge.")
    elif pf_pass and not trade_kept:
        verdict = ("**NO** — PF holds but the ADX gate prunes too many "
                   "trades; the core edge is not preserved.")
    elif pf_pass and not ret_kept:
        verdict = ("**NO** — PF improves but absolute return falls more "
                   "than half; ADX is over-filtering trend follows.")
    else:
        verdict = "**NO** — filter fails the PF or trade-count adoption gate."

    lab.render_md_comparison(
        out_path=md_path,
        title=f"ADX trend-strength gate comparison (Issue #36) — {ts}",
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
        "ADX(14) is implemented inline in the runner using Wilder's "
        "smoothed True Range + directional movement construction "
        "(equivalent to TA-Lib's ADXR=14 family). The series is causal "
        "by construction; warmup bars fail open. The same threshold "
        "(``>= 20``) is applied to both long and short entries because "
        "the spec specifies one threshold and the test should not "
        "introduce a direction-asymmetry.\n\n"
        "## Filter rule\n\n"
        "```\n"
        "+DM = high[t] - high[t-1]      if positive and > -DM\n"
        "-DM = low[t-1] - low[t]        if positive and > +DM\n"
        "TR  = max(H-L, |H-Cprev|, |L-Cprev|)\n"
        "smooth = Wilder EWMA with alpha = 1/14\n"
        "DI+ = 100 * smooth(+DM) / smooth(TR)\n"
        "DI- = 100 * smooth(-DM) / smooth(TR)\n"
        "DX  = 100 * |DI+ - DI-| / (DI+ + DI-)\n"
        "ADX = smooth(DX)\n"
        "allow_entry = ADX >= 20\n"
        "```\n\n"
        "## Counter-factual diagnostics\n\n"
        f"- longs filtered out: {results[1]['longs_filtered_out']}\n"
        f"- shorts filtered out: {results[1]['shorts_filtered_out']}\n"
        f"- detailed trades CSV: `{trades_path}`\n"
    )
    research_md = (
        f"# ADX trend-strength gate — research report (Issue #36)\n\n"
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
