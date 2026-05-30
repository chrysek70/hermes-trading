"""Per-asset position state helpers.

Used by both the single-asset live worker (`loop.py`) and the
multi-asset live worker (`multi_loop.py`). Centralises read / write /
migrate so single-asset behaviour stays bit-for-bit identical to the
pre-refactor implementation.

Layout on disk:

    state/position.json                          # legacy single-asset (kept compatible)
    state/positions/<ASSET_KEY>.json             # multi-asset, one file per asset

`ASSET_KEY` replaces `/` with `_` in the ccxt symbol — e.g. ``BTC/USDT``
becomes ``BTC_USDT``. The mapping is reversible so the live worker can
recover which symbol a file belongs to without reading the JSON.

Corrupt files are tolerated: ``load_positions`` skips them with a
warning and continues with whatever loaded cleanly. The corrupt file
is left alone for the user to inspect.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import STATE_DIR, log

POSITIONS_DIR_NAME = "positions"
LEGACY_POSITION_NAME = "position.json"


def asset_key(asset: str) -> str:
    """ccxt symbol -> filename-safe key (BTC/USDT -> BTC_USDT)."""
    return asset.replace("/", "_")


def asset_from_key(key: str) -> str:
    """Inverse of asset_key. BTC_USDT -> BTC/USDT (best effort)."""
    if "_" not in key:
        return key
    head, tail = key.split("_", 1)
    return f"{head}/{tail}"


def positions_dir(state_dir: Path | None = None) -> Path:
    base = state_dir or STATE_DIR
    return base / POSITIONS_DIR_NAME


def position_path(asset: str, state_dir: Path | None = None) -> Path:
    return positions_dir(state_dir) / f"{asset_key(asset)}.json"


def legacy_position_path(state_dir: Path | None = None) -> Path:
    base = state_dir or STATE_DIR
    return base / LEGACY_POSITION_NAME


# ---------- single-asset (legacy) IO ----------------------------------------

def load_legacy_position(state_dir: Path | None = None) -> dict | None:
    """Read state/position.json. Returns None on missing / corrupt / old-schema.
    Matches the original ``loop._load_position`` behaviour so single-asset
    mode is unaffected."""
    path = legacy_position_path(state_dir)
    try:
        with open(path) as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        log(f"[yellow]legacy position file unreadable ({exc}); ignoring[/yellow]")
        return None
    if not data or "entry_price" not in data:
        return None
    if "direction" not in data:
        log("[yellow]old-schema (v1 RSI) position on disk — abandoning to start clean[/yellow]")
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return None
    return data


def save_legacy_position(pos: dict, state_dir: Path | None = None) -> None:
    """Write state/position.json (single-asset mode)."""
    path = legacy_position_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pos))


def clear_legacy_position(state_dir: Path | None = None) -> None:
    try:
        legacy_position_path(state_dir).unlink()
    except FileNotFoundError:
        pass


# ---------- multi-asset IO --------------------------------------------------

def load_positions(assets: list[str], state_dir: Path | None = None) -> dict[str, dict]:
    """Read all per-asset position files. Returns a dict {asset: position}.
    Missing files / corrupt files / old-schema entries are skipped with a
    warning. Does not delete corrupt files — the user can inspect them."""
    out: dict[str, dict] = {}
    dpath = positions_dir(state_dir)
    if not dpath.exists():
        return out
    for asset in assets:
        path = position_path(asset, state_dir)
        if not path.exists():
            continue
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            log(f"[yellow]position file for {asset} is corrupt "
                f"({exc}); leaving it alone and continuing without it[/yellow]")
            continue
        if not isinstance(data, dict):
            log(f"[yellow]position file for {asset} is not an object; skipping[/yellow]")
            continue
        if "entry_price" not in data or "direction" not in data:
            log(f"[yellow]position file for {asset} missing required fields; skipping[/yellow]")
            continue
        out[asset] = data
    return out


def save_position(asset: str, pos: dict, state_dir: Path | None = None) -> None:
    path = position_path(asset, state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pos))


def clear_position(asset: str, state_dir: Path | None = None) -> None:
    try:
        position_path(asset, state_dir).unlink()
    except FileNotFoundError:
        pass


# ---------- migration -------------------------------------------------------

def migrate_legacy_position(
    inferred_asset: str,
    state_dir: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Move ``state/position.json`` to the new per-asset layout.

    Idempotent: if no legacy file exists, returns ``{"migrated": False}``.

    If a legacy file exists:
      1. read it
      2. write it to ``state/positions/<ASSET_KEY>.json``
      3. back up the original to
         ``state/position.json.bak.<UTC-iso>``
      4. delete the original (its content lives in the backup AND the
         new location)

    Returns a small dict describing what happened, for the caller's log.
    """
    base = state_dir or STATE_DIR
    legacy = legacy_position_path(state_dir)
    if not legacy.exists():
        return {"migrated": False, "reason": "no legacy file"}

    try:
        with open(legacy) as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return {"migrated": False, "reason": f"legacy unreadable: {exc}"}
    if not isinstance(data, dict) or "entry_price" not in data:
        return {"migrated": False, "reason": "legacy file empty or missing entry_price"}

    ts = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    backup = base / f"{LEGACY_POSITION_NAME}.bak.{ts}"
    shutil.copy2(legacy, backup)

    save_position(inferred_asset, data, state_dir)
    legacy.unlink()
    return {
        "migrated": True,
        "asset": inferred_asset,
        "new_path": str(position_path(inferred_asset, state_dir)),
        "backup_path": str(backup),
    }
