---
id: TASK-9
title: Fix test log pollution — isolate OPTIPROXAI_LOG_DIR in router/proxy tests
status: Done
assignee: []
created_date: '2026-08-18 15:29'
updated_date: '2026-08-18 15:53'
labels: []
dependencies: []
references:
  - tests/conftest.py
modified_files:
  - tests/conftest.py
priority: high
type: bug
ordinal: 13000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Tests that call Router.route() write to the real routing log dir (~/.local/state/optiproxai/log) because OPTIPROXAI_LOG_DIR is not isolated. This pollutes production routing logs with synthetic test fixtures (e.g. 'Refactor router and add tests for edge cases', 'hi', models model-medium/eco-a/eco-b), and the dashboard ingests them as real data.

Affected files (call route() but don't isolate OPTIPROXAI_LOG_DIR):
- tests/test_router_logging.py
- tests/test_proxy_reload.py
- tests/test_tier_override.py
- tests/test_capability_routing.py
- tests/test_input_limit_routing.py

Only tests/test_dashboard.py isolates OPTIPROXAI_LOG_DIR today. No conftest.py exists.

Fix options:
1. Add an autouse fixture in a new tests/conftest.py that sets OPTIPROXAI_LOG_DIR to a tmp_path for all tests.
2. Add per-file monkeypatch.setenv('OPTIPROXAI_LOG_DIR', ...) to each affected file.

Option 1 is preferred — single fix, covers all current and future tests.

Note: RoutingLogger._log_dir is resolved at import time from the env var, so the fixture must set the env var before the first import of optiproxai.logger, or call RoutingLogger.set_log_dir() to override the module-level path. The set_log_dir approach is already used in test_llm_classifier.py.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 No test writes routing logs to ~/.local/state/optiproxai/log (or the real log_dir)
- [x] #2 An autouse fixture in conftest.py (or per-file isolation) redirects OPTIPROXAI_LOG_DIR to a temp directory for all tests that exercise Router.route() or the proxy
- [x] #3 Running the full test suite does not add rows to the production dashboard DB
- [x] #4 Existing tests pass with the isolation in place
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Implementation complete (2026-08-18)

- Created `tests/conftest.py` with autouse `_isolate_log_and_data_dirs` fixture
- Isolates both `OPTIPROXAI_LOG_DIR` (env var + `RoutingLogger.set_log_dir()`) and `OPTIPROXAI_DATA_DIR` (env var + `monkeypatch.setattr` on `dashboard._DASHBOARD_DB_PATH`)
- 318/318 tests pass
- Production dashboard DB: delta=0 after test run
- Production routing log: 0 test-pollution lines after test run
- Pre-existing ruff F401 in `test_feature_training.py` (unused `pytest` import) is out of scope — CI runs `ruff check src/` not `tests/`
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
## Implementation

Created `tests/conftest.py` with a single autouse fixture `_isolate_log_and_data_dirs` that redirects both `OPTIPROXAI_LOG_DIR` and `OPTIPROXAI_DATA_DIR` to temp directories for every test.

### Key design decisions

1. **Two pollution vectors handled**: `RoutingLogger._log_dir` (logger.py:28-31) and `dashboard._DASHBOARD_DB_PATH` (dashboard.py:18) are both resolved at module import time. Setting env vars alone is insufficient since the modules are already imported by fixture time, so the fixture also calls `RoutingLogger.set_log_dir()` and `monkeypatch.setattr(dashboard, "_DASHBOARD_DB_PATH", ...)` to override the already-resolved paths.

2. **No existing test files modified**: The autouse fixture provides a baseline. Tests that already set their own `OPTIPROXAI_DATA_DIR` or `OPTIPROXAI_LOG_DIR` (test_dashboard.py, test_api_keys.py, test_tier_override.py, test_proxy_reload.py, test_cli.py) override the fixture's values for their own tests — this is expected and harmless.

3. **test_llm_classifier.py left as-is**: Its per-test `RoutingLogger.set_log_dir()` calls inside `with tempfile.TemporaryDirectory()` blocks are redundant now but harmless. Removing them risks breaking the test's own log-reading assertions.

### Verification

- 318/318 tests pass (`uv run pytest tests/ -q`)
- `ruff check src/` clean, `ruff format --check tests/conftest.py` clean, `pyright src/` 0 errors
- Pre-test snapshot: dashboard.db=1,822,720 bytes, routing log=3,896,208 bytes
- Post-test: dashboard.db delta=0 (no rows added), routing log delta=0 test-pollution lines (only real proxy traffic from this session)
- Note: `ruff check tests/` reports a pre-existing unused `pytest` import in `test_feature_training.py` — not introduced by this change, not in scope.

### Deviations from plan

None. Implemented exactly as planned.
<!-- SECTION:FINAL_SUMMARY:END -->
