"""24/7 reliability loop — v2 long-short signal engine, live.

Every poll cycle: pull OHLCV via the price adapter at the strategy's
configured timeframe, compute the v2 signal engine's indicators
(``signals.compute_indicators``), evaluate the current ``strategy.yaml``'s
long + short setups, take a paper trade when an entry condition fires,
manage the open position (long_exit / short_exit), log closed outcomes,
and write a heartbeat. Schema drift in an adapter halts the loop.

The live worker uses the SAME engine the backtester runs — so live behaviour
matches the backtest result that justified the migration.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone

import pandas as pd

from . import STATE_DIR, append_jsonl, load_yaml, log, now_iso, read_jsonl, write_json
from . import display as display_mod
from . import reflect as reflect_mod
from . import signals
from .adapters import SchemaError, validate
from .adapters import macro as macro_adapter
from .adapters import news as news_adapter
from .adapters import onchain as onchain_adapter
from .adapters import price as price_adapter
from .markov_regime import MarkovRegimeModel

POLL_SECONDS = int(os.getenv("HERMES_POLL_SECONDS", "10"))
RETRY_ATTEMPTS = 3
CIRCUIT_BREAK_AFTER = 5
CONTEXT_EVERY_SECONDS = int(os.getenv("HERMES_CONTEXT_SECONDS", "300"))
REGIME_EVERY_SECONDS = int(os.getenv("HERMES_REGIME_SECONDS", "300"))
PAPER_NOTIONAL_USD = float(os.getenv("HERMES_PAPER_NOTIONAL_USD", "1000"))

# Seconds per bar by timeframe (for converting wall-clock into bars_held).
_TIMEFRAME_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


async def _with_retries(coro_factory, name: str, attempts: int = RETRY_ATTEMPTS):
    delay = 1.0
    last_exc: Exception | None = None
    for _ in range(attempts):
        try:
            payload = await coro_factory()
            return validate(name, payload)
        except SchemaError:
            raise  # never retry a schema mismatch — halt instead
        except Exception as exc:  # noqa: BLE001 — transient network errors
            last_exc = exc
            await asyncio.sleep(delay)
            delay *= 2
    raise last_exc  # type: ignore[misc]


async def _context(asset: str) -> dict:
    """Best-effort enrichment. Network errors tolerated; schema drift halts."""
    ctx: dict = {}
    for name, factory in (
        ("onchain", onchain_adapter.fetch),
        ("news", news_adapter.fetch),
        ("macro", macro_adapter.fetch),
    ):
        try:
            ctx[name] = await _with_retries(factory, name)
        except SchemaError:
            raise
        except Exception as exc:  # noqa: BLE001
            log(f"[yellow]context {name} unavailable:[/yellow] {exc}")
    return ctx


def _closed_count() -> int:
    return sum(
        1 for t in read_jsonl(STATE_DIR / "trades.jsonl") if t.get("status") == "closed"
    )


def _read_reflection_marker() -> int | None:
    try:
        with open(STATE_DIR / "reflect_state.json") as fh:
            return int(json.load(fh)["closed_at_last_reflection"])
    except Exception:  # noqa: BLE001 — missing or corrupt
        return None


def _write_reflection_marker(n: int) -> None:
    write_json(STATE_DIR / "reflect_state.json", {"closed_at_last_reflection": n})


def _save_position(pos: dict) -> None:
    write_json(STATE_DIR / "position.json", pos)


def _load_position() -> dict | None:
    """Restore an open position from disk. Positions written by the legacy
    v1 RSI worker have no ``direction`` field and are not compatible with
    the v2 long-short engine; those are abandoned with a warning."""
    try:
        with open(STATE_DIR / "position.json") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001
        return None
    if not data or "entry_price" not in data:
        return None
    if "direction" not in data:
        log("[yellow]old-schema (v1 RSI) position on disk — abandoning to start clean with v2[/yellow]")
        _clear_position()
        return None
    return data


def _clear_position() -> None:
    try:
        (STATE_DIR / "position.json").unlink()
    except FileNotFoundError:
        pass


def _init_markov_live():
    """Optional Markov filter. Wired but disabled by default — the walk-forward
    proved it doesn't help OOS (PF 0.65 -> 0.60 with markov on 1h, 1.25 -> 0.89
    on 4h). Kept for experiments; flip on via HERMES_MARKOV_ENABLE=1."""
    cfg_path = STATE_DIR / "markov_regime.yaml"
    if not cfg_path.exists():
        return None
    cfg = load_yaml(cfg_path)
    if not (cfg.get("enabled") or os.getenv("HERMES_MARKOV_ENABLE") == "1"):
        return None
    cfg["enabled"] = True
    model = MarkovRegimeModel(cfg)
    try:
        from . import data as data_mod
        df_hist = data_mod.resample(data_mod.load_klines("BTCUSDT", n_months=24), "1h")
        model.fit(df_hist)
        log(f"[magenta]markov live filter ON — fitted on {len(df_hist)} cached 1h bars[/magenta]")
    except Exception as exc:  # noqa: BLE001
        log(f"[yellow]markov fit failed ({exc}); live filter not active[/yellow]")
        return None
    allowed_set = set(cfg.get("allowed_long_states", []))
    min_prob = float(cfg.get("min_prob_same_or_up", 0.5))
    return model, allowed_set, min_prob


async def _trigger_reflection() -> None:
    """Run one reflection cycle (Hermes mode; deterministic fallback inside).
    Offloaded to a thread so the blocking LLM call doesn't stall the loop, and
    guarded so a reflection failure can never kill the worker."""
    log("[magenta]reflection trigger — reflecting on recent trades[/magenta]")
    try:
        record = await asyncio.to_thread(reflect_mod.reflect, "hermes")
        if record:
            log(
                f"[magenta]reflection ({record['mode']}): "
                f"v{record['from_version']}->v{record['to_version']} "
                f"{record['variable']}={record['to']}[/magenta]"
            )
    except Exception as exc:  # noqa: BLE001
        log(f"[yellow]reflection failed (continuing): {exc}[/yellow]")


def _ohlcv_to_df(ohlcv: list) -> pd.DataFrame:
    """ccxt OHLCV rows ([ts_ms, o, h, l, c, v]) -> DatetimeIndex DataFrame."""
    df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df[["open", "high", "low", "close", "volume"]]


def _bars_held(position: dict, timeframe: str) -> int:
    sec = _TIMEFRAME_SECONDS.get(timeframe, 3600)
    try:
        opened = datetime.fromisoformat(position["opened_at"])
    except Exception:  # noqa: BLE001
        return 0
    return int((datetime.now(timezone.utc) - opened).total_seconds() / sec)


async def run(asset: str, verbose: bool = False) -> None:
    log(f"Booting hermes-trading worker — asset={asset}")
    goal = load_yaml(STATE_DIR / "goal.yaml")
    timeframe = goal.get("timeframe", "4h")
    indicator_limit = int(goal.get("indicator_limit", 300))
    reflection_every = int(goal.get("reflection_every", 5) or 5)

    closed_total = _closed_count()
    last_reflected_at = _read_reflection_marker()
    if last_reflected_at is None:
        last_reflected_at = (closed_total // reflection_every) * reflection_every
        _write_reflection_marker(last_reflected_at)
    log(
        f"timeframe={timeframe} | indicator_limit={indicator_limit} | "
        f"reflection every {reflection_every} closed trades "
        f"({closed_total} closed so far, next at {last_reflected_at + reflection_every})"
    )

    position: dict | None = _load_position()
    if position is not None:
        log(
            f"[green]restored open position[/green] from disk: "
            f"{position.get('direction','?')} {position.get('setup','')} @ {position['entry_price']:.2f}"
        )

    consecutive_failures = 0
    last_context_at = 0.0
    markov = _init_markov_live()
    last_regime_at = 0.0
    regime_str = "regime=off" if markov is None else "regime=?"
    regime_allowed_long = True  # permissive when markov is off

    while True:
        try:
            strategy = load_yaml(STATE_DIR / "strategy.yaml")

            # Fetch OHLCV at the strategy's decision timeframe.
            price = await _with_retries(
                lambda: price_adapter.fetch(asset, timeframe=timeframe, limit=indicator_limit),
                "price",
            )
            if time.monotonic() - last_context_at >= CONTEXT_EVERY_SECONDS:
                last_context_at = time.monotonic()
                await _context(asset)

            if markov is not None and time.monotonic() - last_regime_at >= REGIME_EVERY_SECONDS:
                try:
                    model, allowed_set, min_prob = markov
                    h = await _with_retries(
                        lambda: price_adapter.fetch(asset, timeframe="1h", limit=300), "price"
                    )
                    hdf = pd.DataFrame({"close": h["closes"]})
                    cs = model.current_state(hdf)
                    next_probs = model.next_state_probabilities(hdf) if cs else {}
                    score = float(sum(p for s, p in next_probs.items() if s in allowed_set))
                    regime_allowed_long = bool(cs and cs in allowed_set and score >= min_prob)
                    regime_str = f"regime={cs or '?'} P={score:.2f} {'OK' if regime_allowed_long else 'NO'}"
                    last_regime_at = time.monotonic()
                except Exception as exc:  # noqa: BLE001
                    log(f"[yellow]regime classification failed:[/yellow] {exc}")

            # Build indicator-augmented frame from live OHLCV.
            ohlcv = price.get("ohlcv") or []
            if len(ohlcv) < 50:
                raise RuntimeError(f"insufficient OHLCV ({len(ohlcv)} bars)")
            df = _ohlcv_to_df(ohlcv)
            ind_df = signals.compute_indicators(df, strategy)
            row = ind_df.iloc[-1].to_dict()
            last = float(row["close"])
            rsi_val = float(row["rsi"]) if pd.notna(row.get("rsi")) else None
            consecutive_failures = 0

            if position is None:
                fired_dir = None
                # Long entry — Markov gate (when on) applies to longs only.
                if regime_allowed_long:
                    setup_l = signals.long_entry(row, strategy)
                    if setup_l:
                        position = {
                            "entry_price": last,
                            "opened_at": now_iso(),
                            "size": float(strategy["risk"].get("position_size_r", 0.5)),
                            "direction": "long",
                            "setup": setup_l,
                            "stop": signals.initial_stop(row, setup_l, strategy),
                            "entry_rsi": rsi_val,
                            "entry_regime": regime_str,
                        }
                        fired_dir = "long"
                if fired_dir is None:
                    setup_s = signals.short_entry(row, strategy)
                    if setup_s:
                        position = {
                            "entry_price": last,
                            "opened_at": now_iso(),
                            "size": float(strategy["risk"].get("position_size_r", 0.5)),
                            "direction": "short",
                            "setup": setup_s,
                            "stop": signals.initial_stop_short(row, setup_s, strategy),
                            "entry_rsi": rsi_val,
                            "entry_regime": regime_str,
                        }
                        fired_dir = "short"
                if fired_dir:
                    _save_position(position)
                    color = "green" if fired_dir == "long" else "red"
                    rsi_str_e = f"{rsi_val:.1f}" if rsi_val is not None else "n/a"
                    log(
                        f"[{color}]ENTER {fired_dir}[/{color}] {asset} @ {last:.2f} "
                        f"({position['setup']}, rsi={rsi_str_e}, stop={position['stop']:.2f})"
                    )
            else:
                direction = position.get("direction", "long")
                bars_held = _bars_held(position, timeframe)
                if direction == "long":
                    reason = signals.long_exit(row, position, strategy, bars_held)
                else:
                    reason = signals.short_exit(row, position, strategy, bars_held)
                if reason:
                    if direction == "long":
                        ret_pct = (last - position["entry_price"]) / position["entry_price"]
                    else:
                        ret_pct = (position["entry_price"] - last) / position["entry_price"]
                    trade_return = ret_pct * position["size"]
                    closed_at_iso = now_iso()
                    trade = {
                        "status": "closed",
                        "asset": asset,
                        "direction": direction,
                        "setup": position.get("setup"),
                        # legacy field names — preserved for backward compatibility
                        "opened_at": position["opened_at"],
                        "closed_at": closed_at_iso,
                        "entry_price": position["entry_price"],
                        "exit_price": last,
                        "return": trade_return,
                        "exit_reason": reason,
                        "strategy_version": strategy.get("version"),
                        # new spec field names (Issue #16)
                        "entry_time": position["opened_at"],
                        "exit_time": closed_at_iso,
                        "return_pct": ret_pct,
                        "net_return_pct": trade_return,
                        "position_size": float(position.get("size", 1.0)),
                        "holding_bars": bars_held,
                    }
                    append_jsonl(STATE_DIR / "trades.jsonl", trade)
                    log(
                        f"[cyan]EXIT {direction}[/cyan] {asset} @ {last:.2f} "
                        f"({reason}, return={trade_return:+.4f})"
                    )
                    position = None
                    _clear_position()
                    closed_total += 1
                    if closed_total - last_reflected_at >= reflection_every:
                        last_reflected_at = closed_total
                        _write_reflection_marker(last_reflected_at)
                        await _trigger_reflection()

            heartbeat = {
                "ts": now_iso(),
                "asset": asset,
                "timeframe": timeframe,
                "strategy_version": strategy.get("version"),
                "last_price": last,
                "rsi": rsi_val,
                "position_open": position is not None,
                "position_direction": position.get("direction") if position else None,
            }
            # SuperTrend-specific heartbeat fields (Issue #17). Always
            # populated when the indicator columns exist on the row;
            # values are None during indicator warmup.
            heartbeat.update(display_mod.supertrend_heartbeat_fields(
                last,
                row.get("supertrend_direction"),
                row.get("supertrend_line"),
            ))
            # Issue #18: small bullish-regime flag for dashboards.
            ef = row.get("ema_fast")
            es = row.get("ema_slow")
            try:
                heartbeat["bullish_regime"] = (
                    ef is not None and es is not None
                    and not pd.isna(ef) and not pd.isna(es)
                    and float(ef) > float(es)
                )
            except (TypeError, ValueError):
                heartbeat["bullish_regime"] = None
            write_json(STATE_DIR / "heartbeat.json", heartbeat)

            # ---- per-tick log line (Issue #17: auto-detect display) ----
            if display_mod.is_supertrend_active(strategy):
                log(display_mod.format_supertrend_tick(
                    asset=asset,
                    close=last,
                    supertrend_direction=row.get("supertrend_direction"),
                    supertrend_line=row.get("supertrend_line"),
                    strategy_version=strategy.get("version"),
                    position=position,
                    rsi=rsi_val,
                    verbose=verbose,
                ))
                # Issue #18: verbose "why no trade" diagnostic. Only
                # emitted in verbose mode; never in default output.
                if verbose:
                    diag = display_mod.diagnose_entry_blockers(
                        row=row,
                        strategy=strategy,
                        position=position,
                        portfolio_open=1 if position is not None else 0,
                        max_open=1,
                    )
                    for line in display_mod.format_entry_diagnostic_lines(diag):
                        log(line)
            else:
                # legacy v2 RSI display preserved verbatim — including
                # color tags and the regime suffix.
                last_str = f"{last:.2f}"
                rsi_str = f"{rsi_val:.1f}" if rsi_val is not None else "n/a"
                if position is not None:
                    direction = position.get("direction", "long")
                    if direction == "long":
                        chg = (last - position["entry_price"]) / position["entry_price"]
                        arrow = "↑"
                    else:
                        chg = (position["entry_price"] - last) / position["entry_price"]
                        arrow = "↓"
                    pnl_ret = chg * position["size"]
                    pnl_usd = pnl_ret * PAPER_NOTIONAL_USD
                    color = "green" if pnl_ret >= 0 else "red"
                    pos_str = (
                        f"{arrow}{direction} @ {position['entry_price']:.2f} "
                        f"[{color}]{pnl_ret * 100:+.3f}%  ${pnl_usd:+.2f}[/{color}]"
                    )
                else:
                    pos_str = "flat"
                log(
                    f"tick {asset} {last_str} rsi={rsi_str} v{strategy.get('version')} "
                    f"pos={pos_str} {regime_str}"
                )

        except SchemaError as exc:
            log(f"[red]SCHEMA DRIFT — halting:[/red] {exc}")
            raise
        except Exception as exc:  # noqa: BLE001
            consecutive_failures += 1
            log(f"[yellow]cycle failed ({consecutive_failures}/{CIRCUIT_BREAK_AFTER}):[/yellow] {exc}")
            if consecutive_failures >= CIRCUIT_BREAK_AFTER:
                log("[red]CIRCUIT BREAK — too many consecutive failures, halting.[/red]")
                raise

        await asyncio.sleep(POLL_SECONDS)
