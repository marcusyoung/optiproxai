"""Shared test fixtures — isolates log/data dirs to prevent production pollution.

RoutingLogger._log_dir and dashboard._DASHBOARD_DB_PATH are both resolved at
module import time from env vars. Setting the env var alone is insufficient
because the modules are already imported by the time fixtures run, so we also
call RoutingLogger.set_log_dir() and monkeypatch dashboard._DASHBOARD_DB_PATH
to override the already-resolved module-level paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from optiproxai import dashboard
from optiproxai.logger import RoutingLogger


@pytest.fixture(autouse=True)
def _isolate_log_and_data_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect OPTIPROXAI_LOG_DIR and OPTIPROXAI_DATA_DIR to temp dirs for every test.

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
