"""XDG Base Directory paths for optiproxai.

Always uses Linux-style XDG paths, even on macOS (ignores ~/Library/...):
- Config: $XDG_CONFIG_HOME/optiproxai  (default: ~/.config/optiproxai)
- Data:   $XDG_DATA_HOME/optiproxai    (default: ~/.local/share/optiproxai)
- Cache:  $XDG_CACHE_HOME/optiproxai   (default: ~/.cache/optiproxai)
- Logs:   $XDG_STATE_HOME/optiproxai/log (default: ~/.local/state/optiproxai/log)

All paths can be overridden with environment variables:
- OPTIPROXAI_CONFIG_DIR → config directory
- OPTIPROXAI_LOG_DIR    → log directory
- OPTIPROXAI_DATA_DIR   → data directory
"""

from __future__ import annotations

import os
from pathlib import Path


def _xdg_dir(env_var: str, default_subdir: str) -> Path:
    """Resolve an XDG directory, always using Linux-style defaults."""
    base = os.environ.get(env_var, "").strip()
    if not base:
        base = str(Path.home() / default_subdir)
    p = Path(base) / "optiproxai"
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_dir() -> Path:
    """Return the config directory for optiproxai."""
    if env := os.environ.get("OPTIPROXAI_CONFIG_DIR"):
        return Path(env).expanduser()
    return _xdg_dir("XDG_CONFIG_HOME", ".config")


def log_dir() -> Path:
    """Return the log directory for optiproxai."""
    if env := os.environ.get("OPTIPROXAI_LOG_DIR"):
        return Path(env).expanduser()
    return _xdg_dir("XDG_STATE_HOME", ".local/state") / "log"


def data_dir() -> Path:
    """Return the data directory for optiproxai."""
    if env := os.environ.get("OPTIPROXAI_DATA_DIR"):
        return Path(env).expanduser()
    return _xdg_dir("XDG_DATA_HOME", ".local/share")
