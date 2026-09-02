---
id: TASK-20
title: >-
  Investigate synthetic 429 (credit exhaustion) misclassified as rate-limit
  retry
status: To Do
assignee: []
created_date: '2026-08-28 22:57'
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
