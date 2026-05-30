"""Backtester — replay the signal engine with regime-aware sizing + routing.

v2 API: accept a precomputed ``decisions_df`` indexed like the bars carrying
per-bar columns ``raw_state``, ``stable_state``, ``regime_score``,
``size_multiplier``, ``long_allowed``, ``allowed_setups``. The state machine
applies those:
  - ``markov_long_allowed`` gates long entries
  - ``markov_allowed_setups`` (if non-None) filters which setup names may fire
  - ``markov_size_multiplier`` multiplies the strategy's base position size

Legacy path (``markov_model=...``) still supported for v1 hard-filter
comparisons; it just builds a decisions_df internally.
"""
from __future__ import annotations

import argparse
import csv
import json

import numpy as np
import pandas as pd
import yaml

from . import STATE_DIR, log
from . import data as data_mod
from . import signals
from . import markov_regime as mr


def _snapshot_entry(row: dict, ts) -> dict:
    """Capture diagnostic columns at the moment of entry — used to enrich the
    trade record for post-hoc edge analysis. Missing columns are stored as
    None so the CSV stays well-typed."""
    keys = (
        "rsi", "ema_fast", "ema_slow", "ema_pull", "atr", "vwap",
        "ema_slope", "atr_pct", "vwap_distance_pct", "volume_zscore",
        "donchian_high", "donchian_low", "donchian_mid",
        "markov_state", "markov_stable_state", "markov_regime_score",
        "markov_size_multiplier", "markov_allowed_setups",
    )
    out = {f"entry_{k}" if k not in ("markov_state", "markov_stable_state",
                                    "markov_regime_score",
                                    "markov_size_multiplier",
                                    "markov_allowed_setups") else k: row.get(k)
           for k in keys}
    out["entry_ts"] = ts
    return out


def _run_state_machine(records: list[dict], strategy: dict, warmup: int, fee: float, slippage: float) -> dict:
    base_size = float(strategy["risk"].get("position_size_r", 0.5))

    position = None
    trades = []
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    bars_in_position = 0

    def close_trade(exit_fill: float, reason: str, opened_i: int, i: int, setup: str, direction: str):
        nonlocal equity, peak, max_dd
        if direction == "long":
            gross = (exit_fill - position["entry"]) / position["entry"]
        else:
            gross = (position["entry"] - exit_fill) / position["entry"]
        effective_size = base_size * float(position.get("size_multiplier", 1.0))
        fees_cost = 2.0 * fee * effective_size           # return-space, both sides
        slippage_cost = 2.0 * slippage * effective_size  # already embedded in fills; tracked for reporting
        net = (gross - 2 * fee) * effective_size
        equity *= 1.0 + net
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)

        exit_row = records[min(i, len(records) - 1)]
        trade = {
            "ret": net,
            "reason": reason,
            "setup": setup,
            "direction": direction,
            "bars": i - opened_i,
            "size_multiplier": float(position.get("size_multiplier", 1.0)),
            "markov_state": position.get("markov_state"),
            "markov_stable_state": position.get("markov_stable_state"),
            # ---- detailed diagnostic fields (Phase 2) ----
            "exit_ts": exit_row.get("ts"),
            "side": direction,
            "setup_name": setup,
            "exit_price": exit_fill,
            "gross_return_pct": gross,
            "fees_total": fees_cost,
            "slippage_total": slippage_cost,
            "net_return_pct": net,
            "holding_bars": i - opened_i,
            "exit_reason": reason,
            "position_size_effective": effective_size,
        }
        # carry entry snapshot through (entry_* fields + markov metadata + entry_ts)
        for k in (
            "entry_ts", "entry_rsi", "entry_ema_fast", "entry_ema_slow",
            "entry_ema_pull", "entry_atr", "entry_vwap", "entry_ema_slope",
            "entry_atr_pct", "entry_vwap_distance_pct", "entry_volume_zscore",
            "markov_regime_score", "markov_size_multiplier",
            "markov_allowed_setups",
        ):
            trade[k] = position.get(k)
        trade["entry_price"] = position["entry"]
        # Donchian-specific derived diagnostics
        dh = position.get("entry_donchian_high")
        if dh is not None and isinstance(dh, (int, float)) and not pd.isna(dh) and dh > 0:
            trade["donchian_breakout_distance_pct"] = (position["entry"] - float(dh)) / float(dh)
        else:
            trade["donchian_breakout_distance_pct"] = None
        init_stop = position.get("initial_stop")
        if init_stop is not None and position["entry"] > 0:
            trade["atr_stop_distance_pct"] = (position["entry"] - float(init_stop)) / position["entry"]
        else:
            trade["atr_stop_distance_pct"] = None
        # markov_route — flattened allowed_setups for human reading
        mas = position.get("markov_allowed_setups")
        if isinstance(mas, list):
            trade["markov_route"] = ",".join(mas) if mas else "(none)"
        elif mas is None:
            trade["markov_route"] = "(no markov)"
        else:
            trade["markov_route"] = str(mas)
        trades.append(trade)

    for i, row in enumerate(records):
        if i < warmup:
            continue

        if position is not None:
            bars_in_position += 1

        if position is None:
            if not bool(row.get("markov_long_allowed", True)):
                # short side may still be allowed; skip the long branch only
                long_ok = False
            else:
                long_ok = True

            allowed_setups = row.get("markov_allowed_setups", None)
            if isinstance(allowed_setups, float) and pd.isna(allowed_setups):
                allowed_setups = None

            size_mult = float(row.get("markov_size_multiplier", 1.0) or 0.0)

            if long_ok and size_mult > 0.0:
                setup_l = signals.long_entry(row, strategy)
                if setup_l and (allowed_setups is None or setup_l in allowed_setups):
                    init_stop = signals.initial_stop(row, setup_l, strategy)
                    position = {
                        "entry": row["close"] * (1 + slippage),
                        "setup": setup_l,
                        "direction": "long",
                        "i": i,
                        "stop": init_stop,
                        "initial_stop": init_stop,
                        "size_multiplier": size_mult,
                        "markov_state": row.get("markov_state"),
                        "markov_stable_state": row.get("markov_stable_state"),
                        **_snapshot_entry(row, row.get("ts")),
                    }
                    continue

            if size_mult > 0.0:
                setup_s = signals.short_entry(row, strategy)
                if setup_s:
                    base_setup = setup_s.replace("_short", "")
                    if allowed_setups is None or base_setup in allowed_setups:
                        position = {
                            "entry": row["close"] * (1 - slippage),
                            "setup": setup_s,
                            "direction": "short",
                            "i": i,
                            "stop": signals.initial_stop_short(row, setup_s, strategy),
                            "size_multiplier": size_mult,
                            "markov_state": row.get("markov_state"),
                            "markov_stable_state": row.get("markov_stable_state"),
                            **_snapshot_entry(row, row.get("ts")),
                        }
        else:
            if position["direction"] == "long":
                reason = signals.long_exit(row, position, strategy, i - position["i"])
                if reason:
                    exit_fill = (
                        position["stop"] * (1 - slippage)
                        if reason == "stop"
                        else row["close"] * (1 - slippage)
                    )
                    close_trade(exit_fill, reason, position["i"], i, position["setup"], "long")
                    position = None
            else:
                reason = signals.short_exit(row, position, strategy, i - position["i"])
                if reason:
                    exit_fill = (
                        position["stop"] * (1 + slippage)
                        if reason == "stop"
                        else row["close"] * (1 + slippage)
                    )
                    close_trade(exit_fill, reason, position["i"], i, position["setup"], "short")
                    position = None

    if position is not None:
        if position["direction"] == "long":
            exit_fill = records[-1]["close"] * (1 - slippage)
        else:
            exit_fill = records[-1]["close"] * (1 + slippage)
        close_trade(exit_fill, "end", position["i"], len(records) - 1, position["setup"], position["direction"])

    rets = [t["ret"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    metrics = {
        "trades": len(trades),
        "total_return": equity - 1.0,
        "max_drawdown": max_dd,
        "win_rate": (len(wins) / len(trades)) if trades else 0.0,
        "profit_factor": (sum(wins) / abs(sum(losses))) if sum(losses) != 0 else float("inf"),
        "sharpe_per_trade": (np.mean(rets) / np.std(rets)) if len(rets) > 1 and np.std(rets) > 0 else 0.0,
        "avg_win": float(np.mean(wins)) if wins else 0.0,
        "avg_loss": float(np.mean(losses)) if losses else 0.0,
        # exposure metrics — fraction of bars in position post-warmup
        "exposure_pct": (bars_in_position / max(1, len(records) - warmup)),
    }
    by_setup = {s: sum(1 for t in trades if t["setup"] == s) for s in ("pullback", "breakout", "pullback_short", "breakout_short")}
    by_reason: dict[str, int] = {}
    for t in trades:
        by_reason[t["reason"]] = by_reason.get(t["reason"], 0) + 1

    by_state = compute_by_state_metrics(trades)

    return {
        "metrics": metrics,
        "by_setup": by_setup,
        "by_reason": by_reason,
        "by_state": by_state,
        "trades": trades,
    }


def compute_by_state_metrics(trades: list[dict]) -> dict:
    """Per-stable-state breakdown of trade outcomes."""
    groups: dict[str, list[dict]] = {}
    for t in trades:
        s = t.get("markov_stable_state") or t.get("markov_state") or "unknown"
        groups.setdefault(str(s), []).append(t)
    out: dict[str, dict] = {}
    for s, ts in groups.items():
        rets = [t["ret"] for t in ts]
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r <= 0]
        bars = [t["bars"] for t in ts]
        setup_groups: dict[str, list[float]] = {}
        reason_groups: dict[str, list[float]] = {}
        for t in ts:
            setup_groups.setdefault(str(t.get("setup", "?")), []).append(t["ret"])
            reason_groups.setdefault(str(t.get("reason", "?")), []).append(t["ret"])
        equity = 1.0
        for r in rets:
            equity *= 1.0 + r
        out[s] = {
            "trades": len(rets),
            "total_return": equity - 1.0,
            "win_rate": (len(wins) / len(rets)) if rets else 0.0,
            "avg_return": float(np.mean(rets)) if rets else 0.0,
            "median_return": float(np.median(rets)) if rets else 0.0,
            "profit_factor": (sum(wins) / abs(sum(losses))) if sum(losses) != 0 else float("inf"),
            "expectancy": float(np.mean(rets)) if rets else 0.0,
            "avg_holding_bars": float(np.mean(bars)) if bars else 0.0,
            "by_setup": {sp: {
                "trades": len(r),
                "avg_return": float(np.mean(r)),
                "win_rate": float(sum(1 for x in r if x > 0) / len(r)) if r else 0.0,
            } for sp, r in setup_groups.items()},
            "by_reason": {rs: {
                "trades": len(r),
                "avg_return": float(np.mean(r)),
            } for rs, r in reason_groups.items()},
        }
    return out


def write_state_performance_csv(by_state: dict, path: str) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "state", "trades", "total_return", "win_rate", "avg_return",
            "median_return", "profit_factor", "expectancy", "avg_holding_bars",
            "by_setup_json", "by_reason_json",
        ])
        for s, m in sorted(by_state.items(), key=lambda kv: -kv[1]["trades"]):
            pf = m["profit_factor"]
            w.writerow([
                s, m["trades"], m["total_return"], m["win_rate"], m["avg_return"],
                m["median_return"], (pf if pf != float("inf") else "inf"),
                m["expectancy"], m["avg_holding_bars"],
                json.dumps(m["by_setup"]), json.dumps(m["by_reason"]),
            ])


def _attach_diagnostics(ind: pd.DataFrame, vol_window: int = 20) -> pd.DataFrame:
    """Add post-hoc diagnostic columns used by Phase 2 detailed trade logging.
    All computations are causal — only past bars used at each row."""
    out = ind.copy()
    out["ema_slope"] = out["ema_fast"].pct_change(5)
    out["atr_pct"] = out["atr"] / out["close"]
    out["vwap_distance_pct"] = (out["close"] - out["vwap"]) / out["vwap"]
    vol_mean = out["volume"].rolling(vol_window, min_periods=2).mean()
    vol_std = out["volume"].rolling(vol_window, min_periods=2).std()
    out["volume_zscore"] = (out["volume"] - vol_mean) / vol_std.replace(0.0, np.nan)
    return out


def write_trades_detailed_csv(trades: list[dict], path: str) -> None:
    """Write the per-trade diagnostic CSV requested by Phase 2."""
    columns = [
        "entry_ts", "exit_ts", "side", "setup_name",
        "entry_price", "exit_price",
        "gross_return_pct", "fees_total", "slippage_total", "net_return_pct",
        "holding_bars", "exit_reason",
        "entry_rsi", "entry_ema_fast", "entry_ema_slow", "entry_ema_pull",
        "entry_atr", "entry_vwap",
        "entry_ema_slope", "entry_atr_pct", "entry_vwap_distance_pct",
        "entry_volume_zscore",
        "entry_donchian_high", "entry_donchian_low", "entry_donchian_mid",
        "donchian_breakout_distance_pct", "atr_stop_distance_pct",
        "markov_state", "markov_stable_state", "markov_regime_score",
        "markov_size_multiplier", "markov_allowed_setups", "markov_route",
        "position_size_effective", "size_multiplier",
    ]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(columns)
        for t in trades:
            row = []
            for c in columns:
                v = t.get(c)
                if hasattr(v, "isoformat"):
                    v = v.isoformat()
                if isinstance(v, list):
                    v = ",".join(map(str, v))
                if v is None:
                    v = ""
                row.append(v)
            w.writerow(row)


def _attach_neutral_markov_columns(ind: pd.DataFrame, df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Default columns when no regime model gates trades. State labels are
    still classified post-hoc (no sizing/gating effect) so trade records carry
    a regime tag for diagnostics."""
    out = ind.copy()
    out["markov_long_allowed"] = True
    out["markov_size_multiplier"] = 1.0
    out["markov_allowed_setups"] = pd.Series([None] * len(out), index=out.index, dtype=object)
    out["markov_regime_score"] = 1.0

    raw = None
    stable = None
    if df is not None:
        try:
            cfg_path = STATE_DIR / "markov_regime.yaml"
            if cfg_path.exists():
                with open(cfg_path) as fh:
                    dcfg = yaml.safe_load(fh)
                tmp = mr.MarkovRegimeModel(dcfg)
                raw = tmp.classify_states(df)
                hb = int(dcfg.get("state", {}).get("hysteresis_bars", 1))
                stable = mr.apply_hysteresis(raw, hb)
        except Exception:  # noqa: BLE001 — diagnostics only, never fatal
            raw = None
            stable = None
    out["markov_state"] = raw.reindex(out.index).values if raw is not None else None
    out["markov_stable_state"] = stable.reindex(out.index).values if stable is not None else None
    return out


def _attach_decisions_df(ind: pd.DataFrame, decisions: pd.DataFrame) -> pd.DataFrame:
    """Merge a decisions_df (from markov_regime.compute_decisions or multi-TF)
    onto the indicator frame."""
    out = ind.copy()
    aligned = decisions.reindex(ind.index)
    out["markov_state"] = aligned["raw_state"].values if "raw_state" in aligned else None
    out["markov_stable_state"] = aligned["stable_state"].values if "stable_state" in aligned else None
    out["markov_long_allowed"] = (
        aligned["long_allowed"].fillna(True).astype(bool).values if "long_allowed" in aligned else True
    )
    out["markov_size_multiplier"] = (
        aligned["size_multiplier"].fillna(1.0).astype(float).values if "size_multiplier" in aligned else 1.0
    )
    out["markov_regime_score"] = (
        aligned["regime_score"].fillna(1.0).astype(float).values if "regime_score" in aligned else 1.0
    )
    if "allowed_setups" in aligned.columns:
        out["markov_allowed_setups"] = pd.Series(list(aligned["allowed_setups"].values), index=ind.index, dtype=object)
    else:
        out["markov_allowed_setups"] = pd.Series([None] * len(ind), index=ind.index, dtype=object)
    return out


def run_backtest(
    df,
    strategy,
    fee=0.001,
    slippage=0.0005,
    warmup=250,
    markov_model=None,
    decisions_df=None,
) -> dict:
    ind = signals.compute_indicators(df, strategy)
    ind = _attach_diagnostics(ind)
    ind["ts"] = ind.index  # carry timestamp through to_dict("records")

    if decisions_df is not None:
        ind = _attach_decisions_df(ind, decisions_df)
    elif markov_model is not None and markov_model.cfg.get("enabled", False):
        # Build a decisions_df via the v2 API (mode-aware).
        try:
            decisions = mr.compute_decisions(df, markov_model, markov_model.cfg)
            ind = _attach_decisions_df(ind, decisions)
        except Exception:
            ind = _attach_neutral_markov_columns(ind, df=df)
    else:
        ind = _attach_neutral_markov_columns(ind, df=df)

    return _run_state_machine(ind.to_dict("records"), strategy, warmup, fee, slippage)


def _report(res: dict, strategy: dict, span: tuple, mode_label: str = "off") -> None:
    m = res["metrics"]
    print()
    print(f"  strategy {strategy.get('version')}   {span[0]} -> {span[1]}   markov={mode_label}")
    print("  " + "-" * 64)
    print(f"  trades            {m['trades']}")
    print(f"  total return      {m['total_return'] * 100:+.2f}%")
    print(f"  max drawdown      {m['max_drawdown'] * 100:.2f}%")
    print(f"  win rate          {m['win_rate'] * 100:.1f}%")
    pf = m["profit_factor"]
    print(f"  profit factor     {pf:.2f}" if pf != float("inf") else "  profit factor     inf")
    print(f"  sharpe (per-trade) {m['sharpe_per_trade']:.3f}")
    print(f"  exposure          {m['exposure_pct'] * 100:.1f}%")
    print(f"  avg win / loss    {m['avg_win'] * 100:+.3f}% / {m['avg_loss'] * 100:+.3f}%")
    print(f"  by setup          {res['by_setup']}")
    print(f"  by exit reason    {res['by_reason']}")
    if res.get("by_state"):
        print(f"  by markov state:")
        for s, bs in sorted(res["by_state"].items(), key=lambda kv: -kv[1]["trades"]):
            pf_s = bs["profit_factor"]
            pf_str = f"{pf_s:.2f}" if pf_s != float("inf") else "  inf"
            print(
                f"    {s:<22} n={bs['trades']:>4}  ret={bs['total_return']*100:+6.2f}%  "
                f"win={bs['win_rate']*100:5.1f}%  PF={pf_str:>6}  exp={bs['expectancy']*100:+.3f}%"
            )
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest the signal engine on historical BTC.")
    ap.add_argument("--months", default=None)
    ap.add_argument("--n-months", type=int, default=3)
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--timeframe", default="1m")
    ap.add_argument("--warmup", type=int, default=250)
    ap.add_argument("--strategy", default=str(STATE_DIR / "strategy_v2.yaml"))
    ap.add_argument("--markov", default=None)
    ap.add_argument("--markov-enable", action="store_true")
    ap.add_argument("--state-csv", default=None, help="if set, write per-state CSV here")
    ap.add_argument("--trades-csv", default=None, help="if set, write the Phase-2 detailed per-trade CSV here")
    args = ap.parse_args()

    with open(args.strategy) as fh:
        strategy = yaml.safe_load(fh)
    months = args.months.split(",") if args.months else None
    df = data_mod.load_klines(args.symbol, months=months, n_months=args.n_months)
    df = data_mod.resample(df, args.timeframe)

    markov_model = None
    mode_label = "off"
    if args.markov:
        with open(args.markov) as fh:
            m_cfg = yaml.safe_load(fh)
        if args.markov_enable:
            m_cfg["enabled"] = True
        if m_cfg.get("enabled"):
            markov_model = mr.MarkovRegimeModel(m_cfg)
            markov_model.fit(df)
            mode_label = m_cfg.get("mode", "disabled")
            log(f"markov fitted on {len(df)} bars (in-sample); mode={mode_label}")

    log(f"loaded {len(df)} {args.timeframe} bars; backtest (fee={args.fee}, slip={args.slippage}) …")
    res = run_backtest(df, strategy, fee=args.fee, slippage=args.slippage,
                       warmup=args.warmup, markov_model=markov_model)
    _report(res, strategy, (df.index[0].date(), df.index[-1].date()), mode_label=mode_label)
    if args.state_csv:
        write_state_performance_csv(res["by_state"], args.state_csv)
        log(f"wrote per-state CSV → {args.state_csv}")
    if args.trades_csv:
        write_trades_detailed_csv(res["trades"], args.trades_csv)
        log(f"wrote detailed trade CSV → {args.trades_csv}")


if __name__ == "__main__":
    main()
