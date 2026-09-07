---
id: TASK-20
title: >-
  Investigate synthetic 429 (credit exhaustion) misclassified as rate-limit
  retry
status: To Do
assignee: []
created_date: '2026-08-28 22:57'
updated_date: '2026-09-07 18:54'
labels:
  - bug
  - proxy
  - synthetic
  - 429-handling
  - investigation
dependencies: []
references:
  - src/optiproxai/proxy.py
  - src/optiproxai/fallback_backoff.py
  - 'src/optiproxai/training_data.py:246'
  - TASK-20.01
  - TASK-20.02
priority: high
type: bug
ordinal: 23000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
When the `synthetic` provider runs out of credits it returns HTTP 429 with body `{"error":"You've exceeded your subscription rate limits. Upgrade, or try again later... (https://synthetic.new/billing)"}`. This is a billing/credit-exhaustion error, NOT a true rate limit, yet optiproxai treats every 429 identically: it logs `Fallback cooldown applied ... delay_seconds=300.000` and retries on the same provider. The retry streak climbs uncontrollably (observed streak=17, 18, 19 and counting) with a fresh 300s cooldown each time, so the loop never terminates while credits are exhausted. It also never fails over to a fallback provider because the only routed model is `syn:large:vision` (vision required) and all input-limit-eligible fallback candidates are cooling down (`All input-limit-eligible fallback candidates cooling down; ignoring cooldown`). The proxy does not crash, but it spin-loops on a terminal error.

Hypothesis to validate: the 429 handling path keys only on the HTTP status code and does not inspect the error body, so a subscription/billing 429 is indistinguishable from a genuine rate-limit 429. Need to find where the classification/retry decision is made (proxy.py emits `Fallback cooldown applied`; fallback_backoff.py holds the cooldown state) and decide whether credit-exhaustion 429s should be terminal (hard fail) or force a fallback instead of a fixed cooldown-retry.

Note: the reproduction session required vision, which may also contribute to insufficient fallbacks — confirm whether the misclassification or the missing vision-capable fallback (or both) is the dominant cause.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Locate the exact code path where optiproxai classifies upstream 429 responses and decides retry vs fail-over (proxy.py `Fallback cooldown applied` + fallback_backoff.py cooldown state).
- [ ] #2 Determine whether the synthetic `You've exceeded your subscription rate limits` body should be treated as a non-retryable credit/billing-exhaustion error distinct from a genuine rate-limit 429, citing the current status-code-only logic.
- [ ] #3 Decide and document the correct behavior: e.g. classify subscription/billing 429 as terminal (hard fail to client) or force a fallback provider, and whether vision-required requests need a vision-capable fallback route.
- [ ] #4 Record the recommended fix as an implementation plan (do not implement during this investigation unless explicitly continued).
- [ ] #5 Add or extend tests covering a billing-exhaustion 429 (e.g. returns terminal error or routes to fallback rather than applying a 300s cooldown and looping).
- [ ] #6 Document the behavior change in code comments / README where applicable.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
## Resolution plan (staged, via child tasks)

**Stage 1 -> TASK-20.01: Upstream error event logging** (landing first)
- Add failure-variant execution-log event + server-log line on every non-200 upstream response: status, provider, model, request_id, bounded raw-body excerpt, retry-after header hint if present.
- No behavior change; independently shippable. Provides the evidence base to finalize 20.02 classifier patterns.

**Stage 2 -> TASK-20.02: Body-aware 429 classification + spin-loop guard** (informed by 20.01 captures)
- omp-style reason classification: billing/quota wording ("exceeded your subscription rate limits", "quota exhausted") -> terminal (hard fail) or forced failover; genuine throttle wording ("too many requests", "per minute") -> existing cooldown-retry path.
- Spin-loop guard: cap consecutive cooldown-ignoring retries per request, and terminal-classified errors bypass `_try_with_fallbacks` cooldown-retry entirely.
- Tests pinned to captured bodies where available; synthetic string-error shape normalization in `_parse_successful_failure` as needed.
- Vision-capable fallback route coverage is a config concern, verified as part of 20.02 acceptance.

Decision (user-confirmed): synthetic's misleading body is the primary cause; sequencing 20.01 then 20.02. Child tasks carry their own implementation plans.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Investigation findings (2026-09-07)

- Code path confirmed: `_is_retryable_error` (proxy.py:909) keys on status code only; `_record_retryable_failure` (proxy.py:969) applies cooldown with max-delay special-case only for 401/402/403. `_parse_successful_failure` inspects bodies but only for HTTP-200-wrapped errors.

- Router compounds the loop: `router.py:445-459` deliberately ignores cooldown when all candidates (or all input-limit-eligible fallbacks) are cooling down, re-selecting the same cooled provider each request; streak climbs 5s->300s capped. Server log confirms streak=1..10 for syn:large:vision/synthetic on 2026-09-05 20:12-20:49, repeated 09-06 and 09-07.

- Failover DID work in observed windows (`FALLBACK [1/2] moonshotai/Kimi-K3 provider=sference ... 200 OK`, and doubleword on 09-05 20:49); the spin is across requests at router level, not within one request. On 2026-09-06 the client itself received raw 429s (lines 23696-23728 of server log).

- Evidence gap: routing logs record classification only; execution logs record successful usage only (no error events); server log has no upstream error text. The one known body (`{"error":"You've exceeded your subscription rate limits..."}`) was observed client-side in opencode, relayed through the proxy's `_openai_error(429, "Upstream error: {raw[:500]}")` wrapper - authentic but truncated at 500 chars (any trailing fields such as message id are cut off).

- Precedent: oh-my-pi (`parseRateLimitReason`) classifies this exact synthetic wording as QUOTA_EXHAUSTED (billing) vs 'too many requests' as retryable throttle. Pattern can be drafted now, pinned later against captured bodies.
<!-- SECTION:NOTES:END -->
