"""Walk-forward harness — v2, mode-aware.

Per fold: slice train + (embargo) + test. Fit any data-dependent piece on the
TRAIN slice only. For ``bad_regime_avoidance`` the per-state bad-set is
identified by backtesting the BASELINE strategy on the train slice and
applying the train-only PF/expectancy thresholds — test outcomes never inform
the bad set. For ``multi_timeframe_*``, the Markov state classifier is fit
independently on each timeframe's train slice, and scores are combined onto
the decision TF.

Strategy parameters are config (not fit), so without Markov this is just an
honest OOS performance estimate of the strategy on history.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
import yaml

from . import STATE_DIR, log
from . import backtest as bt
from . import data as data_mod
from . import markov_regime as mr
from . import signals


def _stitch_metrics(trades: list[dict]) -> dict:
    rets = [t["ret"] for t in trades]
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for r in rets:
        equity *= 1.0 + r
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    avg_size = float(np.mean([t.get("size_multiplier", 1.0) for t in trades])) if trades else 1.0
    return {
        "trades": len(rets),
        "total_return": equity - 1.0,
        "max_drawdown": max_dd,
        "win_rate": (len(wins) / len(rets)) if rets else 0.0,
        "profit_factor": (sum(wins) / abs(sum(losses))) if sum(losses) != 0 else float("inf"),
        "sharpe_per_trade": (np.mean(rets) / np.std(rets)) if len(rets) > 1 and np.std(rets) > 0 else 0.0,
        "avg_win": float(np.mean(wins)) if wins else 0.0,
        "avg_loss": float(np.mean(losses)) if losses else 0.0,
        "avg_size_multiplier": avg_size,
    }


def _build_single_tf_decisions(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    cfg: dict,
    strategy: dict,
    fee: float,
    slippage: float,
) -> pd.DataFrame | None:
    """Fit one Markov model on train, return test-window decisions.

    For mode == bad_regime_avoidance, runs the baseline strategy on train to
    derive the bad-state set (TRAIN-ONLY), then feeds that into the test
    decisions.
    """
    try:
        model = mr.MarkovRegimeModel(cfg)
        model.fit(train_df)
    except Exception as exc:  # noqa: BLE001
        log(f"  markov fit failed ({exc}); fold runs neutral")
        return None

    mode = str(cfg.get("mode", "disabled"))
    bad_set: set[str] = set()

    if mode == "bad_regime_avoidance":
        # Run BASELINE on train (neutral decisions) to get per-state trades.
        train_ind = signals.compute_indicators(train_df, strategy)
        train_ind = bt._attach_neutral_markov_columns(train_ind)
        # Then tag with stable_state for per-state metrics computation.
        train_decisions = mr.compute_decisions(train_df, model, cfg)
        train_ind["markov_stable_state"] = train_decisions["stable_state"].reindex(train_ind.index).values
        baseline_train = bt._run_state_machine(train_ind.to_dict("records"), strategy, warmup=0, fee=fee, slippage=slippage)
        bad_set, per_state = mr.identify_bad_states_from_train(baseline_train["trades"], cfg)
        log(
            f"  train trades={len(baseline_train['trades'])} → bad states "
            f"from train: {sorted(bad_set) if bad_set else '(none)'}"
        )

    return mr.compute_decisions(test_df, model, cfg, bad_state_set=bad_set)


def _build_multi_tf_decisions(
    full_df: pd.DataFrame,
    train_lo: int,
    train_hi: int,
    test_lo: int,
    test_hi: int,
    cfg: dict,
    decision_index: pd.DatetimeIndex,
) -> pd.DataFrame | None:
    """For each timeframe in cfg.multi_timeframe.weights: resample, fit on
    train slice, score test slice, combine onto decision_index."""
    weights = cfg.get("multi_timeframe", {}).get("weights", {})
    if not weights:
        return None
    decisions_by_tf: dict[str, pd.DataFrame] = {}
    train_full = full_df.iloc[train_lo:train_hi]
    test_full = full_df.iloc[test_lo:test_hi]
    for tf in weights:
        try:
            train_tf = data_mod.resample(train_full, tf)
            test_tf = data_mod.resample(test_full, tf)
            model_tf = mr.MarkovRegimeModel(cfg)
            model_tf.fit(train_tf)
            decisions_by_tf[tf] = mr.compute_decisions(test_tf, model_tf, cfg)
        except Exception as exc:  # noqa: BLE001
            log(f"  multi-tf {tf} skipped ({exc})")
    if not decisions_by_tf:
        return None
    return mr.multi_timeframe_score(decisions_by_tf, weights, decision_index)


def walk_forward(
    df: pd.DataFrame,
    strategy: dict,
    markov_cfg: dict | None = None,
    train_bars: int = 5760,
    test_bars: int = 1440,
    mode_window: str = "rolling",
    embargo_bars: int = 0,
    fee: float = 0.001,
    slippage: float = 0.0005,
) -> dict:
    ind_full = signals.compute_indicators(df, strategy)
    use_markov = bool(markov_cfg and markov_cfg.get("enabled"))
    multi_tf = bool(use_markov and markov_cfg.get("multi_timeframe", {}).get("enabled"))
    label = markov_cfg.get("mode", "disabled") if use_markov else "off"
    if multi_tf:
        label = f"{label}+multi_tf"

    n = len(ind_full)
    folds = []
    all_trades: list[dict] = []
    fold = 0
    cursor = 0
    while True:
        if mode_window == "rolling":
            train_lo, train_hi = cursor, cursor + train_bars
        else:
            train_lo, train_hi = 0, train_bars + cursor
        test_lo = train_hi + embargo_bars
        test_hi = test_lo + test_bars
        if test_hi > n:
            break
        fold += 1

        train_df = df.iloc[train_lo:train_hi]
        test_df = df.iloc[test_lo:test_hi]

        test_ind = ind_full.iloc[test_lo:test_hi].copy()

        if not use_markov:
            test_ind = bt._attach_neutral_markov_columns(test_ind)
        else:
            if multi_tf:
                decisions = _build_multi_tf_decisions(
                    df, train_lo, train_hi, test_lo, test_hi, markov_cfg, test_ind.index,
                )
            else:
                decisions = _build_single_tf_decisions(train_df, test_df, markov_cfg, strategy, fee, slippage)

            if decisions is None:
                test_ind = bt._attach_neutral_markov_columns(test_ind)
            else:
                test_ind = bt._attach_decisions_df(test_ind, decisions)

        res = bt._run_state_machine(test_ind.to_dict("records"), strategy, warmup=0, fee=fee, slippage=slippage)
        all_trades.extend(res["trades"])
        folds.append({
            "fold": fold,
            "train_range": (df.index[train_lo].date(), df.index[train_hi - 1].date()),
            "test_range": (df.index[test_lo].date(), df.index[test_hi - 1].date()),
            "test_metrics": res["metrics"],
            "test_by_state": res["by_state"],
        })
        cursor += test_bars

    return {
        "folds": folds,
        "oos_metrics": _stitch_metrics(all_trades),
        "by_state": bt.compute_by_state_metrics(all_trades),
        "mode_label": label,
        "trades": all_trades,
    }


def _report(res: dict, span: tuple) -> None:
    print()
    print(f"  walk-forward  {span[0]} -> {span[1]}  markov={res['mode_label']}")
    print("  " + "-" * 80)
    print(f"  {'fold':>4}  {'train':<27}  {'test':<27}  {'n':>4}  {'ret':>8}  {'PF':>5}")
    for f in res["folds"]:
        m = f["test_metrics"]
        pf = m["profit_factor"]
        pf_str = f"{pf:5.2f}" if pf != float("inf") else "  inf"
        tr = f"{f['train_range'][0]} .. {f['train_range'][1]}"
        te = f"{f['test_range'][0]} .. {f['test_range'][1]}"
        print(f"  {f['fold']:>4}  {tr:<27}  {te:<27}  {m['trades']:>4}  {m['total_return']*100:+7.2f}%  {pf_str}")
    print()
    m = res["oos_metrics"]
    print("  STITCHED OOS")
    print(f"    trades            {m['trades']}")
    print(f"    total return      {m['total_return']*100:+.2f}%")
    print(f"    max drawdown      {m['max_drawdown']*100:.2f}%")
    print(f"    win rate          {m['win_rate']*100:.1f}%")
    pf = m["profit_factor"]
    print(f"    profit factor     {pf:.2f}" if pf != float("inf") else "    profit factor     inf")
    print(f"    sharpe            {m['sharpe_per_trade']:.3f}")
    print(f"    avg size mult     {m['avg_size_multiplier']:.3f}")
    print(f"    avg win / loss    {m['avg_win']*100:+.3f}% / {m['avg_loss']*100:+.3f}%")
    if res.get("by_state"):
        print("    by markov state:")
        for s, bs in sorted(res["by_state"].items(), key=lambda kv: -kv[1]["trades"]):
            pf_s = bs["profit_factor"]
            pf_str = f"{pf_s:5.2f}" if pf_s != float("inf") else "  inf"
            print(
                f"      {s:<22} n={bs['trades']:>4}  ret={bs['total_return']*100:+6.2f}%  "
                f"win={bs['win_rate']*100:5.1f}%  PF={pf_str}  exp={bs['expectancy']*100:+.3f}%"
            )
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description="Walk-forward harness (v2 — mode-aware).")
    ap.add_argument("--months", default=None)
    ap.add_argument("--n-months", type=int, default=24)
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--strategy", default=str(STATE_DIR / "strategy_v2.yaml"))
    ap.add_argument("--markov", default=None)
    ap.add_argument("--markov-enable", action="store_true")
    ap.add_argument("--mode", default=None, help="override yaml's mode")
    ap.add_argument("--window", choices=["rolling", "anchored"], default="rolling")
    ap.add_argument("--train-bars", type=int, default=5760)
    ap.add_argument("--test-bars", type=int, default=1440)
    ap.add_argument("--embargo-bars", type=int, default=0)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.0005)
    args = ap.parse_args()

    with open(args.strategy) as fh:
        strategy = yaml.safe_load(fh)

    markov_cfg = None
    if args.markov:
        with open(args.markov) as fh:
            markov_cfg = yaml.safe_load(fh)
        if args.markov_enable:
            markov_cfg["enabled"] = True
        if args.mode:
            markov_cfg["mode"] = args.mode

    months = args.months.split(",") if args.months else None
    df = data_mod.load_klines(args.symbol, months=months, n_months=args.n_months)
    df = data_mod.resample(df, args.timeframe)

    log(
        f"loaded {len(df)} {args.timeframe} bars; walk-forward window={args.window} "
        f"train={args.train_bars} test={args.test_bars} embargo={args.embargo_bars}"
    )
    res = walk_forward(
        df, strategy, markov_cfg=markov_cfg,
        train_bars=args.train_bars, test_bars=args.test_bars,
        mode_window=args.window, embargo_bars=args.embargo_bars,
        fee=args.fee, slippage=args.slippage,
    )
    _report(res, (df.index[0].date(), df.index[-1].date()))


if __name__ == "__main__":
    main()
