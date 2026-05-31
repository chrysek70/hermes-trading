"""Self-improving paper-trading worker."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml
from rich.console import Console

__version__ = "0.1.0"

_console = Console()


def _default_state_dir() -> Path:
    # Package lives at <root>/hermes_trading; state at <root>/state.
    # On Railway <root> is /app, locally it is ~/hermes-trading.
    return Path(__file__).resolve().parent.parent / "state"


STATE_DIR = Path(os.getenv("HERMES_STATE_DIR", str(_default_state_dir())))


#: Module-level display mode for ``log()`` output.
#:
#: **Default is "local"** — interactive monitoring is the primary use
#: case for the screen. UTC remains the canonical storage format on
#: disk regardless: ``now_iso()`` and every persisted timestamp stay
#: UTC. ``set_display_time_mode("utc")`` (or ``--utc-time`` on the
#: CLI) restores UTC display for cross-machine debugging / log
#: correlation.
_DISPLAY_TIME_MODE = "local"
DISPLAY_TIME_MODES = ("utc", "local", "market")


def set_display_time_mode(mode: str) -> None:
    """Set the display mode for ``log()`` line timestamps.

    ``"utc"``    : default; UTC HH:MM:SS, no abbreviation.
    ``"local"``  : convert to the host OS timezone (auto-detected via
                   ``datetime.astimezone()``). Adds the local tz
                   abbreviation (e.g. ``EDT`` / ``CST`` / ``MST``).
    ``"market"`` : reserved for future stock-strategy support
                   (``America/New_York``). NotImplemented for now —
                   wired so future code can drop in without touching
                   call sites.

    Has NO effect on persisted artifacts. ``state/heartbeat.json``,
    ``state/trades.jsonl``, ``state/positions/``, reports and every
    JSON output remain UTC.
    """
    global _DISPLAY_TIME_MODE
    if mode not in DISPLAY_TIME_MODES:
        raise ValueError(
            f"unknown display time mode: {mode!r}; "
            f"valid: {DISPLAY_TIME_MODES}"
        )
    if mode == "market":
        raise NotImplementedError(
            "market timezone display is reserved for future "
            "stock-strategy support; not implemented in this issue"
        )
    _DISPLAY_TIME_MODE = mode


def get_display_time_mode() -> str:
    return _DISPLAY_TIME_MODE


def format_display_time(
    utc_timestamp: datetime | None = None,
    mode: str | None = None,
) -> tuple[str, str]:
    """Format a UTC timestamp for display.

    Returns ``(time_str, tz_abbrev)``. ``mode`` overrides the module
    setting if given; otherwise the module default is used.

    Examples (host in EDT):
        >>> format_display_time(mode="utc")
        ("02:39:01", "")
        >>> format_display_time(mode="local")
        ("22:39:01", "EDT")
    """
    if utc_timestamp is None:
        utc_timestamp = datetime.now(timezone.utc)
    elif utc_timestamp.tzinfo is None:
        utc_timestamp = utc_timestamp.replace(tzinfo=timezone.utc)
    mode = mode or _DISPLAY_TIME_MODE
    if mode == "local":
        local = utc_timestamp.astimezone()
        tz_abbrev = local.strftime("%Z") or ""
        return local.strftime("%H:%M:%S"), tz_abbrev
    # default / explicit utc — no abbreviation (consistent with prior format)
    return utc_timestamp.strftime("%H:%M:%S"), ""


def log(msg: str) -> None:
    """Print a timestamped log line. Respects the module-level display
    mode (UTC default; ``set_display_time_mode("local")`` switches to
    host-OS local timezone). Persisted artifacts are unaffected.
    """
    ts_str, tz_abbrev = format_display_time()
    prefix = f"{ts_str} {tz_abbrev}" if tz_abbrev else ts_str
    _console.print(f"[dim]{prefix}[/dim] {msg}")


def now_iso() -> str:
    """Canonical UTC ISO-8601 timestamp. **Unaffected** by display
    mode — this is the function used by every persistence path
    (heartbeat, trades, positions). Display mode is a presentation
    concern only."""
    return datetime.now(timezone.utc).isoformat()


def load_yaml(path: Path) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


def save_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
