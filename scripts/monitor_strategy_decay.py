#!/usr/bin/env python3
"""Live strategy decay monitor (Issue #15).

Reads closed paper-trade records from a JSONL log and reports whether
recent live performance has degraded versus research-time
expectations.

Monitor / report only. Never modifies trading decisions, never resizes
positions, never auto-disables strategies. Has no exchange-side state.

Default trade log: ``state/trades.jsonl`` (written by the live worker
in ``hermes_trading/loop.py``).

Usage:
    uv run python scripts/monitor_strategy_decay.py
    uv run python scripts/monitor_strategy_decay.py --windows 10,25,50
    uv run python scripts/monitor_strategy_decay.py \\
        --baseline-pf 2.50 --baseline-dd 5.54 --baseline-win-rate 0.487 \\
        --json --output results/decay_report_$(date +%Y%m%d_%H%M%S).json
    uv run python scripts/monitor_strategy_decay.py --self-test

Exit codes:
    0 = OK (no warnings on any reportable window)
    1 = DEGRADED (one or more warnings on at least one window)
    2 = insufficient data or unreadable input
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRADES = ROOT / "state" / "trades.jsonl"

# Default baselines come from Issue #14's adopted research candidate
# (btc_eth_reference_parallel). The user can override on the command
# line; these are sensible defaults for a parallel-portfolio strategy.
DEFAULTS = {
    "baseline_pf": 2.50,
    "baseline_dd": 5.54,           # percent
    "baseline_win_rate": 0.487,    # 48.7%
    "min_trades": 10,
    "windows": [10, 25, 50],
    "pf_warn_below": 1.20,
    "win_rate_warn_factor": 0.65,  # warn when win_rate < baseline * 0.65
    "dd_warn_factor": 1.25,        # warn when DD > baseline * 1.25
    "consecutive_loss_warn": 4,
}

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
GRAY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"


# ---------- parsing ---------------------------------------------------------

REQUIRED_FIELDS = ("return",)   # The single absolutely-required field.

OPTIONAL_FIELDS = (
    "status", "asset", "opened_at", "closed_at",
    "entry_price", "exit_price", "exit_reason",
    "strategy_version", "direction", "setup",
)


def load_trades(path: Path) -> tuple[list[dict], list[str]]:
    """Return (closed_trades, parse_warnings). Defensive: skip blank
    lines and lines that don't parse as JSON, collect a warning for
    each. Filter to status='closed' when status is present (the live
    worker only writes closed trades, but we don't trust the field to
    always be there)."""
    trades: list[dict] = []
    warnings: list[str] = []
    if not path.exists():
        return trades, [f"file not found: {path}"]
    try:
        text = path.read_text()
    except OSError as exc:
        return trades, [f"could not read {path}: {exc}"]
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            warnings.append(f"line {i}: not valid JSON ({exc})")
            continue
        if not isinstance(obj, dict):
            warnings.append(f"line {i}: not a JSON object")
            continue
        if "status" in obj and obj["status"] != "closed":
            continue
        if "return" not in obj:
            warnings.append(f"line {i}: missing required field 'return'")
            continue
        try:
            obj["return"] = float(obj["return"])
        except (TypeError, ValueError):
            warnings.append(f"line {i}: 'return' is not numeric")
            continue
        trades.append(obj)
    return trades, warnings


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        s = str(value)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


# ---------- metrics ---------------------------------------------------------

def compute_metrics(trades: list[dict]) -> dict:
    """Compute metrics over a list of trades (already sliced to a
    window). All returns are net (whatever the live worker wrote)."""
    if not trades:
        return {
            "n": 0,
            "total_return": 0.0,
            "avg_return": 0.0,
            "median_return": 0.0,
            "win_rate": 0.0,
            "profit_factor": float("nan"),
            "max_drawdown": 0.0,
            "avg_holding_seconds": None,
            "worst_trade": None,
            "best_trade": None,
            "consecutive_losses": 0,
            "latest_trade_iso": None,
        }
    rets = [t["return"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    sum_wins = sum(wins)
    sum_losses = sum(losses)
    if sum_losses == 0:
        pf = float("inf") if sum_wins > 0 else float("nan")
    else:
        pf = sum_wins / abs(sum_losses)

    eq = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in rets:
        eq *= 1.0 + r
        peak = max(peak, eq)
        if peak > 0:
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd

    holds = []
    latest_iso = None
    for t in trades:
        opened = _parse_iso(t.get("opened_at"))
        closed = _parse_iso(t.get("closed_at"))
        if opened and closed and closed >= opened:
            holds.append((closed - opened).total_seconds())
        if closed:
            if latest_iso is None or closed > latest_iso:
                latest_iso = closed
    avg_hold = statistics.mean(holds) if holds else None

    # Trailing consecutive losses from the end of the window
    cl = 0
    for r in reversed(rets):
        if r <= 0:
            cl += 1
        else:
            break

    return {
        "n": len(trades),
        "total_return": eq - 1.0,
        "avg_return": statistics.mean(rets),
        "median_return": statistics.median(rets),
        "win_rate": len(wins) / len(rets),
        "profit_factor": pf,
        "max_drawdown": max_dd,
        "avg_holding_seconds": avg_hold,
        "worst_trade": min(rets),
        "best_trade": max(rets),
        "consecutive_losses": cl,
        "latest_trade_iso": latest_iso.isoformat() if latest_iso else None,
    }


def evaluate_warnings(metrics: dict, baseline_pf: float, baseline_dd_pct: float,
                      baseline_win_rate: float, cfg: dict) -> list[str]:
    """Return list of human-readable warning labels triggered by metrics.
    Empty list means no warnings."""
    warnings: list[str] = []
    pf = metrics["profit_factor"]
    if pf == pf and pf != float("inf") and pf < cfg["pf_warn_below"]:
        warnings.append(f"PF {pf:.2f} < {cfg['pf_warn_below']:.2f}")
    if metrics["win_rate"] < baseline_win_rate * cfg["win_rate_warn_factor"]:
        warnings.append(
            f"win rate {metrics['win_rate']*100:.1f}% < "
            f"{baseline_win_rate * cfg['win_rate_warn_factor'] * 100:.1f}% "
            f"({cfg['win_rate_warn_factor']:.0%} of baseline {baseline_win_rate*100:.1f}%)"
        )
    dd_thresh = (baseline_dd_pct / 100.0) * cfg["dd_warn_factor"]
    if metrics["max_drawdown"] > dd_thresh:
        warnings.append(
            f"max DD {metrics['max_drawdown']*100:.2f}% > "
            f"{dd_thresh*100:.2f}% ({cfg['dd_warn_factor']:.0%} of baseline {baseline_dd_pct:.2f}%)"
        )
    if metrics["consecutive_losses"] >= cfg["consecutive_loss_warn"]:
        warnings.append(
            f"consecutive losses {metrics['consecutive_losses']} "
            f">= {cfg['consecutive_loss_warn']}"
        )
    if metrics["total_return"] < 0:
        warnings.append(f"total return {metrics['total_return']*100:+.2f}% < 0")
    return warnings


# ---------- output ---------------------------------------------------------

def format_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds/60:.1f}m"
    if seconds < 86400:
        return f"{seconds/3600:.1f}h"
    return f"{seconds/86400:.1f}d"


def render_terminal(report: dict, color: bool = True) -> str:
    g, r, y, c, gr, b, x = (GREEN, RED, YELLOW, CYAN, GRAY, BOLD, RESET) if color else ("",) * 7
    lines = []
    lines.append(f"{b}Strategy decay report{x}")
    lines.append("")
    lines.append(f"{gr}Source: {report['source']}{x}")
    lines.append(f"Closed trades: {report['total_closed']}")
    latest = report.get("latest_trade_iso")
    if latest:
        lines.append(f"Latest trade: {latest}")
    if report.get("parse_warnings"):
        lines.append("")
        lines.append(f"{y}Parse warnings:{x}")
        for w in report["parse_warnings"]:
            lines.append(f"  - {w}")
    lines.append("")
    lines.append(f"Baselines: PF={report['baselines']['pf']:.2f}  "
                 f"DD={report['baselines']['dd_pct']:.2f}%  "
                 f"win-rate={report['baselines']['win_rate']*100:.1f}%")
    lines.append("")

    overall_warnings = 0
    reportable_windows = 0
    for w in report["windows"]:
        n = w["metrics"]["n"]
        if n < report["min_trades"]:
            lines.append(f"{b}Window {w['window']}:{x} insufficient data "
                         f"({n}/{report['min_trades']}) — skipped")
            continue
        reportable_windows += 1
        m = w["metrics"]
        warns = w["warnings"]
        if warns:
            overall_warnings += 1
        status_color = r if warns else g
        lines.append(f"{b}Window {w['window']}:{x}  [{n} trades]")
        pf_str = "inf" if m["profit_factor"] == float("inf") else (
            "n/a" if m["profit_factor"] != m["profit_factor"] else f"{m['profit_factor']:.2f}")
        # PF / win / DD / return / consecutive losses with per-line WARNING tag
        def warn_tag(condition: bool) -> str:
            return f"  {r}WARNING{x}" if condition else ""
        pf_warn = any("PF" in w_ for w_ in warns)
        wr_warn = any("win rate" in w_ for w_ in warns)
        dd_warn = any("max DD" in w_ for w_ in warns)
        ret_warn = any("total return" in w_ for w_ in warns)
        cl_warn = any("consecutive losses" in w_ for w_ in warns)
        ret_color = (g if m["total_return"] >= 0 else r) if color else ""
        lines.append(f"  PF: {pf_str}{warn_tag(pf_warn)}")
        lines.append(f"  Win rate: {m['win_rate']*100:.1f}%{warn_tag(wr_warn)}")
        lines.append(f"  Max DD: {m['max_drawdown']*100:.2f}%{warn_tag(dd_warn)}")
        lines.append(f"  Return: {ret_color}{m['total_return']*100:+.2f}%{x}{warn_tag(ret_warn)}")
        lines.append(f"  Consecutive losses: {m['consecutive_losses']}{warn_tag(cl_warn)}")
        lines.append(f"  Avg holding: {format_seconds(m['avg_holding_seconds'])}")
        lines.append(f"  Best / worst: {m['best_trade']*100:+.3f}% / {m['worst_trade']*100:+.3f}%")
        lines.append("")

    if reportable_windows == 0:
        lines.append(f"{y}Status: INSUFFICIENT DATA{x}")
        lines.append("  Need more closed trades before decay can be assessed.")
    elif overall_warnings == 0:
        lines.append(f"{g}Status: OK{x}")
        lines.append("  No decay warnings on any reportable window.")
    else:
        lines.append(f"{r}Status: DEGRADED{x}")
        lines.append("  Recommended action: review strategy before increasing exposure.")
        lines.append("  No automatic action taken.")
    return "\n".join(lines)


# ---------- main ------------------------------------------------------------

def build_report(trades: list[dict], parse_warnings: list[str], source: str,
                 windows: list[int], baselines: dict, cfg: dict,
                 min_trades: int) -> dict:
    """The pure-function core. Used by both the CLI main() and the
    self-test."""
    overall_latest = None
    for t in trades:
        d = _parse_iso(t.get("closed_at")) or _parse_iso(t.get("opened_at"))
        if d and (overall_latest is None or d > overall_latest):
            overall_latest = d
    report = {
        "source": source,
        "total_closed": len(trades),
        "latest_trade_iso": overall_latest.isoformat() if overall_latest else None,
        "min_trades": min_trades,
        "baselines": baselines,
        "config": cfg,
        "parse_warnings": parse_warnings,
        "windows": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    for w in windows:
        slice_ = trades[-w:] if len(trades) >= w else trades
        m = compute_metrics(slice_)
        warns = evaluate_warnings(m, baselines["pf"], baselines["dd_pct"],
                                  baselines["win_rate"], cfg) if m["n"] >= min_trades else []
        report["windows"].append({"window": w, "metrics": m, "warnings": warns})
    # overall status
    if all(w["metrics"]["n"] < min_trades for w in report["windows"]):
        report["status"] = "INSUFFICIENT_DATA"
    elif any(w["warnings"] for w in report["windows"]):
        report["status"] = "DEGRADED"
    else:
        report["status"] = "OK"
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Decay monitor for the live paper strategy.")
    ap.add_argument("--trades", type=Path, default=DEFAULT_TRADES)
    ap.add_argument("--windows", default=",".join(str(w) for w in DEFAULTS["windows"]),
                    help="comma-separated window sizes")
    ap.add_argument("--baseline-pf", type=float, default=DEFAULTS["baseline_pf"])
    ap.add_argument("--baseline-dd", type=float, default=DEFAULTS["baseline_dd"],
                    help="baseline max drawdown in PERCENT, e.g. 5.54")
    ap.add_argument("--baseline-win-rate", type=float, default=DEFAULTS["baseline_win_rate"])
    ap.add_argument("--min-trades", type=int, default=DEFAULTS["min_trades"])
    ap.add_argument("--pf-warn-below", type=float, default=DEFAULTS["pf_warn_below"])
    ap.add_argument("--win-rate-warn-factor", type=float, default=DEFAULTS["win_rate_warn_factor"])
    ap.add_argument("--dd-warn-factor", type=float, default=DEFAULTS["dd_warn_factor"])
    ap.add_argument("--consecutive-loss-warn", type=int, default=DEFAULTS["consecutive_loss_warn"])
    ap.add_argument("--json", action="store_true", help="print JSON instead of human output")
    ap.add_argument("--output", type=Path, default=None,
                    help="also write the JSON report to this path")
    ap.add_argument("--no-color", action="store_true",
                    help="disable ANSI color codes in human output")
    ap.add_argument("--self-test", action="store_true",
                    help="run built-in tests against the fixture and exit")
    args = ap.parse_args(argv)

    if args.self_test:
        return _run_self_test()

    windows = [int(x) for x in args.windows.split(",") if x.strip()]
    cfg = {
        "pf_warn_below": args.pf_warn_below,
        "win_rate_warn_factor": args.win_rate_warn_factor,
        "dd_warn_factor": args.dd_warn_factor,
        "consecutive_loss_warn": args.consecutive_loss_warn,
    }
    baselines = {
        "pf": args.baseline_pf,
        "dd_pct": args.baseline_dd,
        "win_rate": args.baseline_win_rate,
    }
    trades, parse_warnings = load_trades(args.trades)
    if not trades and parse_warnings:
        # unreadable input
        print(f"could not load any trades from {args.trades}", file=sys.stderr)
        for w in parse_warnings:
            print(f"  - {w}", file=sys.stderr)
        return 2

    report = build_report(trades, parse_warnings, str(args.trades),
                          windows, baselines, cfg, args.min_trades)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_terminal(report, color=not args.no_color))

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, default=str))

    if report["status"] == "INSUFFICIENT_DATA":
        return 2
    return 1 if report["status"] == "DEGRADED" else 0


# ---------- self-test -------------------------------------------------------

FIXTURE_PATH = ROOT / "tests" / "fixtures" / "trades_decay_sample.jsonl"


def _run_self_test() -> int:
    """Run a battery of checks against the fixture file. Designed to
    catch regressions in metric math and warning thresholds. Returns
    0 on success, 1 on test failure."""
    from io import StringIO
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        if condition:
            print(f"  {GREEN}✓{RESET} {name}")
        else:
            failures.append(f"{name} -- {detail}")
            print(f"  {RED}✗{RESET} {name}  {detail}")

    print(f"{BOLD}Self-test: decay monitor{RESET}")
    print()
    if not FIXTURE_PATH.exists():
        print(f"{RED}fixture missing: {FIXTURE_PATH}{RESET}")
        return 1
    trades, parse_warnings = load_trades(FIXTURE_PATH)

    print(f"Loaded {len(trades)} trades from {FIXTURE_PATH.name} "
          f"({len(parse_warnings)} parse warnings — expected on the malformed lines)")
    print()

    # 1. The fixture is laid out so the first 15 trades are healthy
    #    (PF >= 2.0, win rate ~ 55%), the last 10 are decayed (4+
    #    consecutive losses, PF < 1.0, negative return).
    healthy = compute_metrics(trades[:15])
    decayed = compute_metrics(trades[-10:])
    check("loaded enough trades", len(trades) >= 25,
          f"got {len(trades)}")
    check("parser flagged exactly the expected malformed lines",
          len(parse_warnings) == 3,
          f"got {len(parse_warnings)} warnings: {parse_warnings}")
    check("healthy slice PF > 2.0", healthy["profit_factor"] > 2.0,
          f"got {healthy['profit_factor']:.2f}")
    check("healthy slice win rate >= 0.55",
          healthy["win_rate"] >= 0.55, f"got {healthy['win_rate']:.2f}")
    check("healthy slice no consecutive losses warning",
          healthy["consecutive_losses"] < 4,
          f"got {healthy['consecutive_losses']}")
    check("decayed slice PF < 1.0",
          decayed["profit_factor"] < 1.0, f"got {decayed['profit_factor']:.2f}")
    check("decayed slice total return < 0",
          decayed["total_return"] < 0, f"got {decayed['total_return']:.4f}")
    check("decayed slice consecutive losses >= 4",
          decayed["consecutive_losses"] >= 4,
          f"got {decayed['consecutive_losses']}")

    # 2. The full report on the fixture should be DEGRADED on the 10-window.
    cfg = {"pf_warn_below": DEFAULTS["pf_warn_below"],
           "win_rate_warn_factor": DEFAULTS["win_rate_warn_factor"],
           "dd_warn_factor": DEFAULTS["dd_warn_factor"],
           "consecutive_loss_warn": DEFAULTS["consecutive_loss_warn"]}
    baselines = {"pf": DEFAULTS["baseline_pf"],
                 "dd_pct": DEFAULTS["baseline_dd"],
                 "win_rate": DEFAULTS["baseline_win_rate"]}
    report = build_report(trades, parse_warnings, str(FIXTURE_PATH),
                          DEFAULTS["windows"], baselines, cfg,
                          DEFAULTS["min_trades"])
    check("report.status == DEGRADED on fixture",
          report["status"] == "DEGRADED",
          f"got {report['status']}")
    win10 = next(w for w in report["windows"] if w["window"] == 10)
    check("window-10 has consecutive_losses warning",
          any("consecutive losses" in w for w in win10["warnings"]),
          f"got warnings {win10['warnings']}")

    # 3. Empty input handling
    empty_report = build_report([], [], "(empty)", [10, 25, 50],
                                baselines, cfg, 10)
    check("empty input → INSUFFICIENT_DATA",
          empty_report["status"] == "INSUFFICIENT_DATA",
          f"got {empty_report['status']}")

    # 4. All-winners edge case (PF should be inf, not crash)
    all_wins = [{"return": 0.01} for _ in range(10)]
    wins_m = compute_metrics(all_wins)
    check("all-winners gives PF = inf",
          wins_m["profit_factor"] == float("inf"),
          f"got {wins_m['profit_factor']}")
    check("all-winners gives drawdown = 0",
          wins_m["max_drawdown"] == 0,
          f"got {wins_m['max_drawdown']}")

    # 5. Render terminal output without crashing
    try:
        out = render_terminal(report, color=False)
        check("render_terminal produces output",
              "Strategy decay report" in out and "Status:" in out,
              "missing expected markers")
    except Exception as exc:  # noqa: BLE001
        check("render_terminal produces output", False, str(exc))

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
