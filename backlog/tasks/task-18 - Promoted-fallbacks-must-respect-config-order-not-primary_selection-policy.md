---
id: TASK-18
title: 'Promoted fallbacks must respect config order, not primary_selection policy'
status: Done
assignee: []
created_date: '2026-08-28 16:29'
updated_date: '2026-09-07 18:19'
labels:
  - bug
  - routing
  - fallback
  - priority-order
dependencies: []
documentation:
  - >-
    decisions/doc-16 -
    Decision-promoted-fallback-selection-uses-config-order-not-primary_selection.md
priority: high
ordinal: 22000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Bug: when all primary candidates are filtered out and a fallback is promoted to primary at routing time, _select_primary_candidate() applies the tier's primary_selection policy (session_sticky hash or round-robin) to the fallback list instead of respecting config order.

Observed 2026-08-28 17:19 (analysis/COMPLEX, session_sticky): both Hy3 primaries input-limit filtered (prompt >160K), both fallbacks eligible (fallback_count=2: DeepSeek-V4-Pro@doubleword first, syn:large:vision@synthetic second per config). Session-sticky hash of the session key landed on index 1 → picked syn:large:vision (524K, second priority) instead of DeepSeek-V4-Pro (1M, first priority, deliberately listed first). Deterministic for that session key — the priority order in config is silently overridden by the hash.

Inconsistency: retry-time fallback (request failure after routing, proxy.py retry loop over decision.fallbacks) already walks the list in config order. Routing-time promotion uses hash/round-robin instead. Same list, two different selection semantics.

Fix (bug, no new config surface): when selection_candidates come from the fallback list (promoted_from_fallback=True in either promotion branch), select selection_candidates[0] — config order IS the policy, matching retry-time behavior. primary_selection (session_sticky / round_robin) applies to actual primary candidates only.

Decisions to settle in the decision record:
1. Promotion branches that should follow config order: input-limit promotion (line ~437) AND the all-primaries-cooling ignore-cooldown branch (line ~446, currently does not set promoted_from_fallback). Recommend both.
2. No new fallback_selection config: list order encodes priority (same as retry path).
3. Locality trade-off (documented, accepted): switching models at promotion forfeits provider prefix cache for the oversized prompt; but the previously-sticky primary is already excluded, so a different model means uncached regardless — priority wins.

Tests: promoted fallback respects config order under session_sticky (key hashing to index 1 still picks first-listed); same under round-robin (no rotation on promoted list, or at minimum order-respected first pick); primary selection unchanged (sticky + round-robin still apply when primaries exist); retry path (decision.fallbacks order) unchanged; single-fallback short-circuit unaffected.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 _select_primary_candidate returns candidates[0] when promoted_from_fallback=True, bypassing session_sticky hash and round-robin; len==1 still short-circuits first
- [x] #2 route() forwards its local promoted_from_fallback flag into the _select_primary_candidate call so both fallback-promotion branches (input-limit promotion ~437, all-fallbacks-cooling ~453) select the first-listed fallback
- [x] #3 Tests assert the first-listed fallback is selected under session_sticky (session key hashing to index 1) and under round-robin; primary selection (sticky + round-robin) unchanged; retry path unchanged; single-fallback short-circuit unaffected
- [x] #4 ruff check src/ passes
- [x] #5 ruff format --check src/ tests/ passes
- [x] #6 pyright src/ passes
- [x] #7 pytest tests/ -q passes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
# TASK-18 Implementation Plan: Promoted fallbacks respect config order

## Approach / Rationale

The routing-time fallback-promotion path in `Router.route()` sets `selection_candidates` to the fallback list when all primaries are filtered out (input-limit or cooldown), then calls `_select_primary_candidate()` with `filter_to_candidates=selection_candidates`. That method applies the tier's `primary_selection` policy (session_sticky hash / round-robin) to whatever list it receives. For a promoted fallback list, that silently overrides the config-declared priority order.

Fix (doc-16): add a `promoted_from_fallback: bool = False` parameter to `_select_primary_candidate()`. When `True`, the method returns `candidates[0]` (config order) and bypasses the session_sticky/round-robin logic. `route()` already maintains a local `promoted_from_fallback` flag set `True` in both fallback-promotion branches; pass it through. `primary_selection` continues to apply only to actual primaries. No new config surface; list order encodes fallback priority, consistent with the retry-time fallback path in `proxy.py`.

## Files to modify

### src/optiproxai/router.py
- `_select_primary_candidate` (~line 826): add `promoted_from_fallback: bool = False` parameter. After the existing `len(candidates) == 1` short-circuit (line 852), add: if `promoted_from_fallback`, `log.debug(...)` and `return candidates[0]`. Keep the session_sticky (line 856) and round-robin (line 871) branches unchanged for the `False` path.
- `route()` call site (~line 470): pass `promoted_from_fallback=promoted_from_fallback` into `_select_primary_candidate(...)`.
- No change to the `fallback_entries` exclusion loop (~line 495), which already removes the promoted model from the fallback list using the same flag.
- No change to `resolve_model()` (line 589) — it has no promotion path and must keep its current primary_selection behavior.

### tests/test_router_logging.py (and/or test_input_limit_routing.py)
- Extend coverage per the manifest. Existing `test_route_promotes_fallback_when_all_primary_candidates_are_cooling` uses a single fallback, which short-circuits at `len==1` and does not exercise the bug; new tests must use multiple fallbacks.

## Constraints / Risks
- The new parameter defaults to `False`, so the only other caller (`resolve_model`, line 589, no filter/session_key) is unaffected.
- The primary ignore-cooldown branch (~line 445: `elif primary_candidates`) is intentionally NOT a fallback promotion and keeps normal `primary_selection` (doc-16).
- Single-fallback promotion already short-circuits via `len(candidates)==1`; the fix additionally guarantees first-listed selection for multi-fallback promotions.
- Determinism: config-order promotion is fully deterministic (always `candidates[0]`), so it removes the per-session-key variance that caused the observed bug.

## Out of scope
- Retry-time fallback selection in `proxy.py` (already config-order; unchanged).
- Any new `fallback_selection` config key (rejected in doc-16).
- Capability-based promotion semantics.

## Single-task vs decompose

Recommend SINGLE task. The change is one method parameter + one call-site arg + tests, tightly coupled, with no independently shippable intermediate state. Per TASK-12 precedent, the Task Manifest below serves as the in-task todo checklist rather than spawning Backlog subtasks (skip Phase 2).

## Task Manifest

| # | Title | Files | Depends On | Labels | Acceptance Criterion |
|---|---|---|---|---|---|
| 1 | Add promoted_from_fallback bypass to _select_primary_candidate | src/optiproxai/router.py | - | logic | `_select_primary_candidate` returns `candidates[0]` when `promoted_from_fallback=True` and otherwise preserves existing session_sticky/round-robin behavior; `len==1` still short-circuits first |
| 2 | Pass promoted_from_fallback at routing call site | src/optiproxai/router.py | 1 | logic | `route()` forwards its local `promoted_from_fallback` flag into the `_select_primary_candidate` call so both fallback-promotion branches select the first-listed fallback |
| 3 | Add tests for config-order fallback promotion | tests/test_router_logging.py, tests/test_input_limit_routing.py | 1, 2 | test | Tests assert the first-listed fallback is selected under session_sticky (session key hashing to a non-zero index) and under round-robin; primary selection (sticky + round-robin) and the retry path remain unchanged; single-fallback short-circuit unaffected |
| 4 | Run CI gates | (no file changes) | 1-3 | test | `ruff check src/`, `ruff format --check src/ tests/`, `pyright src/`, and `pytest tests/ -q` all pass |
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented on branch task/TASK-018 per doc-16.

- router.py `_select_primary_candidate`: new `promoted_from_fallback: bool = False` param; config-order bypass inserted after the len==1 short-circuit, before session_sticky/round-robin branches; debug log line for the promoted selection.
- router.py route() call site (~line 470): passes `promoted_from_fallback=promoted_from_fallback`.
- resolve_model() call site untouched (defaults keep primary_selection for primaries).
- tests/test_router_logging.py: new `TestPromotedFallbackConfigOrder` class with `_promotion_config()` helper (2 primaries + 3 fallbacks, fallback_backoff enabled) and 3 tests: session_sticky promotion picks first-listed fallback with a key hashing to index 1 (bug repro condition), round-robin promotion picks first-listed, and session_sticky primaries still hash-rotate when no promotion occurs. Uses `_session_hash` imported from router.
- The task description's "recommend both" suggestion for the primary ignore-cooldown branch was rejected in doc-16: that branch uses real primaries and keeps primary_selection.
- Existing single-fallback promotion test (short-circuits at len==1) unchanged and still passing.

2026-09-07: Implementation complete on task/TASK-018. All 4 manifest items done; CI gates green (ruff, format, pyright 0 errors, pytest 436 passed, uv build OK). Decision record doc-16 created and linked.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
## TASK-18 Complete: Promoted fallbacks respect config order

### What was built

Fallback lists promoted to primary at routing time now select strictly by config order, matching the retry-time fallback path. The tier's primary_selection policy (session_sticky/round-robin) applies only to actual primary candidates.

### Files changed

- `src/optiproxai/router.py` — `_select_primary_candidate()` gains `promoted_from_fallback: bool = False`; when set, returns `candidates[0]` after the len==1 short-circuit, bypassing session_sticky hash and round-robin, with a debug log line. `route()` call site (~line 470) forwards the existing local `promoted_from_fallback` flag set by both fallback-promotion branches. `resolve_model()` and the fallback_entries exclusion loop are unchanged.
- `tests/test_router_logging.py` — new `TestPromotedFallbackConfigOrder` class with `_promotion_config()` helper (2 primaries + 3 fallbacks, backoff enabled): session_sticky promotion picks the first-listed fallback even when the session key hashes to index 1 (the bug repro condition); round-robin promotion picks the first-listed fallback; session_sticky primaries still hash-rotate when no promotion occurs.

### Design decisions

- doc-16: config order is the policy for promoted fallbacks; no new config surface.
- The primary ignore-cooldown branch (router.py ~445) is NOT a fallback promotion and keeps primary_selection — the task description's "recommend both" suggestion was rejected there.

### CI gates

- ruff check src/: passed
- ruff format --check src/ tests/: 39 files formatted (1 auto-formatted)
- pyright src/: 0 errors, 0 warnings
- pytest tests/ -q: 436 passed (+4 new)
- uv build: succeeded

### Deviations from plan

None. (test_input_limit_routing.py was listed as optional in the plan; all promotion-path tests fit in test_router_logging.py.)
<!-- SECTION:FINAL_SUMMARY:END -->
