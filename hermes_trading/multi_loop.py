"""Multi-asset live (paper) worker.

Mirrors the single-asset ``loop.run`` semantics but iterates over an
asset list each tick:

  - per-asset OHLCV fetch (sequential — paper mode, 2-5 assets, latency
    is not a constraint)
  - per-asset indicator computation
  - per-asset entry / exit using the same ``signals`` module the
    backtester uses
  - portfolio-level position cap (`max_open_positions`)
  - per-asset position-state file (`state/positions/<KEY>.json`)
  - per-asset circuit breaker — a failing asset is skipped without
    stopping the others; the worker only halts if every asset trips
  - portfolio heartbeat at `state/heartbeat.json`

Reflection (auto-tuning of the strategy yaml) is intentionally
**disabled** in multi-asset mode. Reflection's allowlist was designed
for single-asset v2 long-short keys, and its interaction with a
shared per-asset strategy file is untested.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pandas as pd

from . import STATE_DIR, append_jsonl, load_yaml, log, now_iso, write_json
from . import positions
from . import signals
from .adapters import SchemaError, price as price_adapter

POLL_DEFAULT = int(os.getenv("HERMES_POLL_SECONDS", "10"))
PAPER_NOTIONAL_USD = float(os.getenv("HERMES_PAPER_NOTIONAL_USD", "1000"))

_TIMEFRAME_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


# ---------- pure helpers (used by both run loop and self-test) --------------

def build_trade_row(
    asset: str,
    position: dict,
    exit_price: float,
    exit_reason: str,
    bars_held: int,
    strategy_version: str | None,
) -> dict:
    """Construct a closed-trade record. Includes both the new spec
    field names (entry_time / exit_time / return_pct / net_return_pct /
    position_size / holding_bars) and the legacy field names
    (opened_at / closed_at / return) for backward compatibility with
    existing readers like ``scripts/monitor_strategy_decay.py``."""
    entry_price = float(position["entry_price"])
    direction = position.get("direction", "long")
    size = float(position.get("size", 1.0))
    if direction == "long":
        ret_pct = (exit_price - entry_price) / entry_price
    else:
        ret_pct = (entry_price - exit_price) / entry_price
    net = ret_pct * size
    now = now_iso()
    return {
        "status": "closed",
        "asset": asset,
        "direction": direction,
        "setup": position.get("setup"),
        # legacy names (kept for backward compat)
        "opened_at": position["opened_at"],
        "closed_at": now,
        "return": net,
        # new spec names (Issue #16)
        "entry_time": position["opened_at"],
        "exit_time": now,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "return_pct": ret_pct,
        "net_return_pct": net,
        "position_size": size,
        "exit_reason": exit_reason,
        "holding_bars": bars_held,
        "strategy_version": strategy_version,
    }


def can_enter(
    asset: str,
    positions_by_asset: dict[str, dict | None],
    max_open_positions: int,
) -> tuple[bool, str]:
    """Portfolio gate. Returns (allowed, reason)."""
    if positions_by_asset.get(asset) is not None:
        return False, "asset_already_open"
    open_count = sum(1 for v in positions_by_asset.values() if v is not None)
    if open_count >= max_open_positions:
        return False, "portfolio_cap_reached"
    return True, "ok"


def evaluate_tick(
    asset: str,
    row: dict,
    strategy: dict,
    position: dict | None,
    positions_by_asset: dict[str, dict | None],
    max_open_positions: int,
    size_per_asset: float,
    strategy_version: str | None,
) -> tuple[dict | None, dict | None]:
    """Pure function. Returns (new_position_or_None, closed_trade_or_None).

    No I/O, no globals — used by the self-test to exercise the engine
    without mocking the price adapter."""
    if position is not None:
        bars_held = _bars_held(position, strategy_timeframe_seconds(strategy))
        direction = position.get("direction", "long")
        if direction == "long":
            reason = signals.long_exit(row, position, strategy, bars_held)
        else:
            reason = signals.short_exit(row, position, strategy, bars_held)
        if reason:
            exit_price = float(row["close"])
            return None, build_trade_row(asset, position, exit_price,
                                         reason, bars_held, strategy_version)
        return position, None

    # flat: try entry, respecting the portfolio cap
    allowed, _ = can_enter(asset, positions_by_asset, max_open_positions)
    if not allowed:
        return None, None
    setup = signals.long_entry(row, strategy)
    if not setup:
        return None, None
    new_pos = {
        "asset": asset,
        "entry_price": float(row["close"]),
        "opened_at": now_iso(),
        "size": float(size_per_asset),
        "direction": "long",
        "setup": setup,
        "stop": float(signals.initial_stop(row, setup, strategy)),
        "strategy_version": strategy_version,
        "entry_rsi": float(row["rsi"]) if pd.notna(row.get("rsi")) else None,
    }
    return new_pos, None


def strategy_timeframe_seconds(strategy: dict) -> int:
    """Best-effort: strategy yaml doesn't carry timeframe; the
    multi-asset config does. Default to 4h."""
    return _TIMEFRAME_SECONDS.get(strategy.get("_timeframe", "4h"), 14400)


def _bars_held(position: dict, sec_per_bar: int) -> int:
    from datetime import datetime, timezone
    try:
        opened = datetime.fromisoformat(position["opened_at"])
    except Exception:  # noqa: BLE001
        return 0
    return int((datetime.now(timezone.utc) - opened).total_seconds() / sec_per_bar)


def _ohlcv_to_df(ohlcv: list) -> pd.DataFrame:
    df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df[["open", "high", "low", "close", "volume"]]


# ---------- the orchestration loop ------------------------------------------

async def _with_retries(coro_factory, name: str, attempts: int = 3):
    delay = 1.0
    last_exc: Exception | None = None
    for _ in range(attempts):
        try:
            from .adapters import validate
            payload = await coro_factory()
            return validate(name, payload)
        except SchemaError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            await asyncio.sleep(delay)
            delay *= 2
    raise last_exc  # type: ignore[misc]


async def run(cfg_path: str | Path, state_dir: Path | None = None) -> None:
    """Run the multi-asset paper worker indefinitely.

    ``cfg_path`` is the multi-asset yaml (e.g. ``state/live_multiasset.yaml``).
    """
    state_dir = state_dir or STATE_DIR
    cfg_path = Path(cfg_path)
    cfg = load_yaml(cfg_path)
    assets: list[str] = list(cfg["assets"])
    timeframe: str = cfg.get("timeframe", "4h")
    indicator_limit = int(cfg.get("indicator_limit", 300))
    poll_seconds = int(cfg.get("poll_seconds", POLL_DEFAULT))
    max_open = int(cfg.get("max_open_positions", len(assets)))
    circuit_break_after = int(cfg.get("circuit_break_after", 5))
    strategy_path = cfg["strategy"]
    if not Path(strategy_path).is_absolute():
        strategy_path = state_dir.parent / strategy_path if str(strategy_path).startswith("state/") else state_dir / Path(strategy_path).name
    strategy_path = Path(strategy_path)
    size_per_asset = float(cfg.get("size_per_asset", 1.0 / max(len(assets), 1)))
    heartbeat_schema = cfg.get("heartbeat_schema", "multiasset-v1")
    sec_per_bar = _TIMEFRAME_SECONDS.get(timeframe, 14400)

    log(f"Booting hermes-trading MULTI-ASSET paper worker")
    log(f"  assets={assets}  timeframe={timeframe}  max_open={max_open}")
    log(f"  strategy={strategy_path}  size_per_asset={size_per_asset}")

    # One-shot legacy migration (state/position.json -> state/positions/<KEY>.json).
    # The legacy file's asset is inferred from goal.yaml; we don't try to read
    # the position dict's own asset field because old v1 entries didn't have one.
    legacy_asset = None
    goal_path = state_dir / "goal.yaml"
    if goal_path.exists():
        try:
            legacy_asset = load_yaml(goal_path).get("asset")
        except Exception:  # noqa: BLE001
            legacy_asset = None
    if legacy_asset is None:
        legacy_asset = assets[0]
    mig = positions.migrate_legacy_position(legacy_asset, state_dir)
    if mig.get("migrated"):
        log(f"  migrated legacy state/position.json -> {mig['new_path']}")
        log(f"  backup at {mig['backup_path']}")
    elif mig.get("reason") and mig["reason"] != "no legacy file":
        log(f"  legacy position not migrated: {mig['reason']}")

    positions_state: dict[str, dict | None] = {a: None for a in assets}
    restored = positions.load_positions(assets, state_dir)
    for a, p in restored.items():
        positions_state[a] = p
        log(f"  restored {a} position: {p.get('direction','?')} "
            f"{p.get('setup','')} @ {p['entry_price']:.2f}")

    consecutive_failures: dict[str, int] = {a: 0 for a in assets}
    broken: set[str] = set()
    realized_pnl_pct = 0.0

    while True:
        try:
            strategy = load_yaml(strategy_path)
            strategy["_timeframe"] = timeframe  # used by evaluate_tick's bars_held
        except Exception as exc:  # noqa: BLE001
            log(f"[red]could not load strategy {strategy_path}: {exc}; sleeping and retrying[/red]")
            await asyncio.sleep(poll_seconds)
            continue
        version = strategy.get("version", "?")

        per_asset_hb: dict[str, dict] = {}

        for asset in assets:
            if asset in broken:
                per_asset_hb[asset] = {
                    "last_price": None,
                    "position_open": positions_state[asset] is not None,
                    "strategy_version": version,
                    "broken": True,
                }
                continue

            try:
                price = await _with_retries(
                    lambda a=asset: price_adapter.fetch(a, timeframe=timeframe,
                                                        limit=indicator_limit),
                    "price",
                )
                ohlcv = price.get("ohlcv") or []
                if len(ohlcv) < 50:
                    raise RuntimeError(f"insufficient OHLCV ({len(ohlcv)} bars)")
                consecutive_failures[asset] = 0
            except SchemaError:
                raise
            except Exception as exc:  # noqa: BLE001
                consecutive_failures[asset] += 1
                log(f"[yellow]{asset}: fetch failed ({exc}); "
                    f"{consecutive_failures[asset]}/{circuit_break_after}[/yellow]")
                if consecutive_failures[asset] >= circuit_break_after:
                    log(f"[red]{asset}: circuit-broken — skipping for the rest of this session[/red]")
                    broken.add(asset)
                per_asset_hb[asset] = {
                    "last_price": None,
                    "position_open": positions_state[asset] is not None,
                    "strategy_version": version,
                    "error": str(exc),
                }
                continue

            df = _ohlcv_to_df(ohlcv)
            ind_df = signals.compute_indicators(df, strategy)
            row = ind_df.iloc[-1].to_dict()
            last_price = float(row["close"])
            rsi_val = float(row["rsi"]) if pd.notna(row.get("rsi")) else None

            position = positions_state[asset]
            if position is None:
                allowed, gate_reason = can_enter(asset, positions_state, max_open)
                if allowed:
                    setup = signals.long_entry(row, strategy)
                    if setup:
                        new_pos = {
                            "asset": asset,
                            "entry_price": last_price,
                            "opened_at": now_iso(),
                            "size": size_per_asset,
                            "direction": "long",
                            "setup": setup,
                            "stop": float(signals.initial_stop(row, setup, strategy)),
                            "strategy_version": version,
                            "entry_rsi": rsi_val,
                        }
                        positions_state[asset] = new_pos
                        positions.save_position(asset, new_pos, state_dir)
                        log(f"[green]ENTER long[/green] {asset} @ {last_price:.2f} "
                            f"({setup}, stop={new_pos['stop']:.2f}, size={size_per_asset})")
            else:
                bars_held = int((pd.Timestamp.now(tz="UTC") -
                                 pd.Timestamp(position["opened_at"])).total_seconds()
                                / sec_per_bar)
                reason = signals.long_exit(row, position, strategy, bars_held)
                if reason:
                    trade = build_trade_row(asset, position, last_price,
                                            reason, bars_held, version)
                    append_jsonl(state_dir / "trades.jsonl", trade)
                    log(f"[cyan]EXIT long[/cyan] {asset} @ {last_price:.2f} "
                        f"({reason}, return={trade['net_return_pct']:+.4f})")
                    realized_pnl_pct += trade["net_return_pct"] * 100.0
                    positions_state[asset] = None
                    positions.clear_position(asset, state_dir)

            cur_pos = positions_state[asset]
            unrl = 0.0
            if cur_pos is not None:
                unrl = (last_price - cur_pos["entry_price"]) / cur_pos["entry_price"] * cur_pos["size"]
            per_asset_hb[asset] = {
                "last_price": last_price,
                "rsi": rsi_val,
                "position_open": cur_pos is not None,
                "direction": cur_pos.get("direction") if cur_pos else None,
                "entry_price": cur_pos.get("entry_price") if cur_pos else None,
                "stop": cur_pos.get("stop") if cur_pos else None,
                "setup": cur_pos.get("setup") if cur_pos else None,
                "unrealized_pnl_pct": unrl,
                "strategy_version": version,
            }

        if assets and len(broken) == len(assets):
            log("[red]all assets circuit-broken; halting[/red]")
            return

        open_count = sum(1 for v in positions_state.values() if v is not None)
        unrl_portfolio = sum(
            (per_asset_hb[a].get("unrealized_pnl_pct") or 0.0) for a in per_asset_hb
        )
        write_json(state_dir / "heartbeat.json", {
            "ts": now_iso(),
            "mode": "multiasset",
            "schema": heartbeat_schema,
            "timeframe": timeframe,
            "assets": per_asset_hb,
            "portfolio": {
                "open_positions": open_count,
                "max_open_positions": max_open,
                "realized_pnl_pct": realized_pnl_pct,
                "unrealized_pnl_pct": unrl_portfolio * 100.0,
                "broken_assets": sorted(broken),
            },
        })

        # condensed per-tick log line
        bits = []
        for a in assets:
            hb = per_asset_hb.get(a, {})
            lp = hb.get("last_price")
            lp_str = f"{lp:.2f}" if isinstance(lp, (int, float)) else "—"
            posflag = "↑long" if hb.get("position_open") else "flat"
            bits.append(f"{a.split('/')[0]}:{lp_str}({posflag})")
        log(f"tick portfolio open={open_count}/{max_open} "
            f"realized={realized_pnl_pct:+.3f}%  "
            f"unrealized={unrl_portfolio*100:+.3f}%  "
            + "  ".join(bits))

        await asyncio.sleep(poll_seconds)
