"""Shadow paper-lab runner (Issue #43).

Run multiple paper-mode strategy variants side-by-side on live market
data, fully isolated from the main forward paper test.

Isolation strategy
------------------
Each variant runs as its own subprocess with ``HERMES_STATE_DIR``
pointed at ``state/paper_lab/<variant>/``. The worker writes its
``heartbeat.json``, ``trades.jsonl``, and ``positions/`` into that
isolated directory. A shared symlink ``state/paper_lab/state -> ..``
makes the worker's ``state_dir.parent / "state/<strategy>.yaml"``
resolution land on the canonical strategies in ``state/``. A per-variant
symlink ``<variant>/data -> ../../data`` shares the Binance Vision
archive cache so variants don't redownload identical OHLCV / funding
archives.

Main forward-test state files are intentionally NEVER touched:

    state/heartbeat.json
    state/trades.jsonl
    state/forward_paper_test.json

The lab refuses to create artifacts under any of those paths.

Paper only. The worker entry point is ``hermes_trading.run`` which has
no exchange-order side-effects; the lab does not import or call any
exchange-order function.

Usage
-----

    uv run python scripts/paper_lab.py start
    uv run python scripts/paper_lab.py status
    uv run python scripts/paper_lab.py compare
    uv run python scripts/paper_lab.py logs --variant current_best [--lines 50]
    uv run python scripts/paper_lab.py logs --variant current_best --tail
    uv run python scripts/paper_lab.py logs --variant all --tail
    uv run python scripts/paper_lab.py stop
    uv run python scripts/paper_lab.py --self-test

`logs --tail` (alias `--follow` or `-f`) streams new log lines as they
arrive, like `tail -f`. Ctrl-C exits. Use ``--variant all`` to multi-tail
every variant with line prefixes.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAB_ROOT = ROOT / "state" / "paper_lab"

VARIANTS: dict[str, str] = {
    "current_best":             "state/live_multiasset_long_short_funding_vol_volconf.yaml",
    "no_volume_confirmation":   "state/live_multiasset_long_short_funding_vol.yaml",
    "funding_only":             "state/live_multiasset_long_short_funding.yaml",
    "long_only_baseline":       "state/live_multiasset.yaml",
}

MAIN_STATE_FILES_GUARDED: tuple[Path, ...] = (
    ROOT / "state" / "heartbeat.json",
    ROOT / "state" / "trades.jsonl",
    ROOT / "state" / "forward_paper_test.json",
)

SAFETY_BANNER = (
    "Shadow paper lab only. No real-money orders are sent. "
    "Main forward test remains untouched."
)


def _variant_dir(name: str) -> Path:
    return LAB_ROOT / name


def _pid_file(name: str) -> Path:
    return _variant_dir(name) / "worker.pid"


def _log_file(name: str) -> Path:
    return _variant_dir(name) / "worker.log"


def _summary_file(name: str) -> Path:
    return _variant_dir(name) / "summary.json"


def _trades_path(name: str) -> Path:
    return _variant_dir(name) / "trades.jsonl"


def _heartbeat_path(name: str) -> Path:
    return _variant_dir(name) / "heartbeat.json"


def _parse_ts(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


def is_running(name: str) -> int | None:
    pf = _pid_file(name)
    if not pf.exists():
        return None
    try:
        pid = int(pf.read_text().strip())
    except (ValueError, OSError):
        return None
    if _is_pid_alive(pid):
        return pid
    return None


def ensure_isolation(name: str, config_rel: str) -> None:
    """Create the isolated variant directory and supporting symlinks.

    Guarantees:
      - never modifies any main state file
      - idempotent: safe to call repeatedly
      - shared Binance Vision archive cache (read-mostly) via symlink
    """
    if name not in VARIANTS:
        raise ValueError(f"unknown variant: {name}")

    LAB_ROOT.mkdir(parents=True, exist_ok=True)

    # Shared symlink so strategy_path resolution
    # (state_dir.parent / "state/<strategy>.yaml") lands at <root>/state/.
    shared_state_link = LAB_ROOT / "state"
    if not shared_state_link.exists() and not shared_state_link.is_symlink():
        shared_state_link.symlink_to(Path(".."), target_is_directory=True)

    vd = _variant_dir(name)
    vd.mkdir(parents=True, exist_ok=True)
    (vd / "positions").mkdir(exist_ok=True)

    # Shared data cache symlink so variants share Binance Vision
    # downloads instead of redownloading per variant.
    data_link = vd / "data"
    if not data_link.exists() and not data_link.is_symlink():
        os.symlink(Path("../../data"), data_link)

    # goal.yaml: copy once, not modified afterwards.
    goal_copy = vd / "goal.yaml"
    if not goal_copy.exists():
        shutil.copy2(ROOT / "state" / "goal.yaml", goal_copy)

    # Sanity: the variant directory must not collide with the main
    # forward-test state file paths.
    for guard in MAIN_STATE_FILES_GUARDED:
        if guard.resolve() == _heartbeat_path(name).resolve():
            raise RuntimeError(
                f"refusing to alias main state file {guard}")
        if guard.resolve() == _trades_path(name).resolve():
            raise RuntimeError(
                f"refusing to alias main state file {guard}")


def build_command(config_rel: str) -> list[str]:
    """Construct the subprocess command for one variant.

    Uses the current interpreter (sys.executable) so the lab itself
    can be launched via `uv run` and subprocesses inherit the same
    Python + venv without nested `uv run` overhead.
    """
    return [sys.executable, "-m", "hermes_trading.run",
            "--config", str(ROOT / config_rel), "--verbose"]


def _start_variant(name: str, config_rel: str) -> int | None:
    existing = is_running(name)
    if existing:
        print(f"  [skip]  {name:<24} already running PID={existing}")
        return existing

    ensure_isolation(name, config_rel)
    vd = _variant_dir(name)
    env = os.environ.copy()
    env["HERMES_STATE_DIR"] = str(vd.resolve())

    log_path = _log_file(name)
    log_f = log_path.open("a")
    log_f.write(
        f"\n--- paper_lab start {datetime.now(timezone.utc).isoformat()} ---\n"
        f"--- HERMES_STATE_DIR={env['HERMES_STATE_DIR']}\n"
        f"--- config={config_rel}\n"
    )
    log_f.flush()

    cmd = build_command(config_rel)
    proc = subprocess.Popen(
        cmd,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env,
        cwd=str(ROOT),
        start_new_session=True,
    )
    _pid_file(name).write_text(str(proc.pid))
    print(f"  [start] {name:<24} PID={proc.pid} log={log_path.relative_to(ROOT)}")
    return proc.pid


def _stop_variant(name: str, timeout_seconds: float = 15.0) -> None:
    pid = is_running(name)
    if not pid:
        _pid_file(name).unlink(missing_ok=True)
        print(f"  [skip]  {name:<24} not running")
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _pid_file(name).unlink(missing_ok=True)
        print(f"  [skip]  {name:<24} disappeared before SIGTERM")
        return
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not _is_pid_alive(pid):
            break
        time.sleep(0.5)
    if _is_pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.5)
        except ProcessLookupError:
            pass
    _pid_file(name).unlink(missing_ok=True)
    print(f"  [stop]  {name:<24} PID={pid}")


def _load_trades(name: str) -> list[dict]:
    p = _trades_path(name)
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            t = json.loads(line)
        except json.JSONDecodeError:
            continue
        if t.get("status") != "closed":
            continue
        out.append(t)
    return out


def _load_heartbeat(name: str) -> dict | None:
    p = _heartbeat_path(name)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def _count_blocks(name: str) -> dict:
    counts = {
        "funding_block_long": 0,
        "funding_block_short": 0,
        "volume_confirmation_block": 0,
        "funding_missing_data_block": 0,
        "other_block": 0,
        "total": 0,
    }
    p = _log_file(name)
    if not p.exists():
        return counts
    text = p.read_text(errors="replace")
    for line in text.splitlines():
        if "BLOCK long" not in line and "BLOCK short" not in line:
            continue
        counts["total"] += 1
        lower = line.lower()
        if "volume_confirmation" in lower:
            counts["volume_confirmation_block"] += 1
        elif "block_long" in lower:
            counts["funding_block_long"] += 1
        elif "block_short" in lower:
            counts["funding_block_short"] += 1
        elif "missing_data" in lower:
            counts["funding_missing_data_block"] += 1
        else:
            counts["other_block"] += 1
    return counts


@dataclass
class VariantStats:
    n: int
    n_winners: int
    n_losers: int
    win_pct: float
    pf_str: str
    total_return_pct: float
    max_dd_pct: float
    ret_per_exp_pct: float


def _compute_stats(trades: list[dict]) -> VariantStats:
    n = len(trades)
    if n == 0:
        return VariantStats(0, 0, 0, 0.0, "0.00", 0.0, 0.0, 0.0)
    returns = [float(t.get("return", 0.0)) for t in trades]
    winners = [r for r in returns if r > 0]
    losers = [r for r in returns if r <= 0]
    sum_win = sum(winners)
    sum_loss = -sum(losers)
    if sum_loss > 0:
        pf = sum_win / sum_loss
        pf_str = f"{pf:.2f}"
    elif sum_win > 0:
        pf_str = "inf"
    else:
        pf_str = "0.00"
    eq = 1.0
    peak = 1.0
    worst = 0.0
    for r in returns:
        eq *= (1.0 + r)
        peak = max(peak, eq)
        if peak > 0:
            worst = max(worst, (peak - eq) / peak)
    total_return_pct = (eq - 1.0) * 100.0
    win_pct = (len(winners) / n) * 100.0
    ret_per_exp_pct = (sum(returns) / n) * 100.0
    return VariantStats(
        n=n, n_winners=len(winners), n_losers=len(losers),
        win_pct=win_pct, pf_str=pf_str,
        total_return_pct=total_return_pct,
        max_dd_pct=worst * 100.0,
        ret_per_exp_pct=ret_per_exp_pct,
    )


def cmd_start(args: argparse.Namespace) -> int:
    print(SAFETY_BANNER)
    LAB_ROOT.mkdir(parents=True, exist_ok=True)
    for name, cfg in VARIANTS.items():
        _start_variant(name, cfg)
    print()
    print("Started.")
    print(f"  status:  uv run python scripts/paper_lab.py status")
    print(f"  compare: uv run python scripts/paper_lab.py compare")
    print(f"  stop:    uv run python scripts/paper_lab.py stop")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    for name in VARIANTS:
        _stop_variant(name)
    return 0


def _classify_heartbeat(
    name: str,
    pid: int | None,
    hb: dict | None,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Decide what to display for the heartbeat line.

    Compares the heartbeat ts against the ``worker.pid`` file's mtime:
    a heartbeat written BEFORE the current worker.pid was created is a
    leftover from a previous worker generation that has not been
    overwritten yet (the new worker is still loading overlays).

    Returns ``(headline, detail)`` where headline goes after
    ``"last heartbeat"`` and detail goes on the next line indented.
    """
    now = now or datetime.now(timezone.utc)
    pid_path = _pid_file(name)
    pid_mtime = None
    if pid_path.exists():
        pid_mtime = datetime.fromtimestamp(pid_path.stat().st_mtime, tz=timezone.utc)
    hb_ts = hb.get("ts") if hb else None
    hb_dt = None
    if hb_ts:
        try:
            hb_dt = _parse_ts(hb_ts)
        except Exception:
            hb_dt = None

    if pid is None:
        # Worker not running. Heartbeat (if present) is from a prior run.
        if hb_dt is None:
            return ("(none)", "")
        age = (now - hb_dt).total_seconds()
        return (f"{hb_ts}  ({age:.0f}s ago, from previous run)", "")

    # Worker IS running.
    if hb_dt is None:
        # No heartbeat at all yet.
        worker_age = "?"
        if pid_mtime is not None:
            worker_age = f"{(now - pid_mtime).total_seconds():.0f}s"
        return (
            f"warming up (PID {pid} spawned {worker_age} ago, no heartbeat yet)",
            "",
        )

    age = (now - hb_dt).total_seconds()
    if pid_mtime is not None and hb_dt < pid_mtime:
        # Heartbeat predates the current worker generation.
        worker_age = (now - pid_mtime).total_seconds()
        return (
            f"warming up (PID {pid} spawned {worker_age:.0f}s ago, "
            f"no fresh heartbeat yet)",
            f"prior-run heartbeat: {hb_ts}  ({age:.0f}s ago)",
        )

    # Fresh heartbeat. Flag old-but-fresh-generation as stale.
    if age >= 60:
        return (
            f"{hb_ts}  ({age:.0f}s ago, STALE — worker may be stuck)",
            "",
        )
    return (f"{hb_ts}  ({age:.0f}s ago)", "")


def cmd_status(args: argparse.Namespace) -> int:
    print(f"PAPER LAB STATUS  ({datetime.now(timezone.utc).isoformat(timespec='seconds')})")
    print("=" * 78)
    any_running = False
    for name, cfg in VARIANTS.items():
        pid = is_running(name)
        running_str = f"RUNNING  PID={pid}" if pid else "stopped"
        if pid:
            any_running = True
        trades = _load_trades(name)
        stats = _compute_stats(trades)
        hb = _load_heartbeat(name)
        blocks = _count_blocks(name)
        hb_headline, hb_detail = _classify_heartbeat(name, pid, hb)
        open_positions = 0
        unrealized_pct = 0.0
        if hb:
            for asset, blk in (hb.get("assets") or {}).items():
                if blk.get("position_open"):
                    open_positions += 1
                    unrealized_pct += float(blk.get("unrealized_pnl_pct") or 0.0)

        print(f"[{name}]  {running_str}")
        print(f"  config             {cfg}")
        print(f"  state_dir          {_variant_dir(name).relative_to(ROOT)}")
        print(f"  log                {_log_file(name).relative_to(ROOT)}")
        print(f"  closed trades      {stats.n}  ({stats.n_winners}W / {stats.n_losers}L)")
        print(f"  total return       {stats.total_return_pct:+.2f}%")
        print(f"  max DD             {stats.max_dd_pct:.2f}%")
        print(f"  PF                 {stats.pf_str}")
        print(f"  open positions     {open_positions}")
        print(f"  unrealized PnL     {unrealized_pct:+.2f}%")
        print(f"  last heartbeat     {hb_headline}")
        if hb_detail:
            print(f"                     {hb_detail}")
        print(f"  blocks (by reason) funding_long={blocks['funding_block_long']}  "
              f"funding_short={blocks['funding_block_short']}  "
              f"volume_conf={blocks['volume_confirmation_block']}  "
              f"other={blocks['other_block']}")
        print()
    if not any_running:
        print("(no variants running)")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    print(SAFETY_BANNER)
    print()
    print(f"PAPER LAB COMPARE  ({datetime.now(timezone.utc).isoformat(timespec='seconds')})")
    print("=" * 102)
    header = (
        f"{'variant':<24}  {'trades':>6}  {'return%':>8}  {'DD%':>6}  "
        f"{'PF':>6}  {'win%':>6}  {'open':>4}  {'last action':<25}"
    )
    print(header)
    print("-" * 102)
    rows_summary: list[dict] = []
    for name, cfg in VARIANTS.items():
        trades = _load_trades(name)
        stats = _compute_stats(trades)
        hb = _load_heartbeat(name)
        open_positions = 0
        if hb:
            for blk in (hb.get("assets") or {}).values():
                if blk.get("position_open"):
                    open_positions += 1
        if trades:
            last = trades[-1]
            last_action = (
                f"close {last.get('asset','?')} "
                f"@ {last.get('closed_at','?')[:19]} "
                f"({last.get('exit_reason','?')})"
            )
        else:
            last_action = "(no trades yet)"
        print(
            f"{name:<24}  {stats.n:>6}  {stats.total_return_pct:>+8.2f}  "
            f"{stats.max_dd_pct:>6.2f}  {stats.pf_str:>6}  {stats.win_pct:>6.1f}  "
            f"{open_positions:>4}  {last_action:<25}"
        )
        rows_summary.append({
            "variant": name,
            "config": cfg,
            "trades": stats.n,
            "winners": stats.n_winners,
            "losers": stats.n_losers,
            "win_pct": stats.win_pct,
            "pf": stats.pf_str,
            "total_return_pct": stats.total_return_pct,
            "max_dd_pct": stats.max_dd_pct,
            "ret_per_exposure_pct": stats.ret_per_exp_pct,
            "open_positions": open_positions,
            "last_action": last_action,
        })
        _summary_file(name).write_text(json.dumps(rows_summary[-1], indent=2))
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    if args.variant == "all":
        # Multi-tail: stream all variants concurrently, prefixing each
        # line with its variant name so a single terminal can watch
        # the whole lab at once. Requires --follow.
        if not args.follow:
            print("--variant all only makes sense with --follow", file=sys.stderr)
            return 2
        return _cmd_logs_follow_all(args.lines)

    if args.variant not in VARIANTS:
        print(f"unknown variant: {args.variant}", file=sys.stderr)
        print(f"available: {', '.join(VARIANTS.keys())} (or 'all' with --follow)",
              file=sys.stderr)
        return 2
    p = _log_file(args.variant)
    if not p.exists():
        print(f"(no log yet: {p})")
        return 0
    n = args.lines

    if args.follow:
        return _cmd_logs_follow_one(args.variant, p, n)

    lines = p.read_text(errors="replace").splitlines()
    for line in lines[-n:]:
        print(line)
    return 0


def _cmd_logs_follow_one(variant: str, p: Path, tail_lines: int) -> int:
    """`tail -f` one variant's log. Ctrl-C to exit."""
    # Switch stdout to line-buffered so streaming works under any
    # redirection (pipe, file, tee). Without this, follow mode looks
    # frozen when output is not a tty.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    print(f"# follow {variant}  log={p.relative_to(ROOT)}  (Ctrl-C to exit)",
          flush=True)
    # Print the last `tail_lines` lines first, then stream new content.
    with p.open("r", errors="replace") as f:
        existing = f.read().splitlines()
        for line in existing[-tail_lines:]:
            print(line, flush=True)
        # Seek to end and follow.
        f.seek(0, 2)
        try:
            while True:
                line = f.readline()
                if not line:
                    # No new data; check the worker is still alive.
                    if is_running(variant) is None:
                        # Worker died — keep tailing in case it gets
                        # restarted (file may grow again).
                        pass
                    time.sleep(0.5)
                    continue
                sys.stdout.write(line)
                sys.stdout.flush()
        except KeyboardInterrupt:
            print()
            print(f"# stopped following {variant}")
            return 0


def _cmd_logs_follow_all(tail_lines: int) -> int:
    """Stream all variants concurrently, one prefixed line per write.

    Uses one filehandle per variant + a simple round-robin readline
    poll. No threads, no select on regular files (which is unsupported
    for regular files anyway on most platforms — they always read
    ready).
    """
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    print("# follow ALL variants  (Ctrl-C to exit)", flush=True)
    handles: dict[str, "any"] = {}
    for name in VARIANTS:
        p = _log_file(name)
        if not p.exists():
            print(f"# (no log yet for {name}; will pick up when created)",
                  flush=True)
            continue
        f = p.open("r", errors="replace")
        existing = f.read().splitlines()
        for line in existing[-tail_lines:]:
            print(f"[{name}] {line}", flush=True)
        f.seek(0, 2)
        handles[name] = f

    try:
        while True:
            saw_any = False
            # Pick up any newly-created logs.
            for name in VARIANTS:
                if name in handles:
                    continue
                p = _log_file(name)
                if p.exists():
                    f = p.open("r", errors="replace")
                    f.seek(0, 2)
                    handles[name] = f
                    print(f"# log for {name} appeared, tracking", flush=True)
            for name, f in handles.items():
                while True:
                    line = f.readline()
                    if not line:
                        break
                    saw_any = True
                    sys.stdout.write(f"[{name}] {line}")
            if saw_any:
                sys.stdout.flush()
            else:
                time.sleep(0.5)
    except KeyboardInterrupt:
        print()
        print("# stopped following ALL variants")
        for f in handles.values():
            f.close()
        return 0


def cmd_self_test(args: argparse.Namespace) -> int:
    print("self-test: paper_lab")
    failures = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        if ok:
            print(f"  [ok]  {name}")
        else:
            print(f"  [FAIL] {name} -- {detail}")
            failures += 1

    # 1. Variants registry covers all 4 expected configs and points
    #    only at canonical state/ files (no mutation).
    expected = {
        "current_best",
        "no_volume_confirmation",
        "funding_only",
        "long_only_baseline",
    }
    check("VARIANTS keys = 4 expected variants", set(VARIANTS) == expected,
          f"got {set(VARIANTS)}")
    for cfg in VARIANTS.values():
        check(f"variant cfg lives under state/: {cfg}",
              cfg.startswith("state/") and (ROOT / cfg).exists(),
              f"missing {cfg}")

    # 2. Main state guard list is intact and points at the right paths.
    guarded_names = {p.name for p in MAIN_STATE_FILES_GUARDED}
    check("MAIN_STATE_FILES_GUARDED contains heartbeat.json",
          "heartbeat.json" in guarded_names)
    check("MAIN_STATE_FILES_GUARDED contains trades.jsonl",
          "trades.jsonl" in guarded_names)
    check("MAIN_STATE_FILES_GUARDED contains forward_paper_test.json",
          "forward_paper_test.json" in guarded_names)

    # 3. Each variant's heartbeat / trades path is OUTSIDE the main
    #    state directory. This is the core isolation invariant.
    main_state_dir = (ROOT / "state").resolve()
    for name in VARIANTS:
        vd = _variant_dir(name).resolve()
        check(f"{name}: variant dir lives under main state",
              str(vd).startswith(str(main_state_dir)))
        # but each variant has its OWN heartbeat path distinct from
        # the main forward-test heartbeat
        check(f"{name}: heartbeat path != main heartbeat",
              _heartbeat_path(name).resolve()
              != (ROOT / "state" / "heartbeat.json").resolve())
        check(f"{name}: trades path != main trades.jsonl",
              _trades_path(name).resolve()
              != (ROOT / "state" / "trades.jsonl").resolve())

    # 4. Subprocess command is constructed from sys.executable +
    #    -m hermes_trading.run, not via a shell, and uses --verbose
    #    + an absolute config path. No exchange-order flag exists.
    cmd = build_command("state/live_multiasset_long_short_funding_vol_volconf.yaml")
    check("build_command uses sys.executable", cmd[0] == sys.executable,
          f"got {cmd[0]!r}")
    check("build_command uses -m hermes_trading.run",
          cmd[1] == "-m" and cmd[2] == "hermes_trading.run",
          f"got {cmd[1:3]!r}")
    check("build_command passes --config + --verbose",
          "--config" in cmd and "--verbose" in cmd,
          f"got {cmd!r}")
    check("build_command does NOT mention 'live' / 'real' / 'order' / 'execute'",
          not any(t.lower() in " ".join(cmd).lower()
                  for t in ["--live", "--real", "--order",
                            "--execute", "real-money"]),
          f"got {cmd!r}")

    # 5. Source-level safety: no exchange order calls in this module.
    # Forbidden tokens are built via concatenation so the source text
    # doesn't itself contain the literal joined strings.
    src = Path(__file__).read_text()
    forbidden = [
        "create" + "_order",
        "create" + "_market_order",
        "create" + "_limit_order",
        "place" + "_order",
        "real" + "_money",
        "live" + "_order",
    ]
    for tok in forbidden:
        check(f"paper_lab source does NOT contain '{tok}'", tok not in src)
    check("safety banner present in source",
          "Shadow paper lab only. No real-money orders are sent." in src)

    # 6. ensure_isolation creates the right symlinks and copies, and
    #    does NOT touch main state files.
    main_hb_before = ((ROOT / "state" / "heartbeat.json").read_text()
                      if (ROOT / "state" / "heartbeat.json").exists()
                      else None)
    main_tr_before = ((ROOT / "state" / "trades.jsonl").read_text()
                      if (ROOT / "state" / "trades.jsonl").exists()
                      else None)
    main_fp_before = (ROOT / "state" / "forward_paper_test.json").read_text()

    name = "current_best"
    ensure_isolation(name, VARIANTS[name])
    vd = _variant_dir(name)
    check("ensure_isolation: variant dir exists", vd.exists())
    check("ensure_isolation: positions/ subdir exists",
          (vd / "positions").exists())
    check("ensure_isolation: goal.yaml copied",
          (vd / "goal.yaml").exists())
    check("ensure_isolation: shared state symlink at lab_root/state",
          (LAB_ROOT / "state").is_symlink())
    check("ensure_isolation: data symlink per variant",
          (vd / "data").is_symlink())
    check("ensure_isolation: data symlink resolves to <root>/state/data",
          (vd / "data").resolve() == (ROOT / "state" / "data").resolve())

    main_hb_after = ((ROOT / "state" / "heartbeat.json").read_text()
                     if (ROOT / "state" / "heartbeat.json").exists()
                     else None)
    main_tr_after = ((ROOT / "state" / "trades.jsonl").read_text()
                     if (ROOT / "state" / "trades.jsonl").exists()
                     else None)
    main_fp_after = (ROOT / "state" / "forward_paper_test.json").read_text()
    check("main heartbeat untouched after ensure_isolation",
          main_hb_after == main_hb_before)
    check("main trades.jsonl untouched after ensure_isolation",
          main_tr_after == main_tr_before)
    check("main forward_paper_test.json untouched after ensure_isolation",
          main_fp_after == main_fp_before)

    # 7. is_running returns None for a fake/dead pid.
    # Uses a synthetic variant name so we never touch a real worker's
    # pid file (rewriting it would bump mtime and confuse
    # _classify_heartbeat into reporting "warming up" the next time the
    # operator runs `status`).
    synth_name = "_selftest_dead_pid"
    fake_pid_path = LAB_ROOT / synth_name / "worker.pid"
    fake_pid_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fake_pid_path.write_text("99999999")
        # Direct call: is_running uses _pid_file(name) so we need the
        # synthetic name to resolve to the path we just wrote.
        # _pid_file is just LAB_ROOT / name / "worker.pid", so this works.
        check("is_running returns None for dead PID",
              is_running(synth_name) is None)
    finally:
        fake_pid_path.unlink(missing_ok=True)
        try:
            fake_pid_path.parent.rmdir()
        except OSError:
            pass

    # 8. Stats: empty trades -> zeros.
    s_empty = _compute_stats([])
    check("compute_stats empty -> n=0", s_empty.n == 0)
    check("compute_stats empty -> pf_str '0.00'",
          s_empty.pf_str == "0.00")
    check("compute_stats empty -> all-zero numeric fields",
          s_empty.total_return_pct == 0.0 and s_empty.max_dd_pct == 0.0)

    # 9. Stats: known fixtures.
    fixt = [
        {"status": "closed", "asset": "BTC/USDT", "return": -0.01,
         "closed_at": "2026-06-01T00:00:00+00:00"},
        {"status": "closed", "asset": "BTC/USDT", "return": +0.03,
         "closed_at": "2026-06-02T00:00:00+00:00"},
        {"status": "closed", "asset": "ETH/USDT", "return": -0.005,
         "closed_at": "2026-06-03T00:00:00+00:00"},
    ]
    s = _compute_stats(fixt)
    check("compute_stats: n=3", s.n == 3)
    check("compute_stats: 1 winner / 2 losers",
          s.n_winners == 1 and s.n_losers == 2)
    check("compute_stats: PF = 0.03 / 0.015 = 2.00",
          s.pf_str == "2.00", f"got {s.pf_str!r}")
    expected_ret = (0.99 * 1.03 * 0.995 - 1.0) * 100.0
    check("compute_stats: total return compound-correct",
          abs(s.total_return_pct - expected_ret) < 1e-9,
          f"got {s.total_return_pct} expected {expected_ret}")

    # 10. The static parts of the lab do NOT import an exchange-execution path.
    # Forbidden tokens built via concatenation; this source itself must not
    # contain the literal joined strings (the variable name and check-name
    # would otherwise re-introduce them and trip check #5).
    main_run_src = (ROOT / "hermes_trading" / "run.py").read_text()
    for label, tok in [
        ("ccxt order-creation entry point", "create" + "_order"),
        ("real-money execution import",     "real" + "_money"),
    ]:
        check(f"hermes_trading.run has no {label}", tok not in main_run_src)

    # 11. Heartbeat classifier covers warmup / fresh / stale / prior-run.
    import tempfile as _tmp
    with _tmp.TemporaryDirectory() as _td:
        _td_path = Path(_td)
        # Monkey-patch _pid_file / _heartbeat_path to point into the
        # temp dir so the test does not race with real state files.
        _orig_pid = globals()["_pid_file"]
        _orig_hb = globals()["_heartbeat_path"]
        globals()["_pid_file"] = lambda n: _td_path / f"{n}.pid"
        globals()["_heartbeat_path"] = lambda n: _td_path / f"{n}.hb.json"
        try:
            now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

            # 11a. Worker stopped, no heartbeat -> "(none)"
            headline, detail = _classify_heartbeat("v1", None, None, now=now)
            check("classify: stopped + no hb -> (none)",
                  headline == "(none)" and detail == "")

            # 11b. Worker stopped, old heartbeat -> "from previous run"
            old_hb = {"ts": "2026-05-31T11:00:00+00:00"}
            headline, _ = _classify_heartbeat("v1", None, old_hb, now=now)
            check("classify: stopped + old hb -> 'from previous run'",
                  "from previous run" in headline,
                  f"got {headline!r}")

            # 11c. Worker running, no heartbeat yet, PID file fresh -> warming up
            pid_path = _td_path / "v2.pid"
            pid_path.write_text("99999")
            mtime = (now - timedelta(seconds=8)).timestamp()
            os.utime(pid_path, (mtime, mtime))
            headline, _ = _classify_heartbeat("v2", 99999, None, now=now)
            check("classify: running + no hb -> warming up + worker age",
                  "warming up" in headline and "8s ago" in headline,
                  f"got {headline!r}")

            # 11d. Worker running, heartbeat predates pid mtime -> warming up,
            #     show prior-run hb in detail
            old_hb2 = {"ts": "2026-06-01T11:59:30+00:00"}  # 30s old, BEFORE pid (8s old)
            headline, detail = _classify_heartbeat("v2", 99999, old_hb2, now=now)
            check("classify: running + pre-pid hb -> warming up headline",
                  "warming up" in headline,
                  f"got {headline!r}")
            check("classify: running + pre-pid hb -> prior-run detail",
                  "prior-run heartbeat" in detail and "30s ago" in detail,
                  f"got {detail!r}")

            # 11e. Worker running, fresh heartbeat (after pid mtime, < 60s) -> fresh
            pid_path2 = _td_path / "v3.pid"
            pid_path2.write_text("77777")
            mtime2 = (now - timedelta(seconds=30)).timestamp()
            os.utime(pid_path2, (mtime2, mtime2))
            fresh_hb = {"ts": "2026-06-01T11:59:55+00:00"}  # 5s ago, after pid (30s ago)
            headline, detail = _classify_heartbeat("v3", 77777, fresh_hb, now=now)
            check("classify: running + fresh hb -> clean age line",
                  "5s ago" in headline
                  and "warming up" not in headline
                  and "STALE" not in headline,
                  f"got {headline!r}")

            # 11f. Worker running, but heartbeat is > 60s old AND newer than
            #     pid mtime (pid older still) -> STALE
            pid_path3 = _td_path / "v4.pid"
            pid_path3.write_text("66666")
            mtime3 = (now - timedelta(seconds=300)).timestamp()
            os.utime(pid_path3, (mtime3, mtime3))
            stale_hb = {"ts": "2026-06-01T11:58:00+00:00"}  # 120s ago, after pid (300s ago)
            headline, _ = _classify_heartbeat("v4", 66666, stale_hb, now=now)
            check("classify: running + 120s-old hb (post-pid) -> STALE",
                  "STALE" in headline,
                  f"got {headline!r}")
        finally:
            globals()["_pid_file"] = _orig_pid
            globals()["_heartbeat_path"] = _orig_hb

    # 12. Argparse: --follow, --tail, -f all set follow=True.
    parser_t = argparse.ArgumentParser(prog="paper_lab")
    sub_t = parser_t.add_subparsers(dest="cmd")
    p_logs_t = sub_t.add_parser("logs")
    p_logs_t.add_argument("--variant", required=True)
    p_logs_t.add_argument("--lines", type=int, default=50)
    p_logs_t.add_argument("--follow", "-f", "--tail",
                          action="store_true", dest="follow")
    for flag in ["--follow", "--tail", "-f"]:
        ns = parser_t.parse_args(["logs", "--variant", "current_best", flag])
        check(f"argparse: '{flag}' sets follow=True", ns.follow is True)
    ns_default = parser_t.parse_args(["logs", "--variant", "current_best"])
    check("argparse: no flag -> follow=False", ns_default.follow is False)

    if failures:
        print(f"\nSELF-TEST FAILED ({failures})")
        return 1
    print("\nSELF-TEST PASSED")
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return cmd_self_test(argparse.Namespace())

    parser = argparse.ArgumentParser(prog="paper_lab")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("start")
    sub.add_parser("stop")
    sub.add_parser("status")
    sub.add_parser("compare")

    p_logs = sub.add_parser("logs")
    p_logs.add_argument("--variant", required=True,
                        help="variant name, or 'all' (with --follow/--tail)")
    p_logs.add_argument("--lines", type=int, default=50)
    p_logs.add_argument("--follow", "-f", "--tail", action="store_true",
                        dest="follow",
                        help="stream new log lines as they arrive (tail -f)")

    args = parser.parse_args(argv)

    if args.cmd == "start":
        return cmd_start(args)
    if args.cmd == "stop":
        return cmd_stop(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "compare":
        return cmd_compare(args)
    if args.cmd == "logs":
        return cmd_logs(args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
