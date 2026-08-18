---
id: TASK-9
title: Fix test log pollution — isolate OPTIPROXAI_LOG_DIR in router/proxy tests
status: To Do
assignee: []
created_date: '2026-08-18 15:29'
labels: []
dependencies: []
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
- [ ] #1 No test writes routing logs to ~/.local/state/optiproxai/log (or the real log_dir)
- [ ] #2 An autouse fixture in conftest.py (or per-file isolation) redirects OPTIPROXAI_LOG_DIR to a temp directory for all tests that exercise Router.route() or the proxy
- [ ] #3 Running the full test suite does not add rows to the production dashboard DB
- [ ] #4 Existing tests pass with the isolation in place
<!-- AC:END -->
