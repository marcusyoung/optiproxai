---
id: TASK-16
title: 'Fix Hy3 input-limit cap misfire: session-keyed last-context token cache'
status: In Progress
assignee: []
created_date: '2026-08-27 19:50'
updated_date: '2026-08-28 11:43'
labels:
  - fallback
  - routing
  - input-limit
  - hy3
  - token-estimate
dependencies: []
references:
  - /research/handoff-2026-08-27-optiproxai-fallback-fix.md
  - src/optiproxai/tokens.py
  - src/optiproxai/router.py
  - src/optiproxai/proxy.py
documentation:
  - >-
    docs/decisions/doc-8 -
    Session-keyed-last-context-token-cache-for-input-limit-gating.md
priority: high
type: bug
ordinal: 20000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Root cause (handoff 2026-08-27, doc 7f948ef7-10ad-4f83-8237-e7a3867a59ca): the analysis profile's Hy3 max_input_tokens:160000 cap was not triggering fallback to the 1M DeepSeek-V4-Pro model. _estimate_tokens() undercounted real prompts (missing tools schema, per-message fields, and critically reasoning_content), so 168-190K actual prompts estimated under 160K and still routed to Hy3, stalling for the full 300s read timeout before failing over.

First fix already COMMITTED (3472b91): count tools schema + per-message fields in _estimate_tokens(). STILL insufficient - does not count reasoning_content.

Remaining work (active queue):
1. Implement session-keyed last-context cache (mirror FallbackBackoffState in fallback_backoff.py): dict + Lock keyed by session ID -> last provider prompt.
2. Confirm/thread X-Session-Id into the usage-logging site in proxy.py (route() already receives session_key; RoutingDecision does NOT store it; _log_usage has no session id).
3. Feed cache from the USAGE log path; read in route() as max(last_prompt, live_estimate); fall back to live estimate on first turn.
4. Add reasoning_content to the estimate OR rely on prior-turn cache (which already includes it).
5. Restart server; verify cap fires (log: 'Skipping input-limit-ineligible candidate model=tencent/hy3 ... max_input_tokens=160000').
6. Commit working-tree changes.

Key decisions (held): analysis stays Hy3; REASONING stays K3; Hy3 capped at 160000 (provider-independent degradation on Novita + Doubleword); MEDIUM+COMPLEX unified on glm-5.3-flash. Robust fix errs toward the 1M fallback: max(prior_turn_provider_prompt, live_tiktoken_estimate).

NOTE (verified 2026-08-27): the handoff's claim that tests/test_api_keys_proxy.py has 17 pre-existing failures is FALSE - that file currently passes all 25 tests (0 failed). Do not rely on that note.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Session-keyed last-context cache implemented (dict + Lock keyed by session ID) mirroring FallbackBackoffState.
- [ ] #2 route() estimates prompt size as max(last-turn provider prompt from cache, live _estimate_tokens()); first turn uses live estimate only.
- [ ] #3 X-Session-Id threaded to the usage-logging site so the cache is fed from the real provider prompt.
- [ ] #4 Cap fires for oversized analysis prompts: log shows 'Skipping input-limit-ineligible candidate model=tencent/hy3 ... max_input_tokens=160000' and falls back to DeepSeek-V4-Pro (1M).
- [ ] #5 tests/test_input_limit_routing.py and tests/test_api_keys_proxy.py pass (api_keys_proxy currently 25 passed / 0 failed).
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
# Implementation Plan — TASK-016: Session-keyed last-context token cache

## Problem
Hy3 `max_input_tokens: 160000` cap misfires: `_estimate_tokens()` undercounts real prompts (notably `reasoning_content`, plus provider-side prompt-template overhead), so 168–190K-token prompts estimate under 160K, route to Hy3, and stall for the 300s read timeout before fallback. Robust fix (held decision): use `max(prior-turn provider prompt, live tiktoken estimate)`, erring toward the 1M DeepSeek-V4-Pro fallback.

## Approach
Provider-reported `usage.prompt_tokens` is ground truth for what the provider actually received. Cache it per session after each successful upstream call, and in `route()` estimate with `max(cached_last_prompt_tokens, live_estimate)`. First turn / no session header → live estimate only (current behaviour).

### 1. New module `src/optiproxai/last_context_cache.py`
- `LastContextCache` class mirroring `FallbackBackoffState`: `dict[str, int]` (session key → last provider prompt tokens) + `threading.Lock`.
- Methods: `record(session_key, prompt_tokens)`, `get(session_key) -> int | None`, `clear()` (test helper).
- Module-level singleton `last_context_cache` (process-local, like `_encoder_cache` in tokens.py / `_http` in proxy.py). Router accepts an optional `last_context_cache` ctor param defaulting to the singleton — same DI pattern as `fallback_backoff_state` — so tests can inject isolated instances.

### 2. `src/optiproxai/tokens.py` — add `reasoning_content` to `_estimate_tokens`
- In the per-message loop add `total += _count_str_tokens(enc, m.get("reasoning_content"))` (belt-and-braces for first-turn accuracy; the cache covers prior-turn ground truth).

### 3. `src/optiproxai/router.py`
- Add `session_key: str | None = None` field to `RoutingDecision`; populate it when constructing the decision in `route()` (session_key is already a `route()` parameter).
- In `route()`: after `prompt_tokens = _estimate_tokens(messages, tools=tools)`, read `self.last_context_cache.get(session_key)` and use `max(live_estimate, cached)` when both present. Cache miss/None session → live estimate unchanged.

### 4. `src/optiproxai/proxy.py` — feed the cache from the usage log path
- In `_log_usage(...)`: after computing `prompt` from `usage`, if `decision is not None and decision.session_key`, call `last_context_cache.record(decision.session_key, prompt)`. This covers both streaming (last_usage in `_stream()` finally) and non-streaming call sites, since both already pass `decision=decision`.
- No signature changes to `_proxy_upstream` or `_log_usage` needed — the session key rides on `RoutingDecision`.

### 5. Tests
- `tests/test_last_context_cache.py` (new): record/get/clear, overwrite-with-latest, lock-free read semantics, miss returns None.
- `tests/test_input_limit_routing.py` (extend): cache-fed prompt exceeds cap → cap fires and falls back (`max(cached, live)`); first-turn (cache miss) uses live estimate; live estimate larger than cache wins; cache ignored when `session_key` is None.
- Keep existing `monkeypatch.setattr("optiproxai.router._estimate_tokens", ...)` pattern working (cache read must compose with it).

### 6. Verification & commit
- `uv run pytest tests/test_last_context_cache.py tests/test_input_limit_routing.py tests/test_api_keys_proxy.py -q`
- Full CI bar: ruff check, ruff format --check, pyright, pytest, uv build.
- Manual: restart server, send oversized analysis prompt with X-Session-Id; confirm log `Skipping input-limit-ineligible candidate model=tencent/hy3 ... max_input_tokens=160000` and DeepSeek-V4-Pro (1M) fallback on turn 2+.
- Commit working-tree changes on branch `task/TASK-016`.

## Constraints / risks
- Cache grows one int per session for process lifetime (small; acceptable process-local state, same lifetime model as FallbackBackoffState).
- Cached value includes provider-side template overhead → errs large, which is the intended bias (1M fallback over 300s stall).
- Clients not sending `X-Session-Id` get no cache benefit (unchanged behaviour).
- Stale cache after conversation reset within same session id: cache only ever inflates the estimate; worst case is early escalation to the 1M model, never a misfire stall.
- Held decisions preserved: analysis stays Hy3 @ 160000 cap; REASONING stays K3; MEDIUM+COMPLEX unified on glm-5.3-flash.

## Task Manifest

| # | Title | Files | Depends On | Labels | Acceptance Criterion |
|---|---|---|---|---|---|
| 1 | Create LastContextCache module | src/optiproxai/last_context_cache.py, tests/test_last_context_cache.py | — | logic, test | LastContextCache records/returns latest provider prompt tokens per session key and all its tests pass |
| 2 | Count reasoning_content in _estimate_tokens | src/optiproxai/tokens.py | — | logic | _estimate_tokens includes reasoning_content fields in its token total |
| 3 | Read cache in route() and store session_key on RoutingDecision | src/optiproxai/router.py | 1 | logic | route() uses max(cached_last_prompt, live_estimate) when session_key is set, live estimate otherwise |
| 4 | Feed cache from _log_usage | src/optiproxai/proxy.py | 1, 3 | logic | USAGE logging path records provider prompt_tokens into the cache keyed by decision.session_key |
| 5 | Add input-limit cache integration tests | tests/test_input_limit_routing.py | 1, 2, 3 | test | Cached oversized prompt skips Hy3-class candidates and selects the larger fallback model; all input-limit tests pass |
| 6 | Verify, run CI checks, commit | — | 1, 2, 3, 4, 5 | infra | pytest subsets pass, ruff/pyright clean, changes committed |

Fits a single task — no subtasks needed.
<!-- SECTION:PLAN:END -->
