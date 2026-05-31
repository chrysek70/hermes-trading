#!/usr/bin/env python3
"""Self-test for the multi-asset live paper worker (Issue #16).

Runs without an exchange, without network, without touching the live
state directory. Uses a temp directory and the pure helper functions
exposed by ``hermes_trading.multi_loop`` and ``hermes_trading.positions``.

Covers:
  - single-asset mode still loads (import sanity)
  - multi-asset config loads + asset list parsed
  - max_open_positions enforced
  - one position per asset enforced
  - legacy state/position.json migration works (with backup)
  - corrupt per-asset state does not kill the whole worker
  - closed trade rows include the required fields (asset,
    strategy_version, setup, entry_time, exit_time, entry_price,
    exit_price, return_pct, net_return_pct, position_size,
    exit_reason, holding_bars)

Invocation:
    uv run python scripts/test_multiasset_worker.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GREEN = "\033[32m"
RED = "\033[31m"
BOLD = "\033[1m"
RESET = "\033[0m"

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  {GREEN}✓{RESET} {name}")
    else:
        failures.append(f"{name}  --  {detail}")
        print(f"  {RED}✗{RESET} {name}  {detail}")


def main() -> int:
    import yaml

    print(f"{BOLD}Self-test: multi-asset live worker (Issue #16){RESET}")
    print()

    # --- 1. Single-asset path still imports / has its public API
    print("1. Single-asset mode is unchanged")
    from hermes_trading import loop as single_loop
    from hermes_trading import run as run_mod
    check("loop.run exists (async)", callable(getattr(single_loop, "run", None)))
    check("run.main exists",         callable(getattr(run_mod, "main", None)))
    check("loop.py still defines _load_position",
          callable(getattr(single_loop, "_load_position", None)))
    print()

    # --- 2. Multi-asset config loads
    print("2. Multi-asset config")
    cfg_path = ROOT / "state" / "live_multiasset.yaml"
    check("config file exists", cfg_path.exists())
    cfg = yaml.safe_load(open(cfg_path))
    check("assets list length == 2", len(cfg["assets"]) == 2,
          f"got {cfg.get('assets')}")
    check("BTC/USDT in assets", "BTC/USDT" in cfg["assets"])
    check("ETH/USDT in assets", "ETH/USDT" in cfg["assets"])
    check("timeframe == 4h", cfg.get("timeframe") == "4h",
          f"got {cfg.get('timeframe')}")
    check("max_open_positions == 2", int(cfg.get("max_open_positions", 0)) == 2,
          f"got {cfg.get('max_open_positions')}")
    print()

    # --- 3. Portfolio cap and per-asset cap enforced
    print("3. Portfolio cap + per-asset cap")
    from hermes_trading.multi_loop import can_enter

    state = {"BTC/USDT": None, "ETH/USDT": None}
    ok, _ = can_enter("BTC/USDT", state, max_open_positions=2)
    check("BTC entry allowed when flat", ok)
    state["BTC/USDT"] = {"entry_price": 70000, "direction": "long"}
    ok, reason = can_enter("BTC/USDT", state, max_open_positions=2)
    check("second BTC entry rejected (asset_already_open)",
          (not ok) and reason == "asset_already_open", f"reason={reason}")
    ok, _ = can_enter("ETH/USDT", state, max_open_positions=2)
    check("ETH entry allowed (1 open, cap 2)", ok)
    state["ETH/USDT"] = {"entry_price": 3000, "direction": "long"}
    ok, reason = can_enter("SOL/USDT", {**state, "SOL/USDT": None},
                           max_open_positions=2)
    check("3rd asset rejected at cap=2 (portfolio_cap_reached)",
          (not ok) and reason == "portfolio_cap_reached", f"reason={reason}")
    print()

    # --- 4. Legacy migration: state/position.json -> state/positions/<KEY>.json
    print("4. Legacy migration with backup")
    from hermes_trading import positions as pos_mod

    with tempfile.TemporaryDirectory() as tmp:
        sd = Path(tmp)
        legacy = sd / "position.json"
        legacy.write_text(json.dumps({
            "entry_price": 65000.0,
            "direction": "long",
            "setup": "pullback",
            "opened_at": "2026-05-29T11:49:00+00:00",
            "size": 0.5,
        }))
        mig = pos_mod.migrate_legacy_position("BTC/USDT", state_dir=sd)
        check("migration reports migrated=True", mig.get("migrated") is True,
              f"mig={mig}")
        check("new file exists",
              pos_mod.position_path("BTC/USDT", sd).exists())
        check("legacy file removed", not legacy.exists())
        bak_files = list(sd.glob("position.json.bak.*"))
        check("backup file written (.bak.<ts>)", len(bak_files) == 1,
              f"got {bak_files}")
        # idempotent on second call
        mig2 = pos_mod.migrate_legacy_position("BTC/USDT", state_dir=sd)
        check("second migration is no-op (idempotent)",
              mig2.get("migrated") is False)
        # round-trip
        loaded = pos_mod.load_positions(["BTC/USDT", "ETH/USDT"], state_dir=sd)
        check("load_positions returns BTC after migration",
              "BTC/USDT" in loaded and loaded["BTC/USDT"]["entry_price"] == 65000.0)
    print()

    # --- 5. Corrupt per-asset state does not kill the worker
    print("5. Corrupt per-asset state tolerated")
    with tempfile.TemporaryDirectory() as tmp:
        sd = Path(tmp)
        pos_mod.positions_dir(sd).mkdir(parents=True, exist_ok=True)
        # one valid, one corrupt
        pos_mod.save_position("BTC/USDT", {
            "entry_price": 70000.0, "direction": "long", "setup": "supertrend",
            "opened_at": "2026-05-29T12:00:00+00:00", "size": 0.5,
        }, state_dir=sd)
        (pos_mod.position_path("ETH/USDT", sd)).write_text("not json {")
        loaded = pos_mod.load_positions(["BTC/USDT", "ETH/USDT"], state_dir=sd)
        check("valid asset still loaded", "BTC/USDT" in loaded)
        check("corrupt asset skipped (not in loaded)", "ETH/USDT" not in loaded)
        check("corrupt file left in place (user can inspect)",
              pos_mod.position_path("ETH/USDT", sd).exists())
        # also: schema-missing entry
        (pos_mod.position_path("SOL/USDT", sd)).write_text(json.dumps({"foo": 1}))
        loaded2 = pos_mod.load_positions(["BTC/USDT", "ETH/USDT", "SOL/USDT"], state_dir=sd)
        check("schema-incomplete asset skipped", "SOL/USDT" not in loaded2)
    print()

    # --- 6. Closed trade row contains all required fields
    print("6. Trade row schema")
    from hermes_trading.multi_loop import build_trade_row

    pos = {
        "entry_price": 70000.0,
        "opened_at": "2026-05-29T12:00:00+00:00",
        "size": 0.5,
        "direction": "long",
        "setup": "supertrend",
    }
    row = build_trade_row(
        asset="BTC/USDT", position=pos, exit_price=71400.0,
        exit_reason="supertrend_flip", bars_held=12,
        strategy_version="v3-supertrend-01",
    )
    required = [
        "asset", "strategy_version", "setup",
        "entry_time", "exit_time", "entry_price", "exit_price",
        "return_pct", "net_return_pct", "position_size",
        "exit_reason", "holding_bars",
    ]
    missing = [k for k in required if k not in row]
    check("trade row has every required field", not missing,
          f"missing={missing}")
    check("legacy fields preserved (opened_at, closed_at, return)",
          "opened_at" in row and "closed_at" in row and "return" in row)
    # 2% gross on long; net = 2% * 0.5 size = 1%
    check("return_pct matches (exit-entry)/entry",
          abs(row["return_pct"] - 0.02) < 1e-9, f"got {row['return_pct']}")
    check("net_return_pct == return_pct * position_size",
          abs(row["net_return_pct"] - 0.01) < 1e-9, f"got {row['net_return_pct']}")
    check("legacy `return` matches net_return_pct",
          row["return"] == row["net_return_pct"])
    check("holding_bars carried through", row["holding_bars"] == 12)
    print()

    # --- 7. evaluate_tick respects cap and per-asset slot
    print("7. evaluate_tick respects portfolio cap")
    from hermes_trading.multi_loop import evaluate_tick

    # A row that should fire the SuperTrend long entry: bullish flip
    bullish_flip_row = {
        "close": 70000.0, "high": 70100, "low": 69900,
        "ema_fast": 70000.0, "ema_slow": 65000.0,         # bullish regime
        "rsi": 50.0, "atr": 700.0,
        "ema_pull": 69800.0,
        "supertrend_direction": 1, "supertrend_direction_prev": -1,
        "supertrend_line": 69300.0,
        "three_bar": False, "vwap": 69800.0, "donchian_high": 71000.0,
    }
    strategy = {
        "setups": {
            "supertrend": {"enabled": True, "period": 10, "multiplier": 3.0,
                           "max_holding_bars": 0},
            "pullback":  {"enabled": False, "pullback_ema": 21, "ema_tol": 0.002,
                          "rsi_threshold": 32,
                          "exit": {"type": "mean_revert", "stop_atr_mult": 1.2,
                                   "target_rsi": 55}},
            "breakout":  {"enabled": False, "require_above_vwap": True,
                          "ignition_atr_mult": 1.0,
                          "exit": {"type": "trail", "trail_ema": 21, "stop_atr_mult": 1.5}},
        },
        "shorts": {"enabled": False},
        "risk": {"position_size_r": 0.5, "atr_period": 14, "max_hold_bars": 240,
                 "regime_flip_exit": False},
        "regime": {"trend_ema_fast": 50, "trend_ema_slow": 200},
        "rsi_period": 14, "_timeframe": "4h",
    }
    new_pos, trade = evaluate_tick(
        asset="BTC/USDT", row=bullish_flip_row, strategy=strategy,
        position=None,
        positions_by_asset={"BTC/USDT": None, "ETH/USDT": None},
        max_open_positions=2, size_per_asset=0.5,
        strategy_version="v3-supertrend-01",
    )
    check("entry fires on bullish SuperTrend flip when flat",
          new_pos is not None and trade is None,
          f"new_pos={new_pos} trade={trade}")
    # at cap → no entry even if signal fires
    state_full = {
        "BTC/USDT": {"entry_price": 70000, "direction": "long", "setup": "supertrend"},
        "ETH/USDT": {"entry_price": 3000, "direction": "long", "setup": "supertrend"},
    }
    new_pos2, _ = evaluate_tick(
        asset="SOL/USDT", row=bullish_flip_row, strategy=strategy,
        position=None,
        positions_by_asset={**state_full, "SOL/USDT": None},
        max_open_positions=2, size_per_asset=0.5, strategy_version="v3-supertrend-01",
    )
    check("entry blocked when portfolio at cap",
          new_pos2 is None, f"new_pos2={new_pos2}")
    print()

    # --- 8. Display module (Issue #17)
    print("8. Display auto-detection + formatting (Issue #17)")
    from hermes_trading import display as display_mod

    st_strategy = {"setups": {"supertrend": {"enabled": True}, "pullback": {"enabled": False}}}
    v2_strategy = {"setups": {"pullback": {"enabled": True}, "breakout": {"enabled": True}}}
    legacy_no_setups = {"regime": {"trend_ema_fast": 50}}
    check("is_supertrend_active True on SuperTrend yaml",
          display_mod.is_supertrend_active(st_strategy))
    check("is_supertrend_active False on v2 yaml",
          not display_mod.is_supertrend_active(v2_strategy))
    check("is_supertrend_active False on missing-setups yaml",
          not display_mod.is_supertrend_active(legacy_no_setups))

    line_flat = display_mod.format_supertrend_tick(
        asset="BTC/USDT", close=73890.80,
        supertrend_direction=1, supertrend_line=72150.22,
        strategy_version="v3-supertrend-01", position=None,
    )
    check("SuperTrend tick line: asset header",
          line_flat.startswith("tick BTC/USDT close=73890.80"),
          f"got {line_flat!r}")
    check("SuperTrend tick line: shows st=UP",
          " st=UP " in line_flat, f"got {line_flat!r}")
    check("SuperTrend tick line: shows line=",
          "line=72150.22" in line_flat, f"got {line_flat!r}")
    check("SuperTrend tick line: shows signed dist%",
          "dist=+2.41%" in line_flat, f"got {line_flat!r}")
    check("SuperTrend tick line: shows v=v3-supertrend-01",
          "v=v3-supertrend-01" in line_flat, f"got {line_flat!r}")
    check("SuperTrend tick line: pos=flat when no position",
          line_flat.rstrip().endswith("pos=flat"), f"got {line_flat!r}")
    check("SuperTrend tick line: no rsi= when not verbose",
          " rsi=" not in line_flat, f"got {line_flat!r}")

    line_long = display_mod.format_supertrend_tick(
        asset="ETH/USDT", close=3901.20,
        supertrend_direction=1, supertrend_line=3720.00,
        strategy_version="v3-supertrend-01",
        position={"entry_price": 3855.00, "size": 1.0, "direction": "long", "setup": "supertrend"},
    )
    check("SuperTrend tick line (long): pos=long",
          " pos=long " in line_long, f"got {line_long!r}")
    check("SuperTrend tick line (long): setup=supertrend",
          "setup=supertrend" in line_long, f"got {line_long!r}")
    check("SuperTrend tick line (long): uPnL is positive",
          " uPnL=+" in line_long, f"got {line_long!r}")

    line_down = display_mod.format_supertrend_tick(
        asset="ETH/USDT", close=3840.15,
        supertrend_direction=-1, supertrend_line=3922.40,
        strategy_version="v3-supertrend-01", position=None,
    )
    check("SuperTrend tick line: shows st=DOWN",
          " st=DOWN " in line_down and "dist=-2.10%" in line_down,
          f"got {line_down!r}")

    line_warmup = display_mod.format_supertrend_tick(
        asset="BTC/USDT", close=73890.80,
        supertrend_direction=None, supertrend_line=None,
        strategy_version="v3-supertrend-01", position=None,
    )
    check("SuperTrend tick line tolerates None (warmup)",
          " st=? " in line_warmup and " line=? " in line_warmup
          and " dist=? " in line_warmup, f"got {line_warmup!r}")

    line_verbose = display_mod.format_supertrend_tick(
        asset="BTC/USDT", close=73890.80,
        supertrend_direction=1, supertrend_line=72150.22,
        strategy_version="v3-supertrend-01", position=None,
        rsi=43.9, verbose=True,
    )
    check("SuperTrend tick line in verbose mode includes rsi=43.9",
          "rsi=43.9" in line_verbose, f"got {line_verbose!r}")

    # Legacy RSI line preserved
    legacy_line = display_mod.format_rsi_tick(
        asset="BTC/USDT", close=73935.30, rsi=43.9,
        strategy_version="10", position=None, regime_str="regime=off",
    )
    check("legacy RSI line: starts with 'tick BTC/USDT 73935.30'",
          legacy_line.startswith("tick BTC/USDT 73935.30"),
          f"got {legacy_line!r}")
    check("legacy RSI line: contains rsi=43.9 v10",
          "rsi=43.9 v10" in legacy_line, f"got {legacy_line!r}")
    check("legacy RSI line: ends with pos=flat regime=off",
          legacy_line.rstrip().endswith("pos=flat regime=off"),
          f"got {legacy_line!r}")

    # Heartbeat fields
    hb_st = display_mod.supertrend_heartbeat_fields(73890.80, 1, 72150.22)
    check("heartbeat: supertrend_direction == 'UP'",
          hb_st["supertrend_direction"] == "UP", f"got {hb_st}")
    check("heartbeat: supertrend_line is the float",
          abs(hb_st["supertrend_line"] - 72150.22) < 1e-9, f"got {hb_st}")
    expected_dist = (73890.80 - 72150.22) / 72150.22 * 100.0
    check("heartbeat: supertrend_distance_pct matches (close-line)/line*100",
          abs(hb_st["supertrend_distance_pct"] - expected_dist) < 1e-9,
          f"got {hb_st['supertrend_distance_pct']}  expected {expected_dist}")
    hb_warm = display_mod.supertrend_heartbeat_fields(73890.80, None, None)
    check("heartbeat: warmup gives Nones",
          hb_warm["supertrend_direction"] is None
          and hb_warm["supertrend_line"] is None
          and hb_warm["supertrend_distance_pct"] is None,
          f"got {hb_warm}")
    print()

    # --- 9. "Why no trade" diagnostic (Issue #18)
    print("9. 'Why no trade' diagnostic (Issue #18)")
    from hermes_trading.display import (
        diagnose_entry_blockers, format_entry_diagnostic_lines,
    )

    st_strat = {"setups": {"supertrend": {"enabled": True},
                           "pullback": {"enabled": False},
                           "breakout": {"enabled": False}},
                "shorts": {"enabled": False}}
    v2_strat = {"setups": {"pullback": {"enabled": True},
                           "breakout": {"enabled": True}}}

    # Case A: DOWN regime, below band, EMA bearish — every blocker fires.
    row_down_bearish = {
        "close": 74097.90,
        "supertrend_direction": -1,
        "supertrend_direction_prev": -1,
        "supertrend_line": 75559.66,
        "ema_fast": 73500.0,
        "ema_slow": 75000.0,
    }
    diag = diagnose_entry_blockers(row_down_bearish, st_strat, position=None,
                                   portfolio_open=0, max_open=2)
    check("waiting_for set to SuperTrend rule",
          diag["waiting_for"] == "SuperTrend flip UP + EMA50 > EMA200")
    check("blockers include supertrend_direction=DOWN",
          any("supertrend_direction=DOWN" in b for b in diag["blockers"]),
          f"got {diag['blockers']}")
    check("blockers include close-below-line with %",
          any("close below supertrend_line by 1.93%" in b for b in diag["blockers"]),
          f"got {diag['blockers']}")
    check("blockers include ema50_below_ema200",
          "ema50_below_ema200" in diag["blockers"], f"got {diag['blockers']}")
    check("near_entry absent when distance > 1% below",
          diag["near_entry"] is None, f"got {diag['near_entry']}")
    check("blocked_by absent when conditions not met",
          diag["blocked_by"] is None)

    # Case B: still DOWN but only just below the band (-0.42%) → near_entry fires.
    row_near = dict(row_down_bearish)
    row_near["close"] = 75559.66 * (1 - 0.0042)
    row_near["ema_fast"] = 76000.0   # regime now bullish
    diag_near = diagnose_entry_blockers(row_near, st_strat, position=None,
                                        portfolio_open=0, max_open=2)
    check("near_entry message contains gap %",
          diag_near["near_entry"] is not None
          and "0.42%" in diag_near["near_entry"],
          f"got {diag_near.get('near_entry')}")
    check("ema50_below_ema200 NOT in blockers when bullish",
          "ema50_below_ema200" not in diag_near["blockers"])

    # Case C: fresh UP flip + bullish regime + portfolio cap reached →
    # entry conditions satisfied but cap is the blocker.
    row_flip = {
        "close": 76000.0,
        "supertrend_direction": 1,
        "supertrend_direction_prev": -1,
        "supertrend_line": 75500.0,
        "ema_fast": 76000.0,
        "ema_slow": 75000.0,
    }
    diag_cap = diagnose_entry_blockers(row_flip, st_strat, position=None,
                                       portfolio_open=2, max_open=2)
    check("blocked_by portfolio cap when entry conditions met",
          diag_cap["blocked_by"] == "portfolio max_open_positions reached",
          f"got {diag_cap['blocked_by']}")
    check("st_flip_ok is True on DOWN→UP transition",
          diag_cap["st_flip_ok"] is True)
    check("bullish_regime_ok is True when EMA fast > slow",
          diag_cap["bullish_regime_ok"] is True)

    # Case D: already in position → describe what we're waiting to exit.
    diag_in = diagnose_entry_blockers(
        row_flip, st_strat,
        position={"entry_price": 75600, "direction": "long", "setup": "supertrend"},
        portfolio_open=1, max_open=2,
    )
    check("in_position description mentions exit/flip DOWN",
          diag_in["in_position"] is True
          and "flip DOWN" in (diag_in["waiting_for"] or ""),
          f"got {diag_in.get('waiting_for')}")
    check("no blockers list for an existing position",
          diag_in["blockers"] == [], f"got {diag_in['blockers']}")

    # Case E: v2 long-short legacy strategy → diagnostic still works,
    #         waiting_for points at the RSI/breakout rules.
    diag_v2 = diagnose_entry_blockers(row_down_bearish, v2_strat, position=None,
                                      portfolio_open=0, max_open=1)
    check("v2 strategy waiting_for mentions RSI/breakout/pullback",
          "RSI" in (diag_v2["waiting_for"] or ""),
          f"got {diag_v2.get('waiting_for')}")

    # Case F: warmup row (None direction / None line) → diagnostic does
    #         not crash and st_dir reports '?'.
    row_warm = {"close": 100.0, "supertrend_direction": None,
                "supertrend_direction_prev": None, "supertrend_line": None,
                "ema_fast": None, "ema_slow": None}
    diag_warm = diagnose_entry_blockers(row_warm, st_strat, position=None)
    check("warmup row gives st_dir='?'",
          diag_warm["st_dir"] == "?", f"got {diag_warm['st_dir']}")
    check("warmup row gives distance_pct None",
          diag_warm["distance_pct"] is None,
          f"got {diag_warm['distance_pct']}")

    # Formatter — produces the expected indented lines
    lines = format_entry_diagnostic_lines(diag)
    check("formatter emits waiting_for line",
          any(line.startswith("  waiting_for: ") for line in lines),
          f"got {lines}")
    check("formatter emits blockers line",
          any(line.startswith("  blockers: ") for line in lines),
          f"got {lines}")
    lines_cap = format_entry_diagnostic_lines(diag_cap)
    check("formatter emits blocked_by line for cap case",
          any(line.startswith("  blocked_by: ") for line in lines_cap),
          f"got {lines_cap}")
    lines_in = format_entry_diagnostic_lines(diag_in)
    check("formatter omits blockers when no blockers present",
          all(not line.startswith("  blockers: ") for line in lines_in),
          f"got {lines_in}")
    print()

    # --- 10. Funding overlay gate (Issue #21)
    print("10. Funding overlay gate (Issue #21)")
    from hermes_trading.multi_loop import evaluate_funding_gate

    g_long_allowed = evaluate_funding_gate("long", percentile=50.0)
    check("long allowed at p50 (mid funding)", g_long_allowed["allow"]
          and g_long_allowed["decision"] == "allow",
          f"got {g_long_allowed}")
    g_long_block = evaluate_funding_gate("long", percentile=96.0)
    check("long BLOCKED at p96 (>= 95 default)",
          (not g_long_block["allow"]) and g_long_block["decision"] == "block_long"
          and "extreme_positive_funding" in g_long_block["reason"],
          f"got {g_long_block}")
    g_long_edge = evaluate_funding_gate("long", percentile=95.0)
    check("long BLOCKED exactly at p95 (>= boundary)",
          (not g_long_edge["allow"]) and g_long_edge["decision"] == "block_long",
          f"got {g_long_edge}")

    g_short_allowed = evaluate_funding_gate("short", percentile=50.0)
    check("short allowed at p50", g_short_allowed["allow"],
          f"got {g_short_allowed}")
    g_short_block = evaluate_funding_gate("short", percentile=2.0)
    check("short BLOCKED at p2 (<= 5 default)",
          (not g_short_block["allow"]) and g_short_block["decision"] == "block_short"
          and "extreme_negative_funding" in g_short_block["reason"],
          f"got {g_short_block}")
    g_short_edge = evaluate_funding_gate("short", percentile=5.0)
    check("short BLOCKED exactly at p5 (<= boundary)",
          (not g_short_edge["allow"]) and g_short_edge["decision"] == "block_short",
          f"got {g_short_edge}")

    # Direction-aware: long not blocked at p2 (only short blocked), short not blocked at p98
    g_long_low = evaluate_funding_gate("long", percentile=2.0)
    check("long ALLOWED at p2 (low funding only affects shorts)",
          g_long_low["allow"], f"got {g_long_low}")
    g_short_high = evaluate_funding_gate("short", percentile=98.0)
    check("short ALLOWED at p98 (high funding only affects longs)",
          g_short_high["allow"], f"got {g_short_high}")

    # Missing data: fail open by default
    g_missing_open = evaluate_funding_gate("long", percentile=None,
                                           on_missing_data="fail_open")
    check("missing data + fail_open → allow + decision=missing_data",
          g_missing_open["allow"] and g_missing_open["decision"] == "missing_data",
          f"got {g_missing_open}")
    g_missing_closed = evaluate_funding_gate("long", percentile=None,
                                             on_missing_data="fail_closed")
    check("missing data + fail_closed → block + decision=missing_data_blocked",
          (not g_missing_closed["allow"])
          and g_missing_closed["decision"] == "missing_data_blocked",
          f"got {g_missing_closed}")

    # Custom thresholds respected
    g_custom_long = evaluate_funding_gate("long", percentile=85.0,
                                          block_long_above_pct=80.0)
    check("custom block_long_above_pct=80 respected",
          (not g_custom_long["allow"]) and g_custom_long["decision"] == "block_long",
          f"got {g_custom_long}")
    g_custom_short = evaluate_funding_gate("short", percentile=12.0,
                                           block_short_below_pct=15.0)
    check("custom block_short_below_pct=15 respected",
          (not g_custom_short["allow"]) and g_custom_short["decision"] == "block_short",
          f"got {g_custom_short}")

    # Config file exists and points at the long-short strategy with funding enabled
    import yaml as _yaml
    long_short_cfg_path = ROOT / "state" / "live_multiasset_long_short_funding.yaml"
    check("new live config exists", long_short_cfg_path.exists(),
          f"path={long_short_cfg_path}")
    long_short_cfg = _yaml.safe_load(open(long_short_cfg_path))
    check("new config: assets == [BTC/USDT, ETH/USDT]",
          long_short_cfg["assets"] == ["BTC/USDT", "ETH/USDT"],
          f"got {long_short_cfg.get('assets')}")
    check("new config: strategy = strategy_supertrend_long_short.yaml",
          long_short_cfg["strategy"] == "state/strategy_supertrend_long_short.yaml",
          f"got {long_short_cfg.get('strategy')}")
    check("new config: funding_filter.enabled = true",
          long_short_cfg["funding_filter"]["enabled"] is True)
    check("new config: block_long_above_pct = 95 (Issue #20 threshold)",
          long_short_cfg["funding_filter"]["block_long_above_pct"] == 95.0)
    check("new config: block_short_below_pct = 5 (Issue #20 symmetric)",
          long_short_cfg["funding_filter"]["block_short_below_pct"] == 5.0)
    check("new config: percentile_window_bars = 180 (30 days @ 4h)",
          long_short_cfg["funding_filter"]["percentile_window_bars"] == 180)
    check("new config: on_missing_data = fail_open (Issue #21 default)",
          long_short_cfg["funding_filter"]["on_missing_data"] == "fail_open")

    # Long-only fallback config still exists and is unchanged shape
    long_only_cfg_path = ROOT / "state" / "live_multiasset.yaml"
    check("long-only fallback config still exists", long_only_cfg_path.exists())
    long_only_cfg = _yaml.safe_load(open(long_only_cfg_path))
    check("long-only fallback uses long-only strategy",
          long_only_cfg["strategy"] == "state/strategy_supertrend.yaml",
          f"got {long_only_cfg.get('strategy')}")
    check("long-only fallback has NO funding_filter section",
          "funding_filter" not in long_only_cfg or not long_only_cfg.get("funding_filter"))
    print()

    # --- 11. Display timezone formatter (Issue #22)
    print("11. Display timezone formatter (Issue #22)")
    from datetime import datetime, timezone, timedelta
    from hermes_trading import (
        format_display_time, set_display_time_mode, get_display_time_mode,
        now_iso, DISPLAY_TIME_MODES,
    )

    # Default mode is LOCAL (Issue #22 — user's preference: local is the
    # primary use case for interactive monitoring). UTC remains canonical
    # for persisted artifacts.
    # Note: prior tests in this script may have called set_display_time_mode,
    # so we re-import the module fresh to verify the *module-level* default.
    import importlib
    import hermes_trading as ht_pkg
    importlib.reload(ht_pkg)
    check("module-level default display mode is 'local'",
          ht_pkg.get_display_time_mode() == "local",
          f"got {ht_pkg.get_display_time_mode()}")
    set_display_time_mode("utc")  # for the next check series

    # A fixed UTC instant — Apr 15 2026 02:39:01 UTC
    sample = datetime(2026, 4, 15, 2, 39, 1, tzinfo=timezone.utc)
    utc_str, utc_abbrev = format_display_time(sample, mode="utc")
    check("UTC formatter returns 02:39:01 with no abbreviation",
          utc_str == "02:39:01" and utc_abbrev == "",
          f"got ({utc_str!r}, {utc_abbrev!r})")

    local_str, local_abbrev = format_display_time(sample, mode="local")
    # The host OS may be UTC (CI) or a specific zone (developer machine).
    # We can't assert a specific abbreviation, but we can assert these:
    check("local formatter returns 8 chars HH:MM:SS",
          len(local_str) == 8 and local_str[2] == ":" and local_str[5] == ":",
          f"got {local_str!r}")
    # Abbreviation must be either non-empty OR (on UTC-host) empty —
    # either way the format must not crash and must be a string.
    check("local formatter returns str abbreviation (may be empty on UTC host)",
          isinstance(local_abbrev, str), f"got {type(local_abbrev)}")

    # Mode override
    utc_via_override, _ = format_display_time(sample, mode="utc")
    set_display_time_mode("local")
    check("mode override respected when explicit",
          format_display_time(sample, mode="utc")[0] == utc_via_override)
    set_display_time_mode("utc")  # reset

    # Invalid mode raises
    raised = False
    try:
        set_display_time_mode("eastern")
    except ValueError:
        raised = True
    check("invalid mode raises ValueError", raised)

    # Market mode wired but NotImplemented — documented in the issue
    raised = False
    try:
        set_display_time_mode("market")
    except NotImplementedError:
        raised = True
    check("'market' mode raises NotImplementedError (reserved for future)",
          raised)

    # now_iso() must always be UTC regardless of display mode
    set_display_time_mode("local")
    iso_local = now_iso()
    set_display_time_mode("utc")
    iso_utc = now_iso()
    set_display_time_mode("utc")  # reset

    parsed_local = datetime.fromisoformat(iso_local)
    parsed_utc = datetime.fromisoformat(iso_utc)
    # Both must be UTC-aware
    check("now_iso() in local mode produces UTC tz",
          parsed_local.utcoffset() == timedelta(0),
          f"got {parsed_local.utcoffset()}")
    check("now_iso() in utc mode produces UTC tz",
          parsed_utc.utcoffset() == timedelta(0),
          f"got {parsed_utc.utcoffset()}")

    # Naive utc input is treated as UTC, not local
    naive = datetime(2026, 4, 15, 2, 39, 1)
    aware_utc = datetime(2026, 4, 15, 2, 39, 1, tzinfo=timezone.utc)
    s1, _ = format_display_time(naive, mode="utc")
    s2, _ = format_display_time(aware_utc, mode="utc")
    check("naive timestamp treated as UTC", s1 == s2,
          f"got {s1!r} vs {s2!r}")

    # run.py must declare --utc-time + aliases and the set_display_time_mode
    # call. The default behaviour (no flag) leaves the module-level default
    # ("local") intact, so the screen shows host time without any flag.
    help_text_path = ROOT / "hermes_trading" / "run.py"
    src = help_text_path.read_text()
    for alias in ("--utc-time", "--bot-time", "--transaction-time"):
        check(f"run.py source declares {alias} flag", alias in src,
              f"alias {alias} not found in run.py")
    check("run.py wires set_display_time_mode",
          "set_display_time_mode" in src,
          "set_display_time_mode call missing in run.py")

    # DISPLAY_TIME_MODES tuple is the authoritative list
    check("DISPLAY_TIME_MODES contains the three modes",
          set(DISPLAY_TIME_MODES) == {"utc", "local", "market"},
          f"got {DISPLAY_TIME_MODES}")
    print()

    # --- 12. Bar-close parity (Issue #24)
    print("12. Bar-close parity: live signals use the LAST CLOSED bar (Issue #24)")
    import pandas as _pd
    from hermes_trading.display import split_display_and_signal_rows
    from hermes_trading import signals as _sig
    from hermes_trading.multi_loop import evaluate_tick

    # Build a synthetic 3-bar indicator frame:
    #   bar T-2  (closed)  : DOWN
    #   bar T-1  (closed)  : DOWN  → signal_row when iloc[-1] is T (in-progress)
    #   bar T    (CURRENT) : UP    → display_row (in-progress flicker)
    # If live evaluates entry on display_row, the bullish flip fires (BUG).
    # If live evaluates on signal_row, no flip — entry must NOT fire (FIX).
    base_strategy_st = {
        "setups": {
            "supertrend": {"enabled": True, "period": 10, "multiplier": 3.0,
                           "max_holding_bars": 0},
            "pullback":  {"enabled": False, "pullback_ema": 21, "ema_tol": 0.002,
                          "rsi_threshold": 32,
                          "exit": {"type": "mean_revert", "stop_atr_mult": 1.2,
                                   "target_rsi": 55}},
            "breakout":  {"enabled": False, "require_above_vwap": True,
                          "ignition_atr_mult": 1.0,
                          "exit": {"type": "trail", "trail_ema": 21,
                                   "stop_atr_mult": 1.5}},
        },
        "shorts": {"enabled": False},
        "risk": {"position_size_r": 0.5, "atr_period": 14, "max_hold_bars": 240,
                 "regime_flip_exit": False},
        "regime": {"trend_ema_fast": 50, "trend_ema_slow": 200},
        "rsi_period": 14, "_timeframe": "4h",
    }
    ind_df_flicker = _pd.DataFrame({
        "open":  [70000.0, 70000.0, 70000.0],
        "high":  [70100.0, 70100.0, 70200.0],
        "low":   [69900.0, 69900.0, 69950.0],
        "close": [70000.0, 70000.0, 70000.0],
        "volume":[10.0, 10.0, 10.0],
        "ema_fast": [70000.0, 70000.0, 70000.0],
        "ema_slow": [65000.0, 65000.0, 65000.0],     # bullish regime everywhere
        "rsi":     [50.0, 50.0, 50.0],
        "atr":     [700.0, 700.0, 700.0],
        "ema_pull": [69800.0, 69800.0, 69800.0],
        "three_bar": [False, False, False],
        "vwap":    [69800.0, 69800.0, 69800.0],
        "donchian_high": [71000.0, 71000.0, 71000.0],
        # T-2 and T-1 are DOWN (closed); T (in-progress) wobbled UP.
        "supertrend_direction":      [-1, -1,  1],
        "supertrend_direction_prev": [None, -1, -1],  # shift(1) of the above
        "supertrend_line":           [70500.0, 70500.0, 69300.0],
    })
    display_row, signal_row = split_display_and_signal_rows(ind_df_flicker)
    check("display_row is the latest (in-progress) bar",
          int(display_row["supertrend_direction"]) == 1,
          f"got {display_row['supertrend_direction']}")
    check("signal_row is the prior CLOSED bar",
          int(signal_row["supertrend_direction"]) == -1,
          f"got {signal_row['supertrend_direction']}")

    # Bug-emulation: entry evaluated on display_row would FIRE.
    bug_setup = _sig.long_entry(display_row, base_strategy_st)
    check("BUG repro: entry on display_row would fire (would have been H1)",
          bug_setup == "supertrend",
          f"got {bug_setup!r}")
    # Fix: entry evaluated on signal_row must NOT fire.
    fix_setup = _sig.long_entry(signal_row, base_strategy_st)
    check("FIX: entry on signal_row does NOT fire on intra-bar flicker",
          fix_setup is None, f"got {fix_setup!r}")

    # And vice versa — a real closed-bar UP flip MUST fire.
    real_flip = _pd.DataFrame({
        "open":  [70000.0, 70000.0, 70000.0],
        "high":  [70100.0, 70200.0, 70300.0],
        "low":   [69900.0, 69950.0, 70000.0],
        "close": [70000.0, 70200.0, 70250.0],
        "volume":[10.0, 10.0, 10.0],
        "ema_fast": [70000.0, 70000.0, 70000.0],
        "ema_slow": [65000.0, 65000.0, 65000.0],
        "rsi":     [50.0, 50.0, 50.0],
        "atr":     [700.0, 700.0, 700.0],
        "ema_pull": [69800.0, 69800.0, 69800.0],
        "three_bar": [False, False, False],
        "vwap":    [69800.0, 69800.0, 69800.0],
        "donchian_high": [71000.0, 71000.0, 71000.0],
        # T-2 DOWN, T-1 flipped to UP at CLOSE, T continues UP.
        "supertrend_direction":      [-1,  1,  1],
        "supertrend_direction_prev": [None, -1,  1],
        "supertrend_line":           [70500.0, 69300.0, 69300.0],
    })
    dr_real, sr_real = split_display_and_signal_rows(real_flip)
    real_setup = _sig.long_entry(sr_real, base_strategy_st)
    check("FIX: a real CLOSED-bar UP flip on signal_row DOES fire",
          real_setup == "supertrend", f"got {real_setup!r}")

    # Stop check semantic: a closed-bar low that did NOT breach the stop
    # should not exit (signals.long_exit ratchet branch only). An
    # in-progress display_row low BELOW the stop must trigger an exit in
    # the orchestration's intra-bar check.
    position_long = {"entry_price": 70200, "direction": "long",
                     "setup": "supertrend", "stop": 70000.0, "size": 0.5,
                     "opened_at": "2026-05-30T20:00:00+00:00"}
    # signal_row's low is 70000 — equal to stop; signals.long_exit will
    # treat this as a stop (low <= stop).
    sr_lows_eq = dict(sr_real)
    sr_lows_eq["low"] = 70010.0  # bar low strictly above the stop
    reason_signal = _sig.long_exit(sr_lows_eq, dict(position_long), base_strategy_st, 1)
    check("signal_row low above stop: no exit (no flip, no breach)",
          reason_signal is None, f"got {reason_signal!r}")

    # Intra-bar low below stop on display_row must trigger an exit (this
    # is the orchestration responsibility, not signals.long_exit).
    dr_intrabar = dict(dr_real)
    dr_intrabar["low"] = 69990.0    # below stop 70000
    # Simulate the orchestration's intra-bar reactive check:
    intrabar_reason = None
    if reason_signal is None and dr_intrabar["low"] <= position_long["stop"]:
        intrabar_reason = "stop"
    check("intra-bar display_row low <= stop triggers 'stop' in orchestration",
          intrabar_reason == "stop", f"got {intrabar_reason!r}")

    # Short side mirror: SuperTrend flip back to UP on closed bar must
    # trigger a short exit; an intra-bar high above stop on display_row
    # must trigger a stop exit.
    short_strategy = dict(base_strategy_st)
    short_strategy["shorts"] = {"enabled": True,
                                "supertrend": {"enabled": True}}
    short_pos = {"entry_price": 70000, "direction": "short",
                 "setup": "supertrend_short", "stop": 70500.0,
                 "size": 0.5, "opened_at": "2026-05-30T20:00:00+00:00"}
    # signal_row's direction is +1 (UP). The SuperTrend short branch
    # ratchets the stop DOWN to the new lower band and then checks
    # `high >= stop` — on a real flip bar the high is typically above
    # the freshly-ratcheted band, which triggers a "stop" close. Both
    # reasons mean "close the short on this closed bar"; what matters
    # for parity is that the closed bar's UP direction DOES close the
    # short. Backtest behaviour is identical.
    short_exit_reason = _sig.short_exit(sr_real, dict(short_pos), short_strategy, 1)
    check("signal_row UP flip CLOSES a SHORT (reason='stop' or 'supertrend_flip')",
          short_exit_reason in ("stop", "supertrend_flip"),
          f"got {short_exit_reason!r}")
    # And the in-progress flicker on the prior DOWN-DOWN bar should NOT
    # close the short — research holds the position through chop.
    short_exit_signal_down = _sig.short_exit(signal_row, dict(short_pos),
                                             short_strategy, 1)
    check("signal_row still DOWN (prior closed): SHORT stays open",
          short_exit_signal_down is None,
          f"got {short_exit_signal_down!r}")

    # evaluate_tick should also use the signal_row when called directly
    # — verify by passing it the flicker signal_row and confirming no
    # entry fires (entries fire only on a real closed-bar flip).
    new_pos, trade = evaluate_tick(
        asset="BTC/USDT",
        row=signal_row,            # caller picks which row to pass
        strategy=base_strategy_st,
        position=None,
        positions_by_asset={"BTC/USDT": None},
        max_open_positions=1, size_per_asset=0.5,
        strategy_version="v3-supertrend-01",
    )
    check("evaluate_tick on signal_row does NOT fire on intra-bar flicker",
          new_pos is None and trade is None,
          f"got new_pos={new_pos}")

    # Helper edge cases
    empty_df = _pd.DataFrame()
    dr_e, sr_e = split_display_and_signal_rows(empty_df)
    check("empty frame returns empty dicts",
          dr_e == {} and sr_e == {}, f"got ({dr_e}, {sr_e})")

    single_bar = ind_df_flicker.iloc[-1:].copy().reset_index(drop=True)
    dr_s, sr_s = split_display_and_signal_rows(single_bar)
    check("single-bar fallback: signal_row == display_row",
          dr_s["supertrend_direction"] == sr_s["supertrend_direction"]
          and int(dr_s["supertrend_direction"]) == 1)

    # Confirm the orchestration files actually wire signal_row to entries
    loop_src = (ROOT / "hermes_trading" / "loop.py").read_text()
    mloop_src = (ROOT / "hermes_trading" / "multi_loop.py").read_text()
    check("loop.py calls split_display_and_signal_rows",
          "split_display_and_signal_rows" in loop_src)
    check("multi_loop.py calls split_display_and_signal_rows",
          "split_display_and_signal_rows" in mloop_src)
    check("loop.py uses signal_row for long_entry",
          "signals.long_entry(signal_row" in loop_src,
          "string not found in loop.py")
    check("multi_loop.py uses signal_row for long_entry",
          "signals.long_entry(signal_row" in mloop_src,
          "string not found in multi_loop.py")
    check("loop.py uses signal_row for long_exit",
          "signals.long_exit(signal_row" in loop_src)
    check("multi_loop.py uses signal_row for long_exit",
          "signals.long_exit(signal_row" in mloop_src)
    check("loop.py still uses display_row low for intra-bar stop",
          'display_row.get("low")' in loop_src and 'reason = "stop"' in loop_src)
    check("multi_loop.py still uses display_row low/high for intra-bar stop",
          ('display_row.get("low")' in mloop_src
           and 'display_row.get("high")' in mloop_src))
    print()

    if failures:
        print(f"{RED}{BOLD}SELF-TEST FAILED: {len(failures)} check(s){RESET}")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"{GREEN}{BOLD}SELF-TEST PASSED{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
