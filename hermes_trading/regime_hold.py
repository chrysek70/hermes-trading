"""Buy-and-hold with regime-based position sizing.

Holds the asset continuously, scaling exposure by the Markov regime each bar.
The benchmark is *naive HODL* (size=1.0 always), not zero — this strategy
tries to inherit the asset's actual drift edge while sidestepping the worst
drawdowns by going lighter (or flat) in adverse regimes.

Important design property: the size map is *config*, not *fit*, so there is
no train/test split to worry about — applying the same size map to every fold
is the same as running it on the full window. State classification is causal
(rolling backward windows only), so there's no lookahead either.

A turnover fee is charged whenever the size changes, so flipping aggressively
between regimes can't be free.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
import yaml

from . import STATE_DIR, log
from . import data as data_mod
from .markov_regime import MarkovRegimeModel

# Sensible defaults — cut size when trend is down or volatile, full when calm + up.
DEFAULT_SIZE_MAP = {
    "up_low_vol": 1.00,
    "up_high_vol": 0.75,
    "sideways_low_vol": 0.75,
    "sideways_high_vol": 0.50,
    "down_low_vol": 0.25,
    "down_high_vol": 0.00,
}


def _metrics(equity: pd.Series, returns: pd.Series, bars_per_year: int) -> dict:
    final = float(equity.iloc[-1])
    peak = equity.cummax()
    max_dd = float(-((equity - peak) / peak).min())
    sharpe = float(returns.mean() / returns.std() * np.sqrt(bars_per_year)) if returns.std() > 0 else 0.0
    return {"total_return": final - 1.0, "max_drawdown": max_dd, "sharpe": sharpe}


def run_regime_hold(
    df: pd.DataFrame,
    markov_cfg: dict,
    size_map: dict | None = None,
    fee: float = 0.0005,
    bars_per_year: int = 8760,
) -> dict:
    """Compute regime-sized hold equity vs naive HODL on the same df."""
    size_map = size_map or DEFAULT_SIZE_MAP

    states = MarkovRegimeModel(markov_cfg).classify_states(df)
    bar_returns = df["close"].pct_change().fillna(0.0)

    # Size set at the CLOSE of bar i (based on the regime at bar i), applied
    # to bar i+1's return. The shift kills any lookahead.
    sizes = states.map(size_map).reindex(df.index).fillna(0.0)
    size_shifted = sizes.shift(1).fillna(0.0)
    size_delta = size_shifted.diff().abs().fillna(size_shifted)

    strategy_returns = size_shifted * bar_returns - fee * size_delta
    equity = (1.0 + strategy_returns).cumprod()

    hodl_returns = bar_returns
    hodl_equity = (1.0 + hodl_returns).cumprod()

    state_dist = states.dropna().value_counts(normalize=True).to_dict()
    return {
        "strategy": _metrics(equity, strategy_returns, bars_per_year),
        "hodl": _metrics(hodl_equity, hodl_returns, bars_per_year),
        "state_distribution": state_dist,
        "size_map": size_map,
        "span": (df.index[0].date(), df.index[-1].date()),
        "fee": fee,
    }


def _report(res: dict) -> None:
    s, h = res["strategy"], res["hodl"]
    span = res["span"]
    print()
    print(f"  regime-hold vs naive HODL   {span[0]} -> {span[1]}   fee/turnover={res['fee']}")
    print("  " + "-" * 70)
    print(f"  size map (weighted by time in each regime):")
    for state, sz in res["size_map"].items():
        share = res["state_distribution"].get(state, 0.0)
        print(f"    {state:<22}  size={sz:.2f}   ({100*share:5.1f}% of bars)")
    print()
    print(f"  {'':10}  {'regime-hold':>14}  {'naive HODL':>14}")
    print(f"  return    {s['total_return']*100:+13.2f}%  {h['total_return']*100:+13.2f}%")
    print(f"  max DD    {s['max_drawdown']*100:13.2f}%  {h['max_drawdown']*100:13.2f}%")
    print(f"  sharpe    {s['sharpe']:14.3f}  {h['sharpe']:14.3f}")
    beat = s["total_return"] > h["total_return"]
    dd_better = s["max_drawdown"] < h["max_drawdown"]
    sharpe_better = s["sharpe"] > h["sharpe"]
    print()
    print(
        f"  beat HODL return? {'YES' if beat else 'NO':<5}   "
        f"smaller DD? {'YES' if dd_better else 'NO':<5}   "
        f"better Sharpe? {'YES' if sharpe_better else 'NO'}"
    )
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description="Buy-and-hold with regime-based de-risking.")
    ap.add_argument("--months", default=None)
    ap.add_argument("--n-months", type=int, default=24)
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--markov", default=str(STATE_DIR / "markov_regime.yaml"))
    ap.add_argument("--fee", type=float, default=0.0005, help="fee per unit of size change")
    args = ap.parse_args()

    with open(args.markov) as fh:
        markov_cfg = yaml.safe_load(fh)
    markov_cfg["enabled"] = True  # we use classify_states; transition matrix unused

    months = args.months.split(",") if args.months else None
    df = data_mod.load_klines(args.symbol, months=months, n_months=args.n_months)
    df = data_mod.resample(df, args.timeframe)

    # Annualisation factor for Sharpe based on timeframe.
    bpy = {"1m": 525600, "5m": 105120, "15m": 35040, "30m": 17520,
           "1h": 8760, "4h": 2190, "1d": 365}.get(args.timeframe, 8760)

    log(f"loaded {len(df)} {args.timeframe} bars; running regime-hold vs HODL …")
    res = run_regime_hold(df, markov_cfg, fee=args.fee, bars_per_year=bpy)
    _report(res)


if __name__ == "__main__":
    main()
