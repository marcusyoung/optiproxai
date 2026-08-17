---
id: TASK-1.2
title: >-
  Integrate tier override into proxy: chat_completions + route_debug endpoint +
  tests
status: Done
assignee: []
created_date: '2026-08-17 12:25'
updated_date: '2026-08-17 17:50'
labels:
  - logic
  - test
dependencies:
  - TASK-1.1
references:
  - src/optiproxai/proxy.py
  - tests/test_tier_override.py
parent_task_id: TASK-1
priority: high
type: feature
ordinal: 3000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Wire the tier override helper into the FastAPI proxy: the chat completions endpoint strips the token before compaction/forwarding and passes the override to route(); the debug endpoint reflects the override in its response payload.

## What to implement

### chat_completions() (proxy.py, ~line 1902 after messages validation, before router.route() call)
- Call `parse_tier_override(messages)` → `(tier_override, stripped_messages)`.
- If `tier_override is not None` or `stripped_messages is not messages`: set `body = dict(body)` and `body["messages"] = stripped_messages`. This must happen before `_resolve_compaction` runs so the token doesn't leak into compaction summaries.
- Pass `tier_override=tier_override` to `state.router.route()`.
- Log: `logger.info("TIER_OVERRIDE request_id=%s tier_override=%s", request_id, tier_override)`.

### route_debug() (proxy.py, ~line 2257)
- Call `parse_tier_override(messages)` on the request messages.
- Pass `tier_override` to `route()`.
- Include `tier_override` in the debug response payload.

## Files affected
- `src/optiproxai/proxy.py` — modify `chat_completions()` and `route_debug()`
- `tests/test_tier_override.py` — add `TestProxyTierOverride` class

## Risks and constraints
- **Compaction interaction**: the token must be stripped before `_resolve_compaction` runs, otherwise the token could leak into compaction summaries. Place the parse+strip call before the compaction call.
- The proxy must handle the case where `parse_tier_override` returns the original messages list unchanged (no match) — do not unnecessarily copy the body in that case.
- Mock `_proxy_upstream` or `_try_with_fallbacks` in tests to verify the stripped body is forwarded upstream.
- The `logger` in proxy.py is `optiproxai.proxy` — the TIER_OVERRIDE log line will appear in stderr, which the user's server redirects to `C:\Users\myoun\.local\bin\optiproxai-server.log`.

## Dependencies
- TASK-1.1 must be complete: `parse_tier_override` and `Router.route(tier_override=...)` must be importable from `optiproxai.router`.

## Output contract
After this task, the chat completions proxy and debug endpoint honor the `/optiproxai:<tier>` override. The token is stripped before upstream forwarding. The debug endpoint reports the override in its JSON payload.

## Decision records
- `decisions/invalid-tier-warn-vs-error` — invalid tier behavior in proxy context
- `decisions/tier-override-token-position` — token position rule
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 parse_tier_override is called before router.route() in chat_completions() and the stripped messages replace body["messages"] before compaction runs
- [x] #2 tier_override is passed to router.route() in chat_completions()
- [x] #3 A TIER_OVERRIDE INFO log line is emitted with request_id and tier_override value when an override is active
- [x] #4 Valid /optiproxai:<tier> in a chat completions request forces the overridden tier (visible in response headers)
- [x] #5 The /optiproxai:<tier> token is stripped from the upstream request body (verified via mocking)
- [x] #6 Invalid /optiproxai:foo in a chat completions request falls through to normal routing with the token stripped
- [x] #7 A /optiproxai:... token in history messages does not trigger an override or stripping in the proxy
- [x] #8 route_debug() calls parse_tier_override and passes tier_override to route()
- [x] #9 route_debug() response payload includes the tier_override field
- [x] #10 All tests in TestProxyTierOverride pass
- [x] #11 uv run pytest tests/test_tier_override.py::TestProxyTierOverride -q passes
- [x] #12 uv run ruff check src/optiproxai/proxy.py passes
- [x] #13 uv run pyright src/optiproxai/proxy.py passes
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Files modified: src/optiproxai/proxy.py (+19 lines: parse_tier_override import, tier_override block in chat_completions, tier_override in route_debug payload), tests/test_tier_override.py (+192 lines: TestProxyTierOverride)

Compaction safety: strip happens before _resolve_compaction runs, confirmed by test_token_stripped_from_upstream_body asserting the upstream body

TIER_OVERRIDE log emitted at INFO level only when an override is active (not on every request); invalid tier still triggers the router-side warning from TASK-1.1
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Tier override wired into the proxy: chat_completions() strips the /optiproxai:<tier> token before compaction and upstream forwarding, passes tier_override to Router.route(), and logs TIER_OVERRIDE per request. route_debug() parses the override and reports it in the payload. 11 new proxy tests in TestProxyTierOverride cover header pinning, token stripping (verified via _proxy_upstream mock), TIER_OVERRIDE log, invalid-tier fallback, history/assistant non-triggering, and debug endpoint fields. 36/36 tier override tests pass; ruff and pyright clean; full suite 381 passed with 1 pre-existing unrelated failure (test_agentic_training_script).
<!-- SECTION:FINAL_SUMMARY:END -->
