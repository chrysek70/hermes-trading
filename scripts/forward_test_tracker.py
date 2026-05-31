"""Forward paper test tracker (Issue #40 fix has shipped).

Read-only. Reports the live-paper test against the milestones encoded
in ``state/forward_paper_test.json``:

  - count of closed trades since started_at
  - profit factor / win rate / max drawdown / return per exposure
  - count of blocked-trade events from the worker log (by reason)
  - latest heartbeat freshness + per-asset overlay state
  - milestone status (10 / 20 / 30 trades)
  - severe-failure tripwire check (PF < 0.8 once at >= 10 trades)
  - side-by-side comparison vs Issue #38 research expectations

This script does NOT mutate state. It is intended for the weekly
health-check cadence and for the eventual 30-trade forward report.

Usage:
    uv run python scripts/forward_test_tracker.py
    uv run python scripts/forward_test_tracker.py --self-test
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
MARKER_PATH = ROOT / "state" / "forward_paper_test.json"


@dataclass
class TradeStats:
    n: int
    n_winners: int
    n_losers: int
    win_rate: float
    pf: float
    total_return_pct: float
    max_dd_pct: float
    sum_abs_return: float
    ret_per_exposure_pct: float
    by_asset: dict
    by_direction: dict
    by_exit_reason: dict


def _parse_ts(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def load_marker(path: Path = MARKER_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"forward-test marker not found at {path}")
    return json.loads(path.read_text())


def load_trades_since(trade_log: Path, since: datetime) -> list[dict]:
    if not trade_log.exists():
        return []
    out: list[dict] = []
    for line in trade_log.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            t = json.loads(line)
        except json.JSONDecodeError:
            continue
        if t.get("status") != "closed":
            continue
        closed_at = t.get("closed_at") or t.get("opened_at")
        if not closed_at:
            continue
        if _parse_ts(closed_at) >= since:
            out.append(t)
    return out


def _compute_max_dd(returns: Iterable[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for r in returns:
        equity *= (1.0 + r)
        peak = max(peak, equity)
        if peak > 0:
            dd = (peak - equity) / peak
            worst = max(worst, dd)
    return worst


def compute_stats(trades: list[dict]) -> TradeStats:
    n = len(trades)
    if n == 0:
        return TradeStats(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                          {}, {}, {})
    returns = [float(t.get("return", 0.0)) for t in trades]
    winners = [r for r in returns if r > 0]
    losers = [r for r in returns if r <= 0]
    sum_win = sum(winners)
    sum_loss = -sum(losers)
    if sum_loss > 0:
        pf = sum_win / sum_loss
    elif sum_win > 0:
        pf = float("inf")
    else:
        pf = 0.0
    win_rate = (len(winners) / n) * 100.0
    total_return = 1.0
    for r in returns:
        total_return *= (1.0 + r)
    total_return_pct = (total_return - 1.0) * 100.0
    max_dd_pct = _compute_max_dd(returns) * 100.0
    sum_abs_return = sum(abs(r) for r in returns) * 100.0
    ret_per_exposure_pct = (sum(returns) * 100.0 / n) if n else 0.0

    by_asset: dict = {}
    by_direction: dict = {}
    by_exit_reason: dict = {}
    for t in trades:
        by_asset[t.get("asset", "?")] = by_asset.get(t.get("asset", "?"), 0) + 1
        by_direction[t.get("direction", "?")] = by_direction.get(
            t.get("direction", "?"), 0) + 1
        by_exit_reason[t.get("exit_reason", "?")] = by_exit_reason.get(
            t.get("exit_reason", "?"), 0) + 1

    return TradeStats(
        n=n,
        n_winners=len(winners),
        n_losers=len(losers),
        win_rate=win_rate,
        pf=pf,
        total_return_pct=total_return_pct,
        max_dd_pct=max_dd_pct,
        sum_abs_return=sum_abs_return,
        ret_per_exposure_pct=ret_per_exposure_pct,
        by_asset=by_asset,
        by_direction=by_direction,
        by_exit_reason=by_exit_reason,
    )


def count_blocks_in_log(log_path: Path, since: datetime) -> dict:
    """Count BLOCK lines emitted by multi_loop after ``since``.

    The worker log has lines like:
      [HH:MM:SS] BLOCK long BTC/USDT @ 73000.00 (volume_confirmation: ...)
      [HH:MM:SS] BLOCK short ETH/USDT @ 2000.00 (funding_filter ...)

    We don't strictly enforce the timestamp filter (the worker prefixes
    wall-clock time, not full ISO ts) — instead we look at all lines
    written AFTER the worker started (assumed to be at or after
    ``since``). This is good enough for the weekly check.
    """
    counts = {
        "funding_block_long": 0,
        "funding_block_short": 0,
        "volume_confirmation_block": 0,
        "funding_missing_data_block": 0,
        "other_block": 0,
        "total": 0,
    }
    if not log_path.exists():
        return counts
    text = log_path.read_text(errors="replace")
    for line in text.splitlines():
        if "BLOCK long" in line or "BLOCK short" in line:
            counts["total"] += 1
            lower = line.lower()
            if "volume_confirmation" in lower:
                counts["volume_confirmation_block"] += 1
            elif "funding_filter block_long" in lower or "block_long" in lower:
                counts["funding_block_long"] += 1
            elif "funding_filter block_short" in lower or "block_short" in lower:
                counts["funding_block_short"] += 1
            elif "missing_data" in lower:
                counts["funding_missing_data_block"] += 1
            else:
                counts["other_block"] += 1
    return counts


def load_heartbeat(state_dir: Path) -> dict | None:
    p = state_dir / "heartbeat.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def evaluate_tripwires(stats: TradeStats, marker: dict,
                        heartbeat: dict | None,
                        log_exists: bool) -> list[str]:
    fails: list[str] = []
    floor = marker.get("tripwires", {}).get(
        "pf_floor_at_or_after_10_trades", 0.8)
    if stats.n >= 10 and stats.pf < floor:
        fails.append(
            f"PF {stats.pf:.2f} < tripwire {floor:.2f} "
            f"at {stats.n} trades")

    if heartbeat is None:
        fails.append("no heartbeat file — worker may not be running")
    else:
        ts = heartbeat.get("ts")
        if ts:
            age = (datetime.now(timezone.utc) - _parse_ts(ts)).total_seconds()
            if age > 3600:
                fails.append(
                    f"heartbeat is stale: {age/60:.1f} minutes old")

    if not log_exists:
        fails.append(
            "worker log missing — block-trade tracking is degraded")

    return fails


def fmt_pf(pf: float) -> str:
    return "inf" if pf == float("inf") else f"{pf:.2f}"


def print_report(stats: TradeStats, marker: dict, hb: dict | None,
                  blocks: dict, tripwires: list[str]) -> None:
    started_at = marker["started_at"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print("=" * 72)
    print(f"FORWARD PAPER TEST  test_id={marker['test_id']}")
    print(f"  started_at:  {started_at}")
    print(f"  now:         {now}")
    print(f"  config:      {marker['config']}")
    print(f"  git_head:    {marker.get('git_head', '?')[:12]}")
    print("=" * 72)

    print()
    print("TRADE STATS (closed trades since started_at)")
    print(f"  trades                 {stats.n}")
    print(f"  win rate               {stats.win_rate:.1f}% "
          f"({stats.n_winners}W / {stats.n_losers}L)")
    print(f"  profit factor          {fmt_pf(stats.pf)}")
    print(f"  total return           {stats.total_return_pct:+.2f}%")
    print(f"  max drawdown           {stats.max_dd_pct:.2f}%")
    print(f"  return per exposure    {stats.ret_per_exposure_pct:+.2f}% (mean)")
    print(f"  by asset               {stats.by_asset}")
    print(f"  by direction           {stats.by_direction}")
    print(f"  by exit reason         {stats.by_exit_reason}")

    print()
    print("BLOCKED TRADES (from worker log)")
    print(f"  total                  {blocks['total']}")
    print(f"  funding (long)         {blocks['funding_block_long']}")
    print(f"  funding (short)        {blocks['funding_block_short']}")
    print(f"  volume_confirmation    {blocks['volume_confirmation_block']}")
    print(f"  funding missing_data   {blocks['funding_missing_data_block']}")
    print(f"  other                  {blocks['other_block']}")

    print()
    print("MILESTONE STATUS")
    for m in marker.get("milestones", [10, 20, 30]):
        if stats.n >= m:
            print(f"  [x] {m} trades reached")
        else:
            print(f"  [ ] {m} trades  ({stats.n}/{m}, "
                  f"{m - stats.n} to go)")

    print()
    print("RESEARCH EXPECTATIONS (Issue #38 walk-forward OOS)")
    exp = marker.get("research_expectations", {}).get("horizons", {})
    print(f"  {'horizon':<6}  {'trades':>7}  {'PF':>6}  "
          f"{'DD%':>6}  {'win%':>6}  {'ret%':>7}  {'ret/exp%':>9}")
    for k, v in exp.items():
        print(f"  {k:<6}  {v['trades']:>7}  {v['pf']:>6.2f}  "
              f"{v['dd_pct']:>6.2f}  {v['win_pct']:>6.1f}  "
              f"{v['ret_pct']:>+7.2f}  {v['ret_per_exp_pct']:>+9.2f}")
    print(f"  {'LIVE':<6}  {stats.n:>7}  {fmt_pf(stats.pf):>6}  "
          f"{stats.max_dd_pct:>6.2f}  {stats.win_rate:>6.1f}  "
          f"{stats.total_return_pct:>+7.2f}  "
          f"{stats.ret_per_exposure_pct:>+9.2f}")

    print()
    print("HEARTBEAT")
    if hb is None:
        print("  (no heartbeat file — worker may not be running)")
    else:
        print(f"  ts                     {hb.get('ts')}")
        print(f"  schema                 {hb.get('schema')}")
        for asset, blk in (hb.get("assets") or {}).items():
            print(f"  {asset}")
            print(f"    last_price           {blk.get('last_price')}")
            print(f"    position_open        {blk.get('position_open')}")
            print(f"    funding_decision     {blk.get('funding_decision')}")
            print(f"    funding_percentile   {blk.get('funding_percentile')}")
            print(f"    vol_bucket           {blk.get('vol_bucket')}")
            print(f"    vol_multiplier       {blk.get('vol_multiplier')}")
            if blk.get("volume_confirmation_enabled"):
                print(f"    volume_decision      "
                      f"{blk.get('volume_confirmation_decision')}")
                print(f"    volume_ratio         "
                      f"{blk.get('volume_ratio')}")

    print()
    print("TRIPWIRES")
    if not tripwires:
        print("  [ok] no tripwires triggered")
    else:
        for t in tripwires:
            print(f"  [!]  {t}")

    print()
    print("FREEZE DIRECTIVE")
    fd = marker.get("freeze_directive", {})
    print(f"  frozen:   {fd.get('frozen')}")
    print(f"  until:    {fd.get('no_changes_until')}")


def _self_test() -> int:
    print("self-test: forward_test_tracker")
    failures = 0

    def check(name, ok, detail=""):
        nonlocal failures
        if ok:
            print(f"  [ok] {name}")
        else:
            print(f"  [FAIL] {name} -- {detail}")
            failures += 1

    fake_trades = [
        {"status": "closed", "asset": "BTC/USDT", "direction": "long",
         "exit_reason": "stop", "return": -0.012,
         "closed_at": "2026-06-01T00:00:00+00:00"},
        {"status": "closed", "asset": "BTC/USDT", "direction": "short",
         "exit_reason": "take_profit", "return": +0.024,
         "closed_at": "2026-06-02T00:00:00+00:00"},
        {"status": "closed", "asset": "ETH/USDT", "direction": "long",
         "exit_reason": "stop", "return": -0.008,
         "closed_at": "2026-06-03T00:00:00+00:00"},
        {"status": "closed", "asset": "ETH/USDT", "direction": "long",
         "exit_reason": "take_profit", "return": +0.018,
         "closed_at": "2026-06-04T00:00:00+00:00"},
    ]
    stats = compute_stats(fake_trades)
    check("count = 4", stats.n == 4)
    check("winners = 2", stats.n_winners == 2)
    check("losers = 2", stats.n_losers == 2)
    check("win rate = 50%", abs(stats.win_rate - 50.0) < 1e-9)
    expected_pf = (0.024 + 0.018) / (0.012 + 0.008)
    check("PF = (sum win) / (sum loss)",
          abs(stats.pf - expected_pf) < 1e-9,
          f"got {stats.pf} expected {expected_pf}")

    losers_only = [
        {"status": "closed", "asset": "BTC/USDT", "direction": "long",
         "exit_reason": "stop", "return": -0.01,
         "closed_at": "2026-06-01T00:00:00+00:00"}
        for _ in range(12)
    ]
    stats_pf_low = compute_stats(losers_only)
    check("PF = 0 when no winners (12 trades)", stats_pf_low.pf == 0.0)
    check("PF=0 trips tripwire at >=10 trades",
          stats_pf_low.n >= 10 and stats_pf_low.pf < 0.8)

    winners_only = [
        {"status": "closed", "asset": "BTC/USDT", "direction": "long",
         "exit_reason": "take_profit", "return": +0.01,
         "closed_at": "2026-06-01T00:00:00+00:00"}
        for _ in range(5)
    ]
    stats_pf_inf = compute_stats(winners_only)
    check("PF = inf when no losers", stats_pf_inf.pf == float("inf"))

    log_lines = [
        "[14:30:00] BLOCK long BTC/USDT @ 73000 (volume_confirmation: low_volume_flip)",
        "[14:30:00] BLOCK short ETH/USDT @ 2000 (funding_filter block_short)",
        "[14:30:00] BLOCK long BTC/USDT @ 73000 (funding_filter block_long)",
        "[14:30:00] ENTER long BTC/USDT",
        "[14:30:00] BLOCK long ETH/USDT @ 2000 (volume_confirmation: low_volume_flip)",
    ]
    tmp_log = ROOT / "state" / "_fwd_test_tracker_selftest.log"
    tmp_log.write_text("\n".join(log_lines))
    try:
        blocks = count_blocks_in_log(tmp_log,
                                      datetime(2026, 1, 1, tzinfo=timezone.utc))
        check("block parser: total = 4", blocks["total"] == 4,
              f"got {blocks}")
        check("block parser: vol_conf = 2",
              blocks["volume_confirmation_block"] == 2,
              f"got {blocks}")
        check("block parser: funding_long = 1",
              blocks["funding_block_long"] == 1,
              f"got {blocks}")
        check("block parser: funding_short = 1",
              blocks["funding_block_short"] == 1,
              f"got {blocks}")
    finally:
        tmp_log.unlink(missing_ok=True)

    if failures:
        print(f"\nSELF-TEST FAILED ({failures})")
        return 1
    print("\nSELF-TEST PASSED")
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return _self_test()

    marker = load_marker()
    since = _parse_ts(marker["started_at"])
    trade_log = ROOT / marker["trade_log_path"]
    worker_log = ROOT / marker["worker_log_path"]

    trades = load_trades_since(trade_log, since)
    stats = compute_stats(trades)
    blocks = count_blocks_in_log(worker_log, since)
    hb = load_heartbeat(ROOT / "state")
    tripwires = evaluate_tripwires(stats, marker, hb, worker_log.exists())

    print_report(stats, marker, hb, blocks, tripwires)

    if tripwires and any("PF " in t for t in tripwires):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
