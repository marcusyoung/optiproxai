---
id: TASK-16
title: 'Fix Hy3 input-limit cap misfire: session-keyed last-context token cache'
status: To Do
assignee: []
created_date: '2026-08-27 19:50'
updated_date: '2026-08-27 19:50'
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
1. Add session-keyed last-context cache state (dict + Lock, keyed by session ID) next to FallbackBackoffState.
2. Thread X-Session-Id into _log_usage (via RoutingDecision.session_key or a param through _proxy_upstream) and write real provider prompt into the cache.
3. In route(), read cache as max(cached_last_prompt, _estimate_tokens(...)); live estimate on first turn / cache miss.
4. Add reasoning_content to _estimate_tokens or rely on prior-turn cache.
5. Restart server, send an oversized analysis prompt, confirm cap log + DeepSeek-V4-Pro fallback.
6. uv run pytest tests/test_input_limit_routing.py tests/test_api_keys_proxy.py -q ; commit.
<!-- SECTION:PLAN:END -->
