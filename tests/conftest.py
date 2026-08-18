"""Shared test fixtures — isolates log/data dirs to prevent production pollution.

RoutingLogger._log_dir and dashboard._DASHBOARD_DB_PATH are both resolved at
module import time from env vars. Setting the env var alone is insufficient
because the modules are already imported by the time fixtures run, so we also
call RoutingLogger.set_log_dir() and monkeypatch dashboard._DASHBOARD_DB_PATH
to override the already-resolved module-level paths.

Env vars are set BEFORE importing optiproxai modules so that import-time path
resolution (dirs._xdg_dir mkdir) picks up test dirs instead of production.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Set env vars before importing optiproxai modules. Without this, the imports
# below trigger dirs._xdg_dir() which creates ~/.local/* optiproxai directories
# on production paths during test collection.
_session_log_dir = Path(tempfile.mkdtemp(prefix="optiproxai-test-log-"))
_session_data_dir = Path(tempfile.mkdtemp(prefix="optiproxai-test-data-"))
os.environ.setdefault("OPTIPROXAI_LOG_DIR", str(_session_log_dir))
os.environ.setdefault("OPTIPROXAI_DATA_DIR", str(_session_data_dir))

from optiproxai import dashboard  # noqa: E402
from optiproxai.logger import RoutingLogger  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_log_and_data_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect OPTIPROXAI_LOG_DIR and OPTIPROXAI_DATA_DIR to per-test temp dirs.

    This prevents tests that call Router.route() or exercise the proxy from
    writing routing logs or dashboard data to production paths.
    """
    log_dir = tmp_path / "log"
    data_dir = tmp_path / "data"
    log_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("OPTIPROXAI_LOG_DIR", str(log_dir))
    monkeypatch.setenv("OPTIPROXAI_DATA_DIR", str(data_dir))
    RoutingLogger.set_log_dir(log_dir)
    monkeypatch.setattr(dashboard, "_DASHBOARD_DB_PATH", data_dir / "dashboard.db")

    yield
