---
id: TASK-20.1
title: >-
  Log upstream error events (body excerpt, status, provider) on non-200
  responses
status: Done
assignee: []
created_date: '2026-09-07 18:54'
updated_date: '2026-09-07 19:05'
labels: []
dependencies: []
references:
  - src/optiproxai/dashboard.py
  - src/optiproxai/proxy.py
  - tests/test_upstream_error_logging.py
  - README.md
parent_task_id: TASK-20
priority: high
type: feature
ordinal: 23010
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
optiproxai has no forensic record of upstream error responses. Routing logs (routing-*.jsonl) record classification only; execution logs (execution-*.jsonl) record successful usage only (written by `_log_usage`/`log_execution_event`, proxy.py:709, only on HTTP 200); the server console log wraps error bodies into `_openai_error(429, "Upstream error: {raw[:500]}")` which goes to the client only and is never persisted. This gap blocked the TASK-20 investigation: the synthetic billing 429 body ("You've exceeded your subscription rate limits...") exists only as a truncated (500-char) client-side relay.

Add a failure-variant log event on every non-200 upstream response (both streaming and non-streaming paths in `_proxy_upstream`), recorded to BOTH:
1. the execution JSONL log (new event type, e.g. `log_execution_error`), with: request_id, timestamp, provider, model, status_code, error_type, bounded raw-body excerpt (e.g. 500 chars), and retry-after header hint if present;
2. a server-log line at WARNING level with the same fields.

No routing/retry behavior change. Successor TASK-20.02 will pin its classifier patterns against real captured bodies from this logging.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Failure-variant logging function (e.g. `log_execution_error` in logger.py) writes an execution-log JSONL event with request_id, provider, model, status_code, error_type, raw-body excerpt (bounded, 500 chars), and retry-after hint when present.
- [x] #2 Both streaming (line 783) and non-streaming (line 862) non-200 paths in `_proxy_upstream` call the failure logging before returning the wrapped client error.
- [x] #3 Server log records a WARNING-level line with the same fields on each non-200 upstream response.
- [x] #4 Body excerpt is truncated to the configured bound and safely handles non-UTF8/empty bodies.
- [x] #5 Existing tests pass; new tests cover: streaming and non-streaming error paths both emit the event; body excerpt truncation; missing/absent retry-after handled gracefully.
- [x] #6 No change to routing, cooldown, or retry behavior (verified by existing fallback/backoff tests passing unchanged).
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
## Implementation Plan

**Goal:** persist upstream error responses (status + body excerpt + provider context) to execution JSONL and server log on every non-200 upstream response, with zero routing-behavior change.

### Files touched
1. `src/optiproxai/logger.py`
   - Add `log_execution_error(...)`: writes a JSONL event (same writer/rotation machinery as `log_execution_event`) with fields: `event_type="upstream_error"`, `request_id`, `provider`, `model`, `status_code`, `error_type`, `body_excerpt` (bounded), `retry_after` (header value or None), `timestamp`.
   - Bounded excerpt: truncate to 500 chars, decode with `errors="replace"`, empty body → empty string.
   - Follow existing graceful-degradation pattern (logging failures must never break the request path).
2. `src/optiproxai/proxy.py` — `_proxy_upstream`
   - Non-streaming path (~line 862): on `resp.status_code != 200`, extract `resp.text` excerpt and `retry-after` header; call `log_execution_error`; add a `logger.warning("UPSTREAM_ERROR ...")` line (standard formatter) before returning `_openai_error`.
   - Streaming path (~line 783): on `upstream.status_code != 200`, same treatment using the already-read `raw` bytes.
   - Both call sites pass provider/model from the candidate being tried (works for primary and fallback attempts alike).
3. `tests/test_router_logging.py` (or a new `tests/test_upstream_error_logging.py`)
   - Streaming non-200 emits JSONL event + warning line.
   - Non-streaming non-200 emits event; body excerpt truncation at 500; non-UTF8 body handled; missing retry-after → None; retry-after present → captured.
   - Regression: fallback/backoff tests unchanged (no behavior coupling).

### Sequence
1. logger.py: `log_execution_error` + unit test for writer shape.
2. proxy.py: wire both paths (smallest diff, reuse existing variables `raw` / `resp.text`).
3. Tests; run `uv run pytest tests/ -q -k "logging or proxy"` then full suite.
4. README logging section: document the new event type (one short paragraph).

### Risks / notes
- Config hot-reload: `log_execution_error` must live on the same module-level logger wiring as `log_execution_event` so it survives reloads identically.
- JSONL event adds ~one line per failed upstream call - acceptable volume; body excerpt bounded.
- No changes to `_is_retryable_error`, `_record_retryable_failure`, fallback_backoff, or router - explicitly out of scope (TASK-20.02).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implementation completed 2026-09-07 on branch task/TASK-20.01. Plan deviation approved by user: function lives in dashboard.py (not logger.py) and error events share the execution-*.jsonl stream + dashboard DB, flagged with event_type=upstream_error so the dashboard-info task can surface them.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Implemented upstream error event logging on every non-200 upstream response (TASK-20.01).

**What was built**
- `dashboard.py`: new `log_execution_error()` beside `log_execution_event` — writes an `upstream_error` record (event_type, request_id, model, provider, profile, status_code, error_type, bounded 500-char body_excerpt, retry_after, elapsed_ms) to the same daily `execution-YYYY-MM-DD.jsonl` stream AND to the dashboard DB. Dashboard DB migration adds columns: event_type, status_code, error_type, body_excerpt, retry_after (via existing `_ensure_column` pattern), so the pending dashboard-info task can surface error occurrences. Insert helper `_insert_execution_error_record` skips token-count columns; rows ingest cleanly.
- `proxy.py`: new `_log_upstream_error()` helper wires both non-200 paths in `_proxy_upstream` (streaming ~line 834, non-streaming ~line 920): calls `log_execution_error` + emits a standard-formatter `UPSTREAM_ERROR status=... provider=... model=... body=...` WARNING server-log line. Non-UTF8 bodies decoded with errors=replace; missing headers/retry-after handled gracefully.
- `tests/test_upstream_error_logging.py`: 12 new tests — JSONL field shape, 500-char truncation, empty/non-UTF8 bodies, missing retry-after, server-log warning, dashboard DB persistence, streaming + non-streaming wiring through `_proxy_upstream`, and a no-behavior-change regression (logging an error does not create backoff state).
- README: new paragraph under "Routing logs and classifier training" documenting the upstream_error event type.

**Deviations from approved plan**
- Function placed in `dashboard.py` (not `logger.py`): user directed same-stream placement after confirming execution-logging lives there (proxy imports `log_execution_event` from dashboard).
- Error events share the existing `execution-*.jsonl` stream (user decision: dashboard should be able to show error occurrences later), differentiated by `event_type="upstream_error"` and zero token counts.

**Test outcome**
Full CI gate green: ruff check passed, format check 40 files clean, pyright 0 errors, 448 tests passed (436 baseline + 12 new), uv build succeeded.

**No routing/cooldown/retry behavior change** (AC #6 verified).
<!-- SECTION:FINAL_SUMMARY:END -->
