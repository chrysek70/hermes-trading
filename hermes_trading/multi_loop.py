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
from . import display as display_mod
from . import funding as funding_mod
from . import positions
from . import signals
from .adapters import SchemaError, price as price_adapter

POLL_DEFAULT = int(os.getenv("HERMES_POLL_SECONDS", "10"))
PAPER_NOTIONAL_USD = float(os.getenv("HERMES_PAPER_NOTIONAL_USD", "1000"))

_TIMEFRAME_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


# ---------- funding overlay (Issue #21) -------------------------------------

def evaluate_funding_gate(
    direction: str,
    percentile: float | None,
    block_long_above_pct: float = 95.0,
    block_short_below_pct: float = 5.0,
    on_missing_data: str = "fail_open",
) -> dict:
    """Pure-function direction-aware funding gate.

    Returns a dict with:
      allow:   bool  — True means the trade may proceed
      decision: str  — "allow" / "block_long" / "block_short" / "missing_data" / "missing_data_blocked"
      reason:  str   — short human-readable explanation

    ``on_missing_data`` is ``"fail_open"`` (default; allow + warn) or
    ``"fail_closed"`` (block + warn). The hard rule in Issue #21 is fail
    open by default.
    """
    if percentile is None:
        if on_missing_data == "fail_closed":
            return {"allow": False, "decision": "missing_data_blocked",
                    "reason": "funding data missing; fail_closed"}
        return {"allow": True, "decision": "missing_data",
                "reason": "funding data missing; fail_open"}
    if direction == "long":
        if percentile >= block_long_above_pct:
            return {"allow": False, "decision": "block_long",
                    "reason": (f"extreme_positive_funding "
                               f"(pct {percentile:.1f} >= {block_long_above_pct:.1f})")}
        return {"allow": True, "decision": "allow",
                "reason": (f"below long block threshold "
                           f"(pct {percentile:.1f} < {block_long_above_pct:.1f})")}
    if direction == "short":
        if percentile <= block_short_below_pct:
            return {"allow": False, "decision": "block_short",
                    "reason": (f"extreme_negative_funding "
                               f"(pct {percentile:.1f} <= {block_short_below_pct:.1f})")}
        return {"allow": True, "decision": "allow",
                "reason": (f"above short block threshold "
                           f"(pct {percentile:.1f} > {block_short_below_pct:.1f})")}
    raise ValueError(f"unknown direction: {direction!r}")


class LiveFundingOverlay:
    """Per-asset funding rate + rolling percentile, lookup-by-timestamp.

    Loads once at worker boot via the same ``hermes_trading.funding``
    loader used by the research scripts. The rolling percentile is
    computed across the full available history (Binance Vision starts
    2020-01 — comfortably long for the 180-bar window).

    On per-tick lookup:
      - Find the most recent funding observation <= ts.
      - Return its raw rate and rolling-percentile value.
      - If nothing is available (data not loaded, or ts before
        earliest), return ``available=False``.
    """

    def __init__(self,
                 assets: list[str],
                 percentile_window_bars: int = 180,
                 n_months_history: int = 48,
                 timeframe: str = "4h"):
        self.assets = assets
        self.timeframe = timeframe
        self.percentile_window_bars = int(percentile_window_bars)
        self.rates: dict[str, "pd.Series | None"] = {}
        self.percentiles: dict[str, "pd.Series | None"] = {}
        for asset in assets:
            sym = asset.replace("/", "")
            try:
                f = funding_mod.load_funding(sym, n_months=n_months_history)
                rate_series = f["funding_rate"]
                # rolling percentile on the 8h funding cadence
                pct = funding_mod.rolling_percentile(
                    rate_series, window=percentile_window_bars,
                )
                self.rates[asset] = rate_series
                self.percentiles[asset] = pct
                log(f"  funding overlay loaded for {asset}: "
                    f"{len(rate_series)} records, "
                    f"span {rate_series.index[0].date()} -> {rate_series.index[-1].date()}")
            except Exception as exc:  # noqa: BLE001
                log(f"[yellow]funding overlay unavailable for {asset}: "
                    f"{exc}; gate will fail open[/yellow]")
                self.rates[asset] = None
                self.percentiles[asset] = None

    def state_at(self, asset: str, ts) -> dict:
        """Return a dict with rate, percentile, available. ``ts`` is a
        timezone-aware datetime / pandas.Timestamp."""
        rates = self.rates.get(asset)
        pcts = self.percentiles.get(asset)
        if rates is None or pcts is None:
            return {"available": False, "rate": None, "percentile": None}
        try:
            ts_pd = pd.Timestamp(ts)
            if ts_pd.tzinfo is None:
                ts_pd = ts_pd.tz_localize("UTC")
            window = rates.loc[:ts_pd]
            if window.empty:
                return {"available": False, "rate": None, "percentile": None}
            last_rate = float(window.iloc[-1])
            last_pct_window = pcts.loc[:ts_pd]
            last_pct = (float(last_pct_window.iloc[-1])
                        if not last_pct_window.empty and not pd.isna(last_pct_window.iloc[-1])
                        else None)
            if last_pct is None:
                return {"available": False, "rate": last_rate, "percentile": None}
            return {"available": True, "rate": last_rate, "percentile": last_pct}
        except Exception as exc:  # noqa: BLE001
            log(f"[yellow]funding lookup failed for {asset}: {exc}[/yellow]")
            return {"available": False, "rate": None, "percentile": None}


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


async def run(
    cfg_path: str | Path,
    state_dir: Path | None = None,
    verbose: bool = False,
) -> None:
    """Run the multi-asset paper worker indefinitely.

    ``cfg_path`` is the multi-asset yaml (e.g. ``state/live_multiasset.yaml``).
    ``verbose`` adds extra debug fields (RSI) to the SuperTrend tick lines.
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

    # ---- funding overlay (Issue #21) ----
    funding_cfg = (cfg.get("funding_filter") or {})
    funding_enabled = bool(funding_cfg.get("enabled"))
    funding_block_long = float(funding_cfg.get("block_long_above_pct", 95.0))
    funding_block_short = float(funding_cfg.get("block_short_below_pct", 5.0))
    funding_window_bars = int(funding_cfg.get("percentile_window_bars", 180))
    funding_missing_policy = str(funding_cfg.get("on_missing_data", "fail_open"))
    funding_overlay: LiveFundingOverlay | None = None
    if funding_enabled:
        log(f"  funding_filter ENABLED  "
            f"long_block>={funding_block_long}  short_block<={funding_block_short}  "
            f"window={funding_window_bars} bars  missing={funding_missing_policy}")
        funding_overlay = LiveFundingOverlay(
            assets, percentile_window_bars=funding_window_bars,
            timeframe=timeframe,
        )
    else:
        log(f"  funding_filter disabled")

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
        asset_row_for_display: dict[str, dict] = {}
        st_mode = display_mod.is_supertrend_active(strategy)

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
            # Issue #24: split into display_row (current in-progress bar — for
            # tick line / heartbeat / intra-bar stop monitoring) and signal_row
            # (most recent CLOSED bar — for entry and SuperTrend flip / time
            # exit decisions). This makes live behaviour match backtest
            # bar-close semantics. signals.py is unchanged.
            display_row, signal_row = display_mod.split_display_and_signal_rows(ind_df)
            row = display_row  # legacy alias used below for heartbeat / display
            last_price = float(row["close"])
            rsi_val = float(row["rsi"]) if pd.notna(row.get("rsi")) else None

            position = positions_state[asset]
            # ---- per-bar funding state lookup (Issue #21) ----
            # Funding is keyed by the bar timestamp; use the SIGNAL bar so
            # the gate sees the same funding value the research backtest
            # would have seen for an entry on this signal.
            funding_state = None
            if funding_overlay is not None:
                funding_state = funding_overlay.state_at(asset, signal_row.get("ts"))

            funding_block_message: str | None = None

            if position is None:
                allowed, gate_reason = can_enter(asset, positions_state, max_open)
                opened = False
                if allowed:
                    # try long first — evaluated on signal_row (Issue #24)
                    setup_l = signals.long_entry(signal_row, strategy)
                    if setup_l:
                        if funding_overlay is not None:
                            gate = evaluate_funding_gate(
                                "long",
                                funding_state.get("percentile") if funding_state else None,
                                block_long_above_pct=funding_block_long,
                                block_short_below_pct=funding_block_short,
                                on_missing_data=funding_missing_policy,
                            )
                            if not gate["allow"]:
                                funding_block_message = (
                                    f"funding_filter {gate['decision']} "
                                    f"({gate['reason']})"
                                )
                                log(f"[yellow]BLOCK long {asset} @ {last_price:.2f} "
                                    f"({gate['reason']})[/yellow]")
                            elif gate["decision"] == "missing_data":
                                log(f"[yellow]funding data missing for {asset}; "
                                    f"long allowed by fail-open policy[/yellow]")
                            if gate["allow"]:
                                opened = True
                        else:
                            opened = True
                        if opened:
                            new_pos = {
                                "asset": asset,
                                "entry_price": last_price,
                                "opened_at": now_iso(),
                                "size": size_per_asset,
                                "direction": "long",
                                "setup": setup_l,
                                "stop": float(signals.initial_stop(signal_row, setup_l, strategy)),
                                "strategy_version": version,
                                "entry_rsi": rsi_val,
                                "funding_rate_at_entry": (
                                    funding_state.get("rate") if funding_state else None),
                                "funding_percentile_at_entry": (
                                    funding_state.get("percentile") if funding_state else None),
                            }
                            positions_state[asset] = new_pos
                            positions.save_position(asset, new_pos, state_dir)
                            log(f"[green]ENTER long[/green] {asset} @ {last_price:.2f} "
                                f"({setup_l}, stop={new_pos['stop']:.2f}, size={size_per_asset})")

                    if not opened and positions_state[asset] is None:
                        # short side also evaluated on signal_row (Issue #24)
                        setup_s = signals.short_entry(signal_row, strategy)
                        if setup_s:
                            short_ok = True
                            if funding_overlay is not None:
                                gate = evaluate_funding_gate(
                                    "short",
                                    funding_state.get("percentile") if funding_state else None,
                                    block_long_above_pct=funding_block_long,
                                    block_short_below_pct=funding_block_short,
                                    on_missing_data=funding_missing_policy,
                                )
                                if not gate["allow"]:
                                    short_ok = False
                                    funding_block_message = (
                                        f"funding_filter {gate['decision']} "
                                        f"({gate['reason']})"
                                    )
                                    log(f"[yellow]BLOCK short {asset} @ {last_price:.2f} "
                                        f"({gate['reason']})[/yellow]")
                                elif gate["decision"] == "missing_data":
                                    log(f"[yellow]funding data missing for {asset}; "
                                        f"short allowed by fail-open policy[/yellow]")
                            if short_ok:
                                new_pos = {
                                    "asset": asset,
                                    "entry_price": last_price,
                                    "opened_at": now_iso(),
                                    "size": size_per_asset,
                                    "direction": "short",
                                    "setup": setup_s,
                                    "stop": float(signals.initial_stop_short(signal_row, setup_s, strategy)),
                                    "strategy_version": version,
                                    "entry_rsi": rsi_val,
                                    "funding_rate_at_entry": (
                                        funding_state.get("rate") if funding_state else None),
                                    "funding_percentile_at_entry": (
                                        funding_state.get("percentile") if funding_state else None),
                                }
                                positions_state[asset] = new_pos
                                positions.save_position(asset, new_pos, state_dir)
                                log(f"[red]ENTER short[/red] {asset} @ {last_price:.2f} "
                                    f"({setup_s}, stop={new_pos['stop']:.2f}, size={size_per_asset})")
            else:
                bars_held = int((pd.Timestamp.now(tz="UTC") -
                                 pd.Timestamp(position["opened_at"])).total_seconds()
                                / sec_per_bar)
                direction = position.get("direction", "long")
                # Issue #24: SuperTrend flip / regime-flip / time / trail
                # exits use the CLOSED signal_row. signals.long_exit also
                # ratchets position["stop"] from the closed bar's
                # supertrend_line — that's the correct, stable value.
                if direction == "long":
                    reason = signals.long_exit(signal_row, position, strategy, bars_held)
                else:
                    reason = signals.short_exit(signal_row, position, strategy, bars_held)
                # Intra-bar stop reactivity: if the running display_row low/
                # high pierces the (possibly just-ratcheted) stop, exit
                # immediately. signals.long_exit already checks the closed
                # bar's low; this catches the new low/high inside the
                # current in-progress bar so paper stops stay responsive.
                if reason is None:
                    if direction == "long":
                        dlow = display_row.get("low")
                        if dlow is not None and not pd.isna(dlow) and float(dlow) <= position["stop"]:
                            reason = "stop"
                    else:
                        dhigh = display_row.get("high")
                        if dhigh is not None and not pd.isna(dhigh) and float(dhigh) >= position["stop"]:
                            reason = "stop"
                if reason:
                    trade = build_trade_row(asset, position, last_price,
                                            reason, bars_held, version)
                    # carry funding-at-entry metadata onto the closed-trade row
                    if "funding_rate_at_entry" in position:
                        trade["funding_rate_at_entry"] = position["funding_rate_at_entry"]
                        trade["funding_percentile_at_entry"] = position.get("funding_percentile_at_entry")
                    append_jsonl(state_dir / "trades.jsonl", trade)
                    log(f"[cyan]EXIT {direction}[/cyan] {asset} @ {last_price:.2f} "
                        f"({reason}, return={trade['net_return_pct']:+.4f})")
                    realized_pnl_pct += trade["net_return_pct"] * 100.0
                    positions_state[asset] = None
                    positions.clear_position(asset, state_dir)

            cur_pos = positions_state[asset]
            unrl = 0.0
            if cur_pos is not None:
                unrl = (last_price - cur_pos["entry_price"]) / cur_pos["entry_price"] * cur_pos["size"]
            asset_hb = {
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
            # SuperTrend-specific heartbeat fields (Issue #17). Always
            # populated when the indicator columns exist on the row;
            # values are None during indicator warmup.
            asset_hb.update(display_mod.supertrend_heartbeat_fields(
                last_price,
                row.get("supertrend_direction"),
                row.get("supertrend_line"),
            ))
            # Issue #18: lightweight bullish-regime flag for dashboards
            ef = row.get("ema_fast")
            es = row.get("ema_slow")
            try:
                bullish_regime_ok = (
                    ef is not None and es is not None
                    and not pd.isna(ef) and not pd.isna(es)
                    and float(ef) > float(es)
                )
            except (TypeError, ValueError):
                bullish_regime_ok = None
            asset_hb["bullish_regime"] = bullish_regime_ok
            # Issue #21: funding overlay heartbeat block
            asset_hb["funding_filter_enabled"] = funding_enabled
            if funding_overlay is not None:
                if funding_state and funding_state.get("available"):
                    rate = funding_state.get("rate")
                    pct = funding_state.get("percentile")
                    # The eventual entry direction is unknown at heartbeat
                    # time (depends on whether a long or short signal would
                    # fire). For the heartbeat we report the gate decision
                    # FOR THE CURRENT ABSOLUTE-FUNDING EXTREMES — i.e. what
                    # would be blocked if a signal fired right now.
                    if pct is not None and pct >= funding_block_long:
                        decision = "block_long"
                        reason = (f"extreme_positive_funding "
                                  f"(pct {pct:.1f} >= {funding_block_long:.1f})")
                    elif pct is not None and pct <= funding_block_short:
                        decision = "block_short"
                        reason = (f"extreme_negative_funding "
                                  f"(pct {pct:.1f} <= {funding_block_short:.1f})")
                    else:
                        decision = "allow"
                        reason = "below long block threshold"
                    asset_hb["funding_rate"] = rate
                    asset_hb["funding_percentile"] = pct
                    asset_hb["funding_decision"] = decision
                    asset_hb["funding_reason"] = reason
                else:
                    asset_hb["funding_rate"] = None
                    asset_hb["funding_percentile"] = None
                    asset_hb["funding_decision"] = "missing_data"
                    asset_hb["funding_reason"] = ("funding data unavailable; "
                                                  "fail-open policy")
            per_asset_hb[asset] = asset_hb
            asset_row_for_display[asset] = {
                "close": last_price,
                "rsi": rsi_val,
                "supertrend_direction": row.get("supertrend_direction"),
                "supertrend_direction_prev": row.get("supertrend_direction_prev"),
                "supertrend_line": row.get("supertrend_line"),
                "ema_fast": ef,
                "ema_slow": es,
                "position": cur_pos,
                "raw_row": row,           # display_row — live indicator state
                "signal_row": signal_row, # Issue #24 — closed bar driving decisions
                "funding_state": funding_state,
                "funding_blocked_message": funding_block_message,
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

        # ---- per-tick output ----
        if st_mode:
            # SuperTrend mode: one line per asset showing SuperTrend
            # status (Issue #17). When --verbose, each flat asset gets
            # additional "why no trade" diagnostic lines (Issue #18).
            for a in assets:
                disp = asset_row_for_display.get(a)
                if disp is None:
                    continue
                log(display_mod.format_supertrend_tick(
                    asset=a,
                    close=disp["close"],
                    supertrend_direction=disp["supertrend_direction"],
                    supertrend_line=disp["supertrend_line"],
                    strategy_version=version,
                    position=disp["position"],
                    rsi=disp.get("rsi"),
                    verbose=verbose,
                ))
                if verbose:
                    # Issue #24: diagnostic reflects the SIGNAL bar — that's
                    # the bar entry decisions are evaluated on.
                    diag = display_mod.diagnose_entry_blockers(
                        row=disp.get("signal_row") or disp["raw_row"],
                        strategy=strategy,
                        position=disp["position"],
                        portfolio_open=open_count,
                        max_open=max_open,
                    )
                    for line in display_mod.format_entry_diagnostic_lines(diag):
                        log(line)
                    # Surface the two bar timestamps so it's obvious which
                    # bar each piece is reading.
                    sb_ts = (disp.get("signal_row") or {}).get("ts")
                    db_ts = disp["raw_row"].get("ts")
                    if sb_ts is not None and db_ts is not None:
                        log(f"  signal_bar={sb_ts}  display_bar={db_ts}")
                    # Issue #21 — funding overlay verbose line
                    if funding_overlay is not None:
                        fs = disp.get("funding_state")
                        if fs and fs.get("available"):
                            rate = fs.get("rate")
                            pct = fs.get("percentile")
                            # mirror the heartbeat decision
                            if pct is not None and pct >= funding_block_long:
                                fdec = "block_long"
                            elif pct is not None and pct <= funding_block_short:
                                fdec = "block_short"
                            else:
                                fdec = "allow"
                            log(f"  funding: rate={rate*100:+.4f}% "
                                f"pct={pct:.1f} decision={fdec}")
                        else:
                            log(f"  funding: data unavailable; fail-open")
                    if disp.get("funding_blocked_message"):
                        log(f"  blocked_by: {disp['funding_blocked_message']}")
            log(f"portfolio open={open_count}/{max_open}  "
                f"realized={realized_pnl_pct:+.3f}%  "
                f"unrealized={unrl_portfolio*100:+.3f}%")
        else:
            # legacy condensed summary — unchanged for v2 strategies
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
