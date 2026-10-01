---
id: TASK-27
title: >-
  Enable INFO logging for optiproxai package loggers without duplicating proxy lines
status: To Do
assignee: []
created_date: '2026-10-01 17:49'
labels: []
dependencies: []
references:
  - src/optiproxai/proxy.py
  - src/optiproxai/scorer.py
  - src/optiproxai/router.py
  - src/optiproxai/api_keys.py
  - src/optiproxai/fallback_backoff.py
  - tests/test_api_keys.py
priority: low
type: chore
ordinal: 27000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Python's `logging` module installs a `lastResort` handler when no handler is found for a
record. That handler emits **WARNING and above only**. Only `optiproxai.proxy` currently
configures its own handler (`logging.getLogger("optiproxai.proxy")`, `setLevel(DEBUG)`,
a `StreamHandler(sys.stderr)`), and root logging is never configured via
`logging.basicConfig(...)`. As a result every other `optiproxai.*` logger is silently
truncated to WARNING+ and its INFO/DEBUG lines never reach `journalctl`/stderr.

Currently suppressed in production:
- `optiproxai.scorer` — `Loading distilled feature classifier model_path=%s` (INFO) and
  `Runtime distilled feature classifier loaded ...` (INFO), plus `Runtime embedding
  disabled, using default fallback` (INFO). These are the lines that confirm a retrained
  `feature_classifier.pkl` was actually loaded after a VPS retrain.
- `optiproxai.api_keys` — `API key created: name=... prefix=...` / `API key removed: ...`
  (INFO). Useful audit trail; very low volume.
- `optiproxai.fallback_backoff` — cooldown state transitions (INFO). Explains why a
  fallback was promoted.
- `optiproxai.router` — primary/fallback selection, session-sticky index, round-robin
  index (DEBUG). Per-request chatty; should stay off by default.

What is NOT lost: failures still surface (classifier load failure and embedding timeout are
WARNING, classification exceptions are ERROR). Per-request decisions are already covered
independently by the proxy `ROUTE`/`USAGE` lines on stderr, the `routing-*.jsonl` /
`execution-*.jsonl` files, the `X-Optiproxai-Tier` / `-Model` / `-Score` response headers,
and `/v1/route`. This task is therefore an observability/audit improvement, not a
correctness fix.

**Implementation trap (must handle):** `optiproxai.proxy` attaches its own handler and does
not set `propagate = False`. Today that is harmless because root has no handler, but the
moment a package/root handler is added at INFO every proxy line would be emitted **twice**
(its own handler plus the new one). The fix must either set `propagate = False` on the
proxy logger or restructure to a single handler on the `optiproxai` parent logger, and a
test must assert no duplicate lines.

Proposed approach (decide during planning):
- Configure logging once for the `optiproxai` package (single handler + formatter) in a
  shared helper / CLI entrypoint rather than per-module.
- Default level INFO for `optiproxai.*`, keeping `optiproxai.router` at WARNING unless an
  opt-in `OPTIPROXAI_LOG_LEVEL=DEBUG` (or equivalent) is set.
- Ensure no duplicate emission between the proxy logger and the package logger.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Identify every `optiproxai.*` logger and classify which INFO/DEBUG messages are currently suppressed by the `lastResort` WARNING threshold; document the list in the Implementation Notes.
- [ ] #2 A single logging configuration for the `optiproxai` package routes INFO (and above) from `scorer`, `api_keys` and `fallback_backoff` to stderr/journalctl.
- [ ] #3 `optiproxai.router` DEBUG selection detail is off by default and enabled only via an explicit opt-in env var (e.g. `OPTIPROXAI_LOG_LEVEL=DEBUG`).
- [ ] #4 No log line is emitted twice: `optiproxai.proxy`'s own handler vs the package handler are reconciled (via `propagate=False` or a single parent handler).
- [ ] #5 A test asserts the proxy logger does not double-emit once package logging is configured, and that an INFO record from a non-proxy `optiproxai.*` logger is captured.
- [ ] #6 `streamlit`/serve startup does not change CLI stdout shape or break existing proxy `ROUTE`/`USAGE` output.
- [ ] #7 Behavior can be verified on the VPS: after a retrain, `journalctl -u optiproxai` shows the `Loading distilled feature classifier` / `... loaded` confirmation lines.
- [ ] #8 No change to routing decisions or proxy API behavior; no unrelated logging refactor beyond this scope.
<!-- AC:END -->
