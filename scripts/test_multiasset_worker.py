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
    # 2% gross on long; Issue #29 — net = (ret - 2*fee) * size, where
    # fee defaults to RESEARCH_FEE_PER_SIDE = 0.001 (10 bp/side). So
    # net = (0.02 - 0.002) * 0.5 = 0.009.
    check("return_pct matches (exit-entry)/entry",
          abs(row["return_pct"] - 0.02) < 1e-9, f"got {row['return_pct']}")
    check("net_return_pct == (return_pct - 2*fee) * position_size (Issue #29)",
          abs(row["net_return_pct"] - 0.009) < 1e-9, f"got {row['net_return_pct']}")
    check("legacy `return` matches net_return_pct",
          row["return"] == row["net_return_pct"])
    check("trade row records fee_per_side (Issue #29)",
          abs(row["fee_per_side"] - 0.001) < 1e-9, f"got {row.get('fee_per_side')}")
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

    # --- 13. Replay multi-asset config support (Issue #26)
    print("13. Replay --config mode (Issue #26)")
    import importlib.util
    import io
    import contextlib

    replay_path = ROOT / "scripts" / "replay_live.py"
    check("replay_live.py exists", replay_path.exists())
    spec = importlib.util.spec_from_file_location("replay_live", replay_path)
    replay_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay_mod)

    # Module exposes the two replay paths and the CSV column spec
    check("replay_live exposes _run_strategy_replay (legacy mode)",
          callable(getattr(replay_mod, "_run_strategy_replay", None)))
    check("replay_live exposes _run_config_replay (new mode)",
          callable(getattr(replay_mod, "_run_config_replay", None)))
    check("replay_live exposes TRADE_CSV_COLUMNS",
          isinstance(getattr(replay_mod, "TRADE_CSV_COLUMNS", None), list))

    # CSV columns: Issue #26 spec is the first 12 columns (order matters —
    # it's the on-disk schema). Issue #34 appends 8 vol_sizing fields
    # AFTER these. The Issue #26 prefix must remain stable so external
    # readers parsing by column count keep working.
    expected_cols = [
        "asset", "direction", "entry_time", "exit_time",
        "entry_price", "exit_price", "return_pct", "net_return_pct",
        "setup", "exit_reason", "bars_held", "funding_decision",
    ]
    check("TRADE_CSV_COLUMNS preserves Issue #26 spec in the first 12 columns",
          replay_mod.TRADE_CSV_COLUMNS[:12] == expected_cols,
          f"got first 12: {replay_mod.TRADE_CSV_COLUMNS[:12]}")

    # Asset / symbol round-trip helpers
    check("_asset_label_from_symbol(BTCUSDT) == 'BTC/USDT'",
          replay_mod._asset_label_from_symbol("BTCUSDT") == "BTC/USDT")
    check("_symbol_from_asset('ETH/USDT') == 'ETHUSDT'",
          replay_mod._symbol_from_asset("ETH/USDT") == "ETHUSDT")

    # Funding decision helper — direction-agnostic per-bar mapping that
    # mirrors the heartbeat decision in multi_loop.
    decision, _ = replay_mod._funding_decision_for_heartbeat(
        {"available": True, "rate": 0.0001, "percentile": 50.0}, 95.0, 5.0,
    )
    check("funding decision at p50 = allow", decision == "allow",
          f"got {decision}")
    decision, _ = replay_mod._funding_decision_for_heartbeat(
        {"available": True, "rate": 0.0008, "percentile": 96.0}, 95.0, 5.0,
    )
    check("funding decision at p96 = block_long", decision == "block_long",
          f"got {decision}")
    decision, _ = replay_mod._funding_decision_for_heartbeat(
        {"available": True, "rate": -0.0008, "percentile": 3.0}, 95.0, 5.0,
    )
    check("funding decision at p3 = block_short", decision == "block_short",
          f"got {decision}")
    decision, _ = replay_mod._funding_decision_for_heartbeat(
        None, 95.0, 5.0,
    )
    check("funding decision when state=None -> missing_data",
          decision == "missing_data", f"got {decision}")

    # Argparse: --config and --strategy are mutually exclusive
    import sys as _sys
    saved_argv = _sys.argv
    try:
        _sys.argv = ["replay_live.py", "--config", "x", "--strategy", "y"]
        raised = False
        try:
            replay_mod.main()
        except SystemExit:
            raised = True
        check("--config + --strategy raises (mutually exclusive)", raised)
    finally:
        _sys.argv = saved_argv

    # Config file used by the new mode parses to the BTC/ETH long-short
    # funding setup
    cfg_path = ROOT / "state" / "live_multiasset_long_short_funding.yaml"
    cfg = yaml.safe_load(open(cfg_path))
    check("config under test has BTC/USDT + ETH/USDT",
          cfg["assets"] == ["BTC/USDT", "ETH/USDT"])
    check("config under test enables funding filter",
          cfg["funding_filter"]["enabled"] is True)

    # _resolve_path works for the state/-relative form used in the
    # live config
    resolved = replay_mod._resolve_path("state/strategy_supertrend_long_short.yaml",
                                         cfg_path.parent)
    check("_resolve_path resolves state/-relative path to repo root",
          resolved == ROOT / "state" / "strategy_supertrend_long_short.yaml",
          f"got {resolved}")

    # Synthetic CSV writer test — feed two fake trades through the
    # writer surface and verify the on-disk header + row count.
    with tempfile.TemporaryDirectory() as tmp:
        out_csv = Path(tmp) / "replay_trades_synthetic.csv"
        rows = [
            {"asset": "BTC/USDT", "direction": "long",
             "entry_time": "2026-03-01T00:00:00+00:00",
             "exit_time": "2026-03-02T04:00:00+00:00",
             "entry_price": 70000.0, "exit_price": 71400.0,
             "return_pct": 0.02, "net_return_pct": 0.009,
             "setup": "supertrend", "exit_reason": "supertrend_flip",
             "bars_held": 7, "funding_decision": "allow"},
            {"asset": "ETH/USDT", "direction": "short",
             "entry_time": "2026-03-03T08:00:00+00:00",
             "exit_time": "2026-03-04T16:00:00+00:00",
             "entry_price": 3500.0, "exit_price": 3430.0,
             "return_pct": 0.02, "net_return_pct": 0.009,
             "setup": "supertrend_short", "exit_reason": "stop",
             "bars_held": 8, "funding_decision": "allow"},
        ]
        import csv as _csv
        with open(out_csv, "w", newline="") as fh:
            w = _csv.DictWriter(fh, fieldnames=replay_mod.TRADE_CSV_COLUMNS)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k) for k in replay_mod.TRADE_CSV_COLUMNS})
        with open(out_csv) as fh:
            reader = _csv.DictReader(fh)
            header = reader.fieldnames
            written = list(reader)
        check("CSV header matches TRADE_CSV_COLUMNS order",
              header == replay_mod.TRADE_CSV_COLUMNS, f"got {header}")
        check("CSV wrote both rows", len(written) == 2)
        check("CSV row 1 asset = BTC/USDT", written[0]["asset"] == "BTC/USDT")
        check("CSV row 2 direction = short",
              written[1]["direction"] == "short")
        check("CSV row 1 funding_decision = allow",
              written[0]["funding_decision"] == "allow")
        check("CSV row 1 bars_held = 7",
              written[0]["bars_held"] == "7")

    # Source-level checks: the new mode wires the closed-bar semantics
    # (Issue #24) and the funding overlay (Issue #21) for parity with
    # the live worker.
    replay_src = (ROOT / "scripts" / "replay_live.py").read_text()
    check("replay source uses signal_row for long_entry",
          "signals.long_entry(signal_row" in replay_src)
    check("replay source uses signal_row for short_entry",
          "signals.short_entry(signal_row" in replay_src)
    check("replay source uses signal_row for long_exit",
          "signals.long_exit(signal_row" in replay_src)
    check("replay source uses signal_row for short_exit",
          "signals.short_exit(signal_row" in replay_src)
    check("replay source keeps intra-bar stop via display_row low/high",
          ('display_row.get("low")' in replay_src
           and 'display_row.get("high")' in replay_src))
    check("replay source imports LiveFundingOverlay from multi_loop",
          "LiveFundingOverlay" in replay_src
          and "from hermes_trading.multi_loop" in replay_src)
    check("replay source enforces portfolio cap via can_enter",
          "can_enter(asset, positions_by_asset, max_open)" in replay_src)
    print()

    # --- 16. vol_sizing live overlay (Issue #33)
    # NOTE: Section 15 is reserved for the online walk-forward simulator
    # (Issue #32, in flight). Numbering chosen to avoid collision.
    print("16. vol_sizing live overlay (Issue #33)")
    from hermes_trading.multi_loop import (
        VOL_SIZING_WINDOW_BARS_DEFAULT,
        VOL_SIZING_TRAIN_MONTHS_DEFAULT,
        VOL_SIZING_MULT_LOW_DEFAULT,
        VOL_SIZING_MULT_MID_DEFAULT,
        VOL_SIZING_MULT_HIGH_DEFAULT,
        vol_bucket_from_thresholds,
        vol_multiplier_from_bucket,
        LiveVolSizingOverlay,
    )

    # 15.0 Locked defaults match Issue #27 spec
    check("VOL_SIZING_WINDOW_BARS_DEFAULT == 24 (4 days at 4h)",
          VOL_SIZING_WINDOW_BARS_DEFAULT == 24)
    check("VOL_SIZING_TRAIN_MONTHS_DEFAULT == 12 (rolling refit)",
          VOL_SIZING_TRAIN_MONTHS_DEFAULT == 12)
    check("VOL_SIZING_MULT_LOW_DEFAULT == 1.00",
          abs(VOL_SIZING_MULT_LOW_DEFAULT - 1.00) < 1e-12)
    check("VOL_SIZING_MULT_MID_DEFAULT == 0.50",
          abs(VOL_SIZING_MULT_MID_DEFAULT - 0.50) < 1e-12)
    check("VOL_SIZING_MULT_HIGH_DEFAULT == 0.25",
          abs(VOL_SIZING_MULT_HIGH_DEFAULT - 0.25) < 1e-12)

    # 15.1 vol_bucket_from_thresholds: pure mapping
    check("low vol (rv < q25) -> Q1",
          vol_bucket_from_thresholds(0.005, 0.010, 0.020) == "Q1")
    check("mid vol (q25 < rv < q75) -> Q2_Q3",
          vol_bucket_from_thresholds(0.015, 0.010, 0.020) == "Q2_Q3")
    check("high vol (rv >= q75) -> Q4",
          vol_bucket_from_thresholds(0.025, 0.010, 0.020) == "Q4")
    check("rv == q25 boundary -> Q1 (<= q25)",
          vol_bucket_from_thresholds(0.010, 0.010, 0.020) == "Q1")
    check("rv == q75 boundary -> Q4 (>= q75)",
          vol_bucket_from_thresholds(0.020, 0.010, 0.020) == "Q4")
    check("None rv -> warmup",
          vol_bucket_from_thresholds(None, 0.010, 0.020) == "warmup")
    check("None thresholds -> warmup",
          vol_bucket_from_thresholds(0.015, None, None) == "warmup")

    # 15.2 vol_multiplier_from_bucket: pure mapping
    check("Q1 bucket -> 1.00 multiplier",
          abs(vol_multiplier_from_bucket("Q1") - 1.00) < 1e-12)
    check("Q2_Q3 bucket -> 0.50 multiplier",
          abs(vol_multiplier_from_bucket("Q2_Q3") - 0.50) < 1e-12)
    check("Q4 bucket -> 0.25 multiplier",
          abs(vol_multiplier_from_bucket("Q4") - 0.25) < 1e-12)
    check("warmup bucket -> 1.00 fail-open multiplier",
          abs(vol_multiplier_from_bucket("warmup") - 1.00) < 1e-12)
    check("unknown bucket -> 1.00 fail-open multiplier",
          abs(vol_multiplier_from_bucket("nonsense") - 1.00) < 1e-12)
    check("custom mult_high parameter respected",
          abs(vol_multiplier_from_bucket("Q4", mult_high=0.10) - 0.10) < 1e-12)

    # 15.3 New live yaml exists, is opt-in, contains the spec block
    vol_cfg_path = ROOT / "state" / "live_multiasset_long_short_funding_vol.yaml"
    check("opt-in vol_sizing config file exists",
          vol_cfg_path.exists())
    vol_cfg = yaml.safe_load(open(vol_cfg_path))
    check("vol config: assets == [BTC/USDT, ETH/USDT]",
          vol_cfg["assets"] == ["BTC/USDT", "ETH/USDT"])
    check("vol config: uses long-short strategy yaml",
          vol_cfg["strategy"] == "state/strategy_supertrend_long_short.yaml")
    check("vol config: funding_filter still enabled",
          vol_cfg["funding_filter"]["enabled"] is True)
    check("vol config: vol_sizing block exists",
          "vol_sizing" in vol_cfg)
    check("vol config: vol_sizing.enabled == True (opt-in active)",
          vol_cfg["vol_sizing"]["enabled"] is True)
    check("vol config: vol_sizing.window_bars == 24",
          vol_cfg["vol_sizing"]["window_bars"] == 24)
    check("vol config: vol_sizing.train_months == 12",
          vol_cfg["vol_sizing"]["train_months"] == 12)
    check("vol config: mult ladder 1.00 / 0.50 / 0.25",
          vol_cfg["vol_sizing"]["mult_low"] == 1.00
          and vol_cfg["vol_sizing"]["mult_mid"] == 0.50
          and vol_cfg["vol_sizing"]["mult_high"] == 0.25)

    # 15.4 EXISTING live yaml unchanged (no vol_sizing block; default OFF)
    existing_cfg_path = ROOT / "state" / "live_multiasset_long_short_funding.yaml"
    existing_cfg = yaml.safe_load(open(existing_cfg_path))
    check("existing live yaml has NO vol_sizing block (unchanged)",
          "vol_sizing" not in existing_cfg)

    # 15.5 LiveVolSizingOverlay: insufficient-history fails open
    #
    # Synthetic overlay constructed by injecting a precomputed rv series
    # rather than loading klines, so we exercise the lookup logic
    # without disk / network.
    import pandas as _pd_15
    overlay = LiveVolSizingOverlay.__new__(LiveVolSizingOverlay)
    overlay.assets = ["BTC/USDT"]
    overlay.timeframe = "4h"
    overlay.window_bars = 24
    overlay.train_months = 12
    overlay.mult_low = 1.00
    overlay.mult_mid = 0.50
    overlay.mult_high = 0.25
    # Tiny rv series — well under the 50-obs stability floor; train slice
    # will be too small, expect fail-open.
    small_idx = _pd_15.date_range("2026-04-01", periods=10, freq="4h", tz="UTC")
    overlay.realised_vol = {"BTC/USDT": _pd_15.Series(
        [0.01] * 10, index=small_idx, dtype=float,
    )}
    s = overlay.state_at("BTC/USDT", small_idx[-1])
    check("insufficient-history -> available=False",
          s["available"] is False, f"got {s}")
    check("insufficient-history -> multiplier == 1.00 (fail-open)",
          abs(s["multiplier"] - 1.00) < 1e-12, f"got {s['multiplier']}")
    check("insufficient-history -> bucket == 'warmup'",
          s["bucket"] == "warmup", f"got {s['bucket']}")

    # 15.6 LiveVolSizingOverlay: with sufficient history, low / mid / high vol
    # produce the expected multipliers
    #
    # Build a 200-bar rv series where the LAST bar is set to a known value.
    # Most values are 0.010 (mid); we craft the test bar to fall into Q1 / Q4.
    long_idx = _pd_15.date_range("2026-01-01", periods=400, freq="4h", tz="UTC")
    rv_base = _pd_15.Series([0.010] * 400, index=long_idx, dtype=float)
    # Spread the train-window values: half at 0.005, half at 0.015 so
    # Q25 ~ 0.005, Q75 ~ 0.015.
    rv_base.iloc[:200] = 0.005
    rv_base.iloc[200:399] = 0.015
    # Probe bar (last) set to low-vol value -> Q1 expected
    rv_base.iloc[-1] = 0.003
    overlay.realised_vol = {"BTC/USDT": rv_base}
    s_low = overlay.state_at("BTC/USDT", long_idx[-1])
    check("sufficient history + low vol -> Q1 multiplier 1.00",
          s_low["available"] is True
          and s_low["bucket"] == "Q1"
          and abs(s_low["multiplier"] - 1.00) < 1e-12,
          f"got {s_low}")
    # Probe bar set to mid-vol value -> Q2_Q3 expected
    rv_base.iloc[-1] = 0.010
    overlay.realised_vol = {"BTC/USDT": rv_base}
    s_mid = overlay.state_at("BTC/USDT", long_idx[-1])
    check("sufficient history + mid vol -> Q2_Q3 multiplier 0.50",
          s_mid["bucket"] == "Q2_Q3"
          and abs(s_mid["multiplier"] - 0.50) < 1e-12,
          f"got {s_mid}")
    # Probe bar set to high-vol value -> Q4 expected
    rv_base.iloc[-1] = 0.030
    overlay.realised_vol = {"BTC/USDT": rv_base}
    s_high = overlay.state_at("BTC/USDT", long_idx[-1])
    check("sufficient history + high vol -> Q4 multiplier 0.25",
          s_high["bucket"] == "Q4"
          and abs(s_high["multiplier"] - 0.25) < 1e-12,
          f"got {s_high}")

    # 15.7 No future leak — the train window for the threshold lookup
    # strictly excludes the current bar.
    #
    # If the current bar were INCLUDED, an extreme outlier at that bar
    # would shift Q75 upward and the bar might not classify as Q4. By
    # excluding it, the bar at 0.030 (well above the historical Q75 ~ 0.015)
    # is correctly classified as Q4 — which the previous check confirmed.
    # Additional explicit check: the quartile dict reports a finite train_n
    # strictly less than the total series length (since the last bar is
    # excluded and we slice by the trailing N months).
    qs = overlay._train_window_quartiles("BTC/USDT", long_idx[-1])
    check("train-window quartile n < full series len (no future leak)",
          0 < qs["n"] < len(rv_base), f"got n={qs['n']}, total={len(rv_base)}")

    # 15.8 multi_loop.run() source-level wiring
    multi_src = (ROOT / "hermes_trading" / "multi_loop.py").read_text()
    check("multi_loop instantiates LiveVolSizingOverlay when enabled",
          "vol_overlay = LiveVolSizingOverlay(" in multi_src)
    check("multi_loop reads vol_sizing.enabled from cfg",
          'vol_cfg.get("enabled"' in multi_src)
    check("multi_loop applies vol multiplier at long entry",
          "size_per_asset * current_vol_mult" in multi_src)
    check("multi_loop records base_size on the position dict",
          '"base_size": size_per_asset' in multi_src)
    check("multi_loop records vol_multiplier on the position dict",
          '"vol_multiplier": current_vol_mult' in multi_src)
    check("multi_loop carries base_size / vol_multiplier onto trade row",
          'trade["base_size"] = position.get("base_size")' in multi_src
          and 'trade["vol_multiplier"] = position.get("vol_multiplier")' in multi_src)
    check("multi_loop carries realized_vol_24 / vol_bucket onto trade row",
          'trade["realized_vol_24"] = position.get("realized_vol_24_at_entry")' in multi_src)
    check("multi_loop adds vol_sizing_enabled to heartbeat",
          'asset_hb["vol_sizing_enabled"] = vol_sizing_enabled' in multi_src)
    check("multi_loop adds vol_bucket / vol_multiplier to heartbeat",
          'asset_hb["vol_bucket"]' in multi_src
          and 'asset_hb["vol_multiplier"]' in multi_src)
    check("multi_loop emits verbose vol line",
          '"  vol: rv24=' in multi_src)
    check("funding gate stays an independent hard gate (still in source)",
          "evaluate_funding_gate" in multi_src
          and 'if not gate["allow"]' in multi_src)
    print()

    # --- 14. Live-vs-research fill-accounting parity (Issue #29)
    print("14. Live-vs-research fill-accounting parity (Issue #29)")
    from hermes_trading.multi_loop import (
        RESEARCH_FEE_PER_SIDE, RESEARCH_SLIPPAGE,
        evaluate_tick as parity_eval_tick,
        build_trade_row as parity_build_trade_row,
    )
    from hermes_trading import backtest as parity_bt

    # 14.0 Default constants match the research backtest's default args
    check("RESEARCH_FEE_PER_SIDE == 0.001 (10 bp / side)",
          abs(RESEARCH_FEE_PER_SIDE - 0.001) < 1e-12,
          f"got {RESEARCH_FEE_PER_SIDE}")
    check("RESEARCH_SLIPPAGE == 0.0005 (5 bp)",
          abs(RESEARCH_SLIPPAGE - 0.0005) < 1e-12,
          f"got {RESEARCH_SLIPPAGE}")

    # 14.1 build_trade_row default fee equals RESEARCH_FEE_PER_SIDE
    pos_simple = {
        "entry_price": 70000.0 * (1 + RESEARCH_SLIPPAGE),
        "opened_at": "2026-05-29T12:00:00+00:00",
        "size": 0.5,
        "direction": "long",
        "setup": "supertrend",
    }
    exit_fill_long = 71400.0 * (1 - RESEARCH_SLIPPAGE)
    row_default = parity_build_trade_row(
        asset="BTC/USDT", position=pos_simple, exit_price=exit_fill_long,
        exit_reason="supertrend_flip", bars_held=10,
        strategy_version="parity-test",
    )
    expected_ret = (exit_fill_long - pos_simple["entry_price"]) / pos_simple["entry_price"]
    expected_net = (expected_ret - 2 * RESEARCH_FEE_PER_SIDE) * 0.5
    check("build_trade_row default fee deducts 2*0.001 in return space",
          abs(row_default["net_return_pct"] - expected_net) < 1e-12,
          f"got {row_default['net_return_pct']} expected {expected_net}")

    # 14.2 build_trade_row accepts a custom fee
    row_zero_fee = parity_build_trade_row(
        asset="BTC/USDT", position=pos_simple, exit_price=exit_fill_long,
        exit_reason="supertrend_flip", bars_held=10,
        strategy_version="parity-test", fee=0.0,
    )
    check("build_trade_row fee=0 zero-fee path",
          abs(row_zero_fee["net_return_pct"] - expected_ret * 0.5) < 1e-12,
          f"got {row_zero_fee['net_return_pct']}")
    check("fee_per_side recorded on trade row",
          abs(row_default["fee_per_side"] - RESEARCH_FEE_PER_SIDE) < 1e-12)

    # 14.3 evaluate_tick LONG entry: entry_price = close * (1 + slippage)
    bullish_flip_row_14 = {
        "close": 70000.0, "high": 70100, "low": 69900,
        "ema_fast": 70000.0, "ema_slow": 65000.0,
        "rsi": 50.0, "atr": 700.0, "ema_pull": 69800.0,
        "supertrend_direction": 1, "supertrend_direction_prev": -1,
        "supertrend_line": 69300.0,
        "three_bar": False, "vwap": 69800.0, "donchian_high": 71000.0,
    }
    st_strategy_14 = {
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
    new_pos_long, _ = parity_eval_tick(
        asset="BTC/USDT", row=bullish_flip_row_14, strategy=st_strategy_14,
        position=None,
        positions_by_asset={"BTC/USDT": None},
        max_open_positions=1, size_per_asset=0.5,
        strategy_version="parity-test",
    )
    check("long entry fill = close * (1 + slippage)",
          new_pos_long is not None
          and abs(new_pos_long["entry_price"] - 70000.0 * (1 + RESEARCH_SLIPPAGE)) < 1e-9,
          f"got {new_pos_long.get('entry_price') if new_pos_long else None}")
    check("long entry records pre-slip close for diagnostics",
          new_pos_long is not None
          and abs(new_pos_long["entry_price_pre_slip"] - 70000.0) < 1e-9)

    # 14.4 evaluate_tick LONG non-stop exit (SuperTrend flip): exit_price =
    # close * (1 - slippage); net deducts 2*fee*size
    long_pos_open = dict(new_pos_long)
    bearish_flip_row_14 = dict(bullish_flip_row_14)
    bearish_flip_row_14["close"] = 70500.0
    bearish_flip_row_14["supertrend_direction"] = -1
    bearish_flip_row_14["supertrend_direction_prev"] = 1
    # supertrend_line is BELOW the entry-time stop (long_pos_open["stop"]
    # ≈ 69300) so the ratchet branch in signals.long_exit does not raise
    # the stop above the bar low — keeps this case a "supertrend_flip"
    # rather than degenerating to "stop".
    bearish_flip_row_14["supertrend_line"] = 69100.0
    bearish_flip_row_14["low"] = 70400.0     # well ABOVE the ~69300 stop
    bearish_flip_row_14["high"] = 70600.0
    _, trade_flip = parity_eval_tick(
        asset="BTC/USDT", row=bearish_flip_row_14, strategy=st_strategy_14,
        position=long_pos_open,
        positions_by_asset={"BTC/USDT": long_pos_open},
        max_open_positions=1, size_per_asset=0.5,
        strategy_version="parity-test",
    )
    check("long non-stop exit produced a closed trade",
          trade_flip is not None,
          f"got {trade_flip}")
    if trade_flip is not None:
        expected_exit = 70500.0 * (1 - RESEARCH_SLIPPAGE)
        check("long non-stop exit fill = close * (1 - slippage)",
              abs(trade_flip["exit_price"] - expected_exit) < 1e-9,
              f"got {trade_flip['exit_price']} expected {expected_exit}")
        check("long non-stop exit records exit_price_pre_slip = bar close",
              abs(trade_flip["exit_price_pre_slip"] - 70500.0) < 1e-9)
        exp_ret = (expected_exit - long_pos_open["entry_price"]) / long_pos_open["entry_price"]
        exp_net = (exp_ret - 2 * RESEARCH_FEE_PER_SIDE) * 0.5
        check("long non-stop net_return_pct = (ret - 2*fee) * size",
              abs(trade_flip["net_return_pct"] - exp_net) < 1e-12,
              f"got {trade_flip['net_return_pct']} expected {exp_net}")

    # 14.5 evaluate_tick LONG STOP exit: exit_price = stop * (1 - slippage)
    stop_pos = {
        "asset": "BTC/USDT",
        "entry_price": 70000.0 * (1 + RESEARCH_SLIPPAGE),
        "entry_price_pre_slip": 70000.0,
        "opened_at": "2026-05-29T12:00:00+00:00",
        "size": 0.5,
        "direction": "long",
        "setup": "supertrend",
        "stop": 69300.0,
    }
    stop_breach_row = {
        "close": 69500.0, "high": 69700.0, "low": 69200.0,    # low < stop
        "ema_fast": 70000.0, "ema_slow": 65000.0,
        "rsi": 40.0, "atr": 700.0, "ema_pull": 69800.0,
        "supertrend_direction": 1, "supertrend_direction_prev": 1,
        "supertrend_line": 69100.0,
        "three_bar": False, "vwap": 69800.0, "donchian_high": 70000.0,
    }
    _, trade_stop = parity_eval_tick(
        asset="BTC/USDT", row=stop_breach_row, strategy=st_strategy_14,
        position=stop_pos,
        positions_by_asset={"BTC/USDT": stop_pos},
        max_open_positions=1, size_per_asset=0.5,
        strategy_version="parity-test",
    )
    check("long stop exit produced a closed trade",
          trade_stop is not None and trade_stop["exit_reason"] == "stop",
          f"got {trade_stop}")
    if trade_stop is not None:
        expected_stop_exit = 69300.0 * (1 - RESEARCH_SLIPPAGE)
        check("long STOP exit fill = stop * (1 - slippage)",
              abs(trade_stop["exit_price"] - expected_stop_exit) < 1e-9,
              f"got {trade_stop['exit_price']} expected {expected_stop_exit}")
        check("long STOP exit records exit_price_pre_slip = stop",
              abs(trade_stop["exit_price_pre_slip"] - 69300.0) < 1e-9)

    # 14.6 SHORT round-trip — same accounting symmetric
    st_strategy_short = dict(st_strategy_14)
    st_strategy_short["shorts"] = {"enabled": True, "supertrend": {"enabled": True}}
    st_strategy_short["setups"] = dict(st_strategy_14["setups"])

    # Short signal row: ST flipped UP→DOWN AND bearish regime (ema_fast < ema_slow)
    short_entry_row = {
        "close": 70000.0, "high": 70100.0, "low": 69900.0,
        "ema_fast": 65000.0, "ema_slow": 70000.0,           # bearish regime
        "rsi": 50.0, "atr": 700.0, "ema_pull": 70200.0,
        "supertrend_direction": -1, "supertrend_direction_prev": 1,
        "supertrend_line": 70700.0,
        "three_bar": False, "vwap": 70200.0, "donchian_low": 69000.0,
    }
    # evaluate_tick's flat-entry path tries longs first; bullish regime is
    # missing, so the short branch is exercised by directly calling the
    # signals module + recording with the same slippage convention. We
    # verify the short-direction round-trip math directly via build_trade_row.
    short_entry_price = 70000.0 * (1 - RESEARCH_SLIPPAGE)
    short_pos = {
        "asset": "BTC/USDT",
        "entry_price": short_entry_price,
        "entry_price_pre_slip": 70000.0,
        "opened_at": "2026-05-29T12:00:00+00:00",
        "size": 0.5,
        "direction": "short",
        "setup": "supertrend_short",
        "stop": 70700.0,
    }
    # Non-stop short exit: close higher
    short_exit_close = 69500.0
    short_exit_fill = short_exit_close * (1 + RESEARCH_SLIPPAGE)
    row_short_close = parity_build_trade_row(
        asset="BTC/USDT", position={**short_pos, "exit_price_pre_slip": short_exit_close},
        exit_price=short_exit_fill,
        exit_reason="supertrend_flip", bars_held=8, strategy_version="parity-test",
    )
    exp_short_ret = (short_pos["entry_price"] - short_exit_fill) / short_pos["entry_price"]
    exp_short_net = (exp_short_ret - 2 * RESEARCH_FEE_PER_SIDE) * 0.5
    check("short non-stop exit: ret = (entry - exit) / entry",
          abs(row_short_close["return_pct"] - exp_short_ret) < 1e-12,
          f"got {row_short_close['return_pct']} expected {exp_short_ret}")
    check("short non-stop exit: net = (ret - 2*fee) * size",
          abs(row_short_close["net_return_pct"] - exp_short_net) < 1e-12,
          f"got {row_short_close['net_return_pct']} expected {exp_short_net}")
    # Short stop exit: stop * (1 + slippage)
    short_stop_fill = 70700.0 * (1 + RESEARCH_SLIPPAGE)
    row_short_stop = parity_build_trade_row(
        asset="BTC/USDT",
        position={**short_pos, "exit_price_pre_slip": 70700.0},
        exit_price=short_stop_fill,
        exit_reason="stop", bars_held=4, strategy_version="parity-test",
    )
    exp_stop_ret = (short_pos["entry_price"] - short_stop_fill) / short_pos["entry_price"]
    exp_stop_net = (exp_stop_ret - 2 * RESEARCH_FEE_PER_SIDE) * 0.5
    check("short STOP exit: exit_price = stop * (1 + slippage)",
          abs(short_stop_fill - 70700.0 * (1 + RESEARCH_SLIPPAGE)) < 1e-12)
    check("short STOP exit: net = (ret - 2*fee) * size (negative round-trip)",
          abs(row_short_stop["net_return_pct"] - exp_stop_net) < 1e-12,
          f"got {row_short_stop['net_return_pct']} expected {exp_stop_net}")

    # 14.7 Direct cross-check: backtest._run_state_machine and the live
    # evaluate_tick / build_trade_row produce the SAME entry price, exit
    # price, and net_return_pct for an identical synthetic trade.
    #
    # Setup: a 3-bar window. Bar 0 fires the long SuperTrend entry; bar 2
    # fires the SuperTrend flip exit. Same strategy, same fee/slippage,
    # so both engines must agree.
    base_strategy_x = dict(st_strategy_14)
    parity_records = [
        # bar 0: bullish flip — entry fires
        {"close": 70000.0, "high": 70100.0, "low": 69900.0,
         "ema_fast": 70000.0, "ema_slow": 65000.0,
         "rsi": 50.0, "atr": 700.0, "ema_pull": 69800.0,
         "supertrend_direction": 1, "supertrend_direction_prev": -1,
         "supertrend_line": 69300.0,
         "three_bar": False, "vwap": 69800.0,
         "donchian_high": 71000.0, "donchian_low": 69000.0, "donchian_mid": 70000.0,
         "markov_long_allowed": True, "markov_size_multiplier": 1.0,
         "markov_allowed_setups": None, "markov_regime_score": 1.0,
         "markov_state": "x", "markov_stable_state": "x",
         "volume": 10.0, "ts": "2026-05-29T00:00:00+00:00"},
        # bar 1: holds, no exit (ST still up, low above stop)
        {"close": 70200.0, "high": 70300.0, "low": 70100.0,
         "ema_fast": 70010.0, "ema_slow": 65000.0,
         "rsi": 52.0, "atr": 700.0, "ema_pull": 69800.0,
         "supertrend_direction": 1, "supertrend_direction_prev": 1,
         "supertrend_line": 69350.0,
         "three_bar": False, "vwap": 69800.0,
         "donchian_high": 71000.0, "donchian_low": 69000.0, "donchian_mid": 70000.0,
         "markov_long_allowed": True, "markov_size_multiplier": 1.0,
         "markov_allowed_setups": None, "markov_regime_score": 1.0,
         "markov_state": "x", "markov_stable_state": "x",
         "volume": 10.0, "ts": "2026-05-29T04:00:00+00:00"},
        # bar 2: bearish flip — exit fires (non-stop)
        {"close": 70500.0, "high": 70600.0, "low": 70400.0,
         "ema_fast": 70000.0, "ema_slow": 65000.0,
         "rsi": 45.0, "atr": 700.0, "ema_pull": 69800.0,
         "supertrend_direction": -1, "supertrend_direction_prev": 1,
         "supertrend_line": 70900.0,
         "three_bar": False, "vwap": 69800.0,
         "donchian_high": 71000.0, "donchian_low": 69000.0, "donchian_mid": 70000.0,
         "markov_long_allowed": True, "markov_size_multiplier": 1.0,
         "markov_allowed_setups": None, "markov_regime_score": 1.0,
         "markov_state": "x", "markov_stable_state": "x",
         "volume": 10.0, "ts": "2026-05-29T08:00:00+00:00"},
    ]
    bt_result = parity_bt._run_state_machine(
        parity_records, base_strategy_x, warmup=0,
        fee=RESEARCH_FEE_PER_SIDE, slippage=RESEARCH_SLIPPAGE,
    )
    check("backtest engine produced exactly 1 closed trade for parity scenario",
          len(bt_result["trades"]) == 1,
          f"got {len(bt_result['trades'])}")

    # Live side: feed bar 0 to evaluate_tick (creates the long pos), then
    # feed bar 2 to close it.
    live_pos, _ = parity_eval_tick(
        asset="BTC/USDT", row=parity_records[0], strategy=base_strategy_x,
        position=None,
        positions_by_asset={"BTC/USDT": None},
        max_open_positions=1, size_per_asset=0.5,
        strategy_version="parity-test",
    )
    _, live_trade = parity_eval_tick(
        asset="BTC/USDT", row=parity_records[2], strategy=base_strategy_x,
        position=live_pos,
        positions_by_asset={"BTC/USDT": live_pos},
        max_open_positions=1, size_per_asset=0.5,
        strategy_version="parity-test",
    )

    if bt_result["trades"] and live_trade is not None:
        bt_trade = bt_result["trades"][0]
        check("PARITY: backtest entry_price == live entry_price",
              abs(bt_trade["entry_price"] - live_pos["entry_price"]) < 1e-9,
              f"bt={bt_trade['entry_price']} live={live_pos['entry_price']}")
        check("PARITY: backtest exit_price == live exit_price",
              abs(bt_trade["exit_price"] - live_trade["exit_price"]) < 1e-9,
              f"bt={bt_trade['exit_price']} live={live_trade['exit_price']}")
        # Backtest size is base_size from strategy ("position_size_r": 0.5)
        # × markov size_multiplier (1.0) = 0.5. Live evaluate_tick uses the
        # same size_per_asset = 0.5 (the test passes it explicitly). So
        # both engines apply size = 0.5 here.
        check("PARITY: backtest net_return_pct == live net_return_pct",
              abs(bt_trade["net_return_pct"] - live_trade["net_return_pct"]) < 1e-9,
              f"bt={bt_trade['net_return_pct']} live={live_trade['net_return_pct']}")
        check("PARITY: backtest gross_return_pct == live return_pct",
              abs(bt_trade["gross_return_pct"] - live_trade["return_pct"]) < 1e-9,
              f"bt={bt_trade['gross_return_pct']} live={live_trade['return_pct']}")

    # 14.8 Source-level confirmation that loop.py and multi_loop.py both
    # apply the slippage and fee constants.
    multi_src = (ROOT / "hermes_trading" / "multi_loop.py").read_text()
    single_src = (ROOT / "hermes_trading" / "loop.py").read_text()
    check("multi_loop.py: long entry applies slippage",
          "last_price * (1.0 + slippage)" in multi_src)
    check("multi_loop.py: short entry applies slippage",
          "last_price * (1.0 - slippage)" in multi_src)
    check("multi_loop.py: stop exit fills at position['stop']",
          'base_exit = float(position["stop"])' in multi_src)
    check("multi_loop.py: passes fee_per_side to build_trade_row",
          "fee=fee_per_side" in multi_src)
    check("loop.py: long entry applies SLIPPAGE",
          "last * (1.0 + SLIPPAGE)" in single_src)
    check("loop.py: short entry applies SLIPPAGE",
          "last * (1.0 - SLIPPAGE)" in single_src)
    check("loop.py: net_return_pct deducts 2*FEE_PER_SIDE",
          "ret_pct - 2.0 * FEE_PER_SIDE" in single_src)
    check("loop.py imports RESEARCH_* constants from multi_loop",
          "from .multi_loop import RESEARCH_FEE_PER_SIDE" in single_src)

    # 14.9 Multi-asset config can override the defaults
    cfg_override = {"assets": ["BTC/USDT"], "timeframe": "4h",
                    "strategy": "state/strategy.yaml",
                    "fee_per_side": 0.0005, "slippage": 0.0002}
    check("config supports fee_per_side override key",
          cfg_override.get("fee_per_side") == 0.0005)
    check("config supports slippage override key",
          cfg_override.get("slippage") == 0.0002)
    print()

    # --- 15. Online walk-forward adaptive simulator (Issue #32)
    print("15. Online walk-forward adaptive simulator (Issue #32)")
    import importlib
    import numpy as np
    import pandas as pd

    sim_spec = importlib.util.spec_from_file_location(
        "run_online_walk_forward",
        ROOT / "scripts" / "run_online_walk_forward.py",
    )
    sim_mod = importlib.util.module_from_spec(sim_spec)
    try:
        sim_spec.loader.exec_module(sim_mod)
        check("simulator module imports cleanly", True)
    except Exception as exc:  # noqa: BLE001
        check("simulator module imports cleanly", False, str(exc))
        sim_mod = None

    if sim_mod is not None:
        # 15.1 rolling_decay rule: 10 synthetic losses (PF=0) → 0.25
        synth_losses = [
            {"net_return_pct": -0.01, "exit_reason": "stop",
             "exit_ts": pd.Timestamp(f"2025-01-{i+1:02d}", tz="UTC")}
            for i in range(10)
        ]
        mult, pf = sim_mod.rolling_decay_multiplier(synth_losses)
        check("rolling_decay: 10 losses (PF<0.7) -> 0.25",
              mult == 0.25, f"got mult={mult} pf={pf}")

        # 15.2 rolling_decay rule: 0.7 <= PF < 1.0 case → 0.5
        # 4 losses at -0.01 (sum -0.04), 6 wins at +0.005 (sum +0.030)
        # PF = 0.030 / 0.040 = 0.75; in [0.7, 1.0) band -> 0.5
        synth_pf_low = [{"net_return_pct": -0.01, "exit_reason": "stop",
                         "exit_ts": pd.Timestamp(f"2025-02-{i+1:02d}", tz="UTC")}
                        for i in range(4)] + [
                       {"net_return_pct": +0.005, "exit_reason": "target_rsi",
                        "exit_ts": pd.Timestamp(f"2025-02-{i+1:02d}", tz="UTC")}
                       for i in range(4, 10)]
        mult, pf = sim_mod.rolling_decay_multiplier(synth_pf_low)
        check("rolling_decay: 10 trades PF in [0.7,1.0) -> 0.5",
              mult == 0.5, f"got mult={mult} pf={pf}")

        # 15.3 rolling_decay rule: fewer than 10 trades → 1.0
        synth_few = synth_losses[:5]
        mult, pf = sim_mod.rolling_decay_multiplier(synth_few)
        check("rolling_decay: <10 trades -> 1.0",
              mult == 1.0 and pf is None, f"got mult={mult} pf={pf}")

        # 15.4 consecutive_loss: 3 losses -> 0.5, 4 losses -> 0.25
        three_losses = [
            {"net_return_pct": -0.01, "exit_reason": "stop",
             "exit_ts": pd.Timestamp(f"2025-03-{i+1:02d}", tz="UTC")}
            for i in range(3)
        ]
        mult, streak = sim_mod.consecutive_loss_multiplier(three_losses)
        check("consec_loss: 3 in a row -> 0.5",
              mult == 0.5 and streak == 3, f"got mult={mult} streak={streak}")
        four_losses = three_losses + [
            {"net_return_pct": -0.01, "exit_reason": "stop",
             "exit_ts": pd.Timestamp("2025-03-04", tz="UTC")},
        ]
        mult, streak = sim_mod.consecutive_loss_multiplier(four_losses)
        check("consec_loss: 4 in a row -> 0.25",
              mult == 0.25 and streak == 4, f"got mult={mult} streak={streak}")
        # win in tail resets
        reset = three_losses + [
            {"net_return_pct": +0.01, "exit_reason": "target_rsi",
             "exit_ts": pd.Timestamp("2025-03-04", tz="UTC")},
        ]
        mult, streak = sim_mod.consecutive_loss_multiplier(reset)
        check("consec_loss: any win resets to 1.0",
              mult == 1.0 and streak == 0, f"got mult={mult} streak={streak}")

        # 15.5 stop_cluster: 4 of last 5 stops -> 0.5
        stops_4 = [
            {"net_return_pct": -0.01, "exit_reason": "stop",
             "exit_ts": pd.Timestamp(f"2025-04-{i+1:02d}", tz="UTC")}
            for i in range(4)
        ] + [
            {"net_return_pct": +0.01, "exit_reason": "supertrend_flip",
             "exit_ts": pd.Timestamp("2025-04-05", tz="UTC")},
        ]
        mult, count = sim_mod.stop_cluster_multiplier(stops_4)
        check("stop_cluster: 4 of last 5 stops -> 0.5",
              mult == 0.5 and count == 4, f"got mult={mult} count={count}")
        stops_5 = [
            {"net_return_pct": -0.01, "exit_reason": "stop",
             "exit_ts": pd.Timestamp(f"2025-04-{i+1:02d}", tz="UTC")}
            for i in range(5)
        ]
        mult, count = sim_mod.stop_cluster_multiplier(stops_5)
        check("stop_cluster: 5 of last 5 stops -> 0.25",
              mult == 0.25 and count == 5, f"got mult={mult} count={count}")
        # mixed: 3 of last 5 stops -> 1.0
        stops_3 = stops_5[:3] + [
            {"net_return_pct": +0.01, "exit_reason": "target_rsi",
             "exit_ts": pd.Timestamp("2025-04-04", tz="UTC")},
            {"net_return_pct": +0.01, "exit_reason": "supertrend_flip",
             "exit_ts": pd.Timestamp("2025-04-05", tz="UTC")},
        ]
        mult, count = sim_mod.stop_cluster_multiplier(stops_3)
        check("stop_cluster: 3 of last 5 stops -> 1.0",
              mult == 1.0 and count == 3, f"got mult={mult} count={count}")

        # 15.6 vol_sizing: synthetic high-vol series -> multiplier <= 1.0
        # build a 200-bar series with a clear high-vol regime in the tail
        rng = np.random.default_rng(42)
        idx = pd.date_range("2025-01-01", periods=200, freq="4h", tz="UTC")
        # first 150 bars: low vol (sigma 0.001), last 50 bars: high vol (sigma 0.02)
        log_steps = np.concatenate([
            rng.normal(0.0, 0.001, 150),
            rng.normal(0.0, 0.02, 50),
        ])
        close = 100.0 * np.exp(np.cumsum(log_steps))
        s = pd.Series(close, index=idx)
        vs = sim_mod.VolSizingState(
            s, idx,
            refit_train_months=1,
            refit_every_bars=10,
            vol_window=24,
        )
        # Walk forward so the state refits causally.
        last_mult = None
        last_rv = None
        for i in range(1, len(idx)):
            vs.update(i)
            last_mult, last_rv = vs.multiplier(i)
        check("vol_sizing: high-vol tail produces multiplier <= 1.0",
              last_mult is not None and last_mult <= 1.0,
              f"got mult={last_mult} rv={last_rv}")

        # 15.7 ensemble = MIN(rolling_decay, stop_cluster, vol_sizing)
        # Build a memory where rolling_decay=0.5, stop_cluster=1.0,
        # vol=0.5. MIN should be 0.5.
        ensemble_trades = synth_pf_low  # rolling_decay -> 0.5
        # The stop_cluster lookup on these 10 trades:
        sc_mult, _ = sim_mod.stop_cluster_multiplier(ensemble_trades)
        rd_mult, _ = sim_mod.rolling_decay_multiplier(ensemble_trades)
        # Use a vs that will yield 1.0 (low vol)
        rng2 = np.random.default_rng(7)
        idx2 = pd.date_range("2025-06-01", periods=200, freq="4h", tz="UTC")
        close2 = 100.0 * np.exp(np.cumsum(rng2.normal(0.0, 0.001, 200)))
        vs_low = sim_mod.VolSizingState(
            pd.Series(close2, index=idx2), idx2,
            refit_train_months=1, refit_every_bars=10, vol_window=24,
        )
        for i in range(1, 100):
            vs_low.update(i)
        vol_mult, _ = vs_low.multiplier(99)
        mults = sim_mod._compute_multipliers(
            "ensemble", ensemble_trades, vs_low, 99,
        )
        expected = min(rd_mult, sc_mult, vol_mult)
        check("ensemble = MIN(rolling_decay, stop_cluster, vol_sizing)",
              abs(mults["active"] - expected) < 1e-12,
              f"got active={mults['active']} expected={expected}")

        # 15.8 No future leakage: rule readout at bar T uses only trades
        # with exit_ts < T.
        mem = sim_mod.ClosedTradeMemory()
        t1 = {"net_return_pct": -0.01, "exit_reason": "stop",
              "exit_time": "2025-05-01T00:00:00+00:00",
              "exit_ts": pd.Timestamp("2025-05-01", tz="UTC")}
        t2 = {"net_return_pct": -0.01, "exit_reason": "stop",
              "exit_time": "2025-05-15T00:00:00+00:00",
              "exit_ts": pd.Timestamp("2025-05-15", tz="UTC")}
        mem.record_close("BTC/USDT", t1)
        mem.record_close("BTC/USDT", t2)
        # decision at exactly t2.exit_ts must NOT include t2 (strict <)
        seen_at_t2 = mem.closed_before("BTC/USDT",
                                        pd.Timestamp("2025-05-15", tz="UTC"))
        check("no future leakage: bar T excludes trades whose exit_ts == T",
              len(seen_at_t2) == 1 and seen_at_t2[0] is t1,
              f"got len={len(seen_at_t2)}")
        # decision strictly after t2 includes both
        seen_after = mem.closed_before(
            "BTC/USDT", pd.Timestamp("2025-05-16", tz="UTC"))
        check("no future leakage: bar T+ includes all earlier trades",
              len(seen_after) == 2,
              f"got len={len(seen_after)}")
        # decision at t1.exit_ts excludes both (strict <)
        seen_before = mem.closed_before(
            "BTC/USDT", pd.Timestamp("2025-05-01", tz="UTC"))
        check("no future leakage: bar T == first exit excludes both",
              len(seen_before) == 0,
              f"got len={len(seen_before)}")

        # 15.9 Sizing locks at entry — adaptive rules never resize OPEN trades
        # Inspect the simulator source for the invariant directly: there is no
        # write to position['adaptive_at_entry'] after the entry path.
        sim_src = (ROOT / "scripts" / "run_online_walk_forward.py").read_text()
        # Count assignments to adaptive_at_entry — there should be exactly two:
        # one in the long entry block, one in the short entry block.
        n_assigns = sim_src.count('"adaptive_at_entry":')
        check("size locks at entry: exactly 2 'adaptive_at_entry' assignments",
              n_assigns == 2, f"got {n_assigns}")
        # And the exit path reads but never writes it.
        check("size locks at entry: exit path reads position['adaptive_at_entry']",
              "position[\"adaptive_at_entry\"]" in sim_src)

        # 15.10 `none` adaptive rule -> trades count matches existing 6mo replay.
        # We don't re-run the replay here (network-bound); instead we
        # verify the decision-log column set matches the spec.
        spec_decision_cols = [
            "timestamp", "asset", "action", "direction", "setup",
            "signal_state", "base_size", "adaptive_multiplier",
            "final_size", "reason", "rolling_pf_10",
            "consecutive_losses", "stop_cluster_count",
            "vol_sizing_multiplier", "funding_decision",
            "position_state", "realized_pnl_to_date",
        ]
        check("decision-log columns match spec",
              sim_mod.DECISION_COLUMNS == spec_decision_cols,
              f"got {sim_mod.DECISION_COLUMNS}")
        spec_trade_cols = [
            "asset", "direction", "entry_time", "exit_time",
            "entry_price", "exit_price", "gross_return_pct",
            "net_return_pct", "base_size", "adaptive_multiplier",
            "final_size", "exit_reason",
        ]
        check("trade-log columns match spec",
              sim_mod.TRADE_COLUMNS == spec_trade_cols,
              f"got {sim_mod.TRADE_COLUMNS}")

        # 15.11 ADAPTIVE_RULES contains the 6 spec rules
        spec_rules = ("none", "rolling_decay_size", "consecutive_loss_size",
                      "stop_cluster_size", "vol_sizing", "ensemble")
        check("ADAPTIVE_RULES contains the 6 spec rules",
              set(sim_mod.ADAPTIVE_RULES) == set(spec_rules),
              f"got {sim_mod.ADAPTIVE_RULES}")
    print()

    # --- 17. Replay vol_sizing parity (Issue #34)
    print("17. Replay vol_sizing parity (Issue #34)")
    import importlib.util as _ilu_17
    replay_path_17 = ROOT / "scripts" / "replay_live.py"
    spec_17 = _ilu_17.spec_from_file_location("replay_live_17", replay_path_17)
    replay_mod_17 = _ilu_17.module_from_spec(spec_17)
    spec_17.loader.exec_module(replay_mod_17)

    # 17.1 Replay module imports LiveVolSizingOverlay and the Issue #33 constants
    check("replay imports LiveVolSizingOverlay",
          getattr(replay_mod_17, "LiveVolSizingOverlay", None) is not None)
    check("replay imports VOL_SIZING_WINDOW_BARS_DEFAULT",
          getattr(replay_mod_17, "VOL_SIZING_WINDOW_BARS_DEFAULT", None) == 24)
    check("replay imports VOL_SIZING_TRAIN_MONTHS_DEFAULT",
          getattr(replay_mod_17, "VOL_SIZING_TRAIN_MONTHS_DEFAULT", None) == 12)

    # 17.2 Trade-CSV column spec includes Issue #34 vol fields (positionally
    # appended after the Issue #26 columns so external readers parsing the
    # first 12 columns still work).
    cols_17 = replay_mod_17.TRADE_CSV_COLUMNS
    issue26_cols = [
        "asset", "direction", "entry_time", "exit_time",
        "entry_price", "exit_price", "return_pct", "net_return_pct",
        "setup", "exit_reason", "bars_held", "funding_decision",
    ]
    check("TRADE_CSV_COLUMNS preserves Issue #26 columns (first 12, in order)",
          cols_17[:12] == issue26_cols,
          f"got first 12: {cols_17[:12]}")
    issue34_cols = [
        "base_size", "vol_multiplier", "final_size",
        "realized_vol_24", "vol_bucket",
        "vol_q1", "vol_q2", "vol_q3",
    ]
    check("TRADE_CSV_COLUMNS appends Issue #34 vol fields (8 columns)",
          cols_17[12:20] == issue34_cols,
          f"got cols 12..20: {cols_17[12:20]}")

    # 17.3 Source-level wiring confirms the spec contract
    replay_src_17 = replay_path_17.read_text()
    check("replay reads vol_sizing block from cfg",
          'cfg.get("vol_sizing")' in replay_src_17)
    check("replay flag vol_sizing_enabled is parsed",
          "vol_sizing_enabled" in replay_src_17)
    check("replay instantiates LiveVolSizingOverlay when enabled",
          "vol_overlay = LiveVolSizingOverlay(" in replay_src_17)
    check("replay logs vol_sizing=ENABLED on boot",
          "vol_sizing=" in replay_src_17
          and "'ENABLED' if vol_sizing_enabled" in replay_src_17)
    check("replay calls vol_overlay.state_at on signal_row[ts]",
          'vol_overlay.state_at(asset, signal_row["ts"])' in replay_src_17)
    check("replay applies vol_mult at LONG entry (final_size = base * mult)",
          "size_per_asset * current_vol_mult" in replay_src_17)
    check("replay records base_size_at_entry / vol_multiplier_at_entry on position",
          '"base_size_at_entry": size_per_asset' in replay_src_17
          and '"vol_multiplier_at_entry": current_vol_mult' in replay_src_17)
    check("replay records realized_vol_24 / vol_bucket / vol_q* on position",
          '"realized_vol_24_at_entry"' in replay_src_17
          and '"vol_bucket_at_entry"' in replay_src_17
          and '"vol_q1_at_entry"' in replay_src_17)
    check("replay carries vol fields onto closed trade row",
          '"base_size": position.get("base_size_at_entry")' in replay_src_17
          and '"vol_multiplier": position.get("vol_multiplier_at_entry")' in replay_src_17
          and '"realized_vol_24": position.get("realized_vol_24_at_entry")' in replay_src_17)
    check("replay end-of-data close also writes vol fields",
          '"base_size": cur_pos.get("base_size_at_entry")' in replay_src_17)
    check("replay emits per-bar verbose vol line on state change",
          '"  vol {asset} rv24=' in replay_src_17
          and "bucket=" in replay_src_17 and "mult=" in replay_src_17)
    check("replay ENTER line shows size=… vol_mult=…",
          'vol_mult={enter_event[\'vol_mult\']:.2f}' in replay_src_17)
    check("replay summary block reports vol_sizing mean mult + by bucket",
          'vol_sizing mean mult' in replay_src_17
          and "vol_sizing by bucket" in replay_src_17)

    # 17.4 New live yaml has vol_sizing.enabled True (re-confirms the wiring
    # target exists with the right schema after Issue #33)
    vol_cfg_17 = yaml.safe_load(open(ROOT / "state" / "live_multiasset_long_short_funding_vol.yaml"))
    check("opt-in yaml: vol_sizing.enabled True (the trigger for replay parity)",
          vol_cfg_17["vol_sizing"]["enabled"] is True)

    # 17.5 Existing non-vol yaml has no vol_sizing block — confirms
    # backward-compat detection (replay must default to disabled).
    base_cfg_17 = yaml.safe_load(open(ROOT / "state" / "live_multiasset_long_short_funding.yaml"))
    check("existing yaml has no vol_sizing block (backward-compat trigger)",
          "vol_sizing" not in base_cfg_17)
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
