---
id: TASK-20.2
title: Body-aware 429 classification (billing vs throttle) + spin-loop guard
status: To Do
assignee: []
created_date: '2026-09-07 18:54'
updated_date: '2026-09-07 18:54'
labels: []
dependencies:
  - TASK-20.1
parent_task_id: TASK-20
priority: high
type: feature
ordinal: 23020
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Follow-up to TASK-20.01 (upstream error event logging). optiproxai currently classifies every 429 identically by status code only (`_is_retryable_error`, proxy.py:909; `_record_retryable_failure`, proxy.py:969). Synthetic's billing/credit-exhaustion 429 returns body `{"error":"You've exceeded your subscription rate limits. Upgrade, or try again later..."}` (plain string error, non-OpenAI shape) and is misclassified as a throttle, producing endless cooldown-retry loops (observed streak=10+ capped at 300s) that never terminate while credits are exhausted.

Implement body-aware classification (modeled on oh-my-pi's `parseRateLimitReason` approach):
1. Billing/quota-worded 429s ("exceeded your subscription rate limits", "quota exhausted") classified as terminal/billing errors - either hard-fail to client or forced failover, NOT the standard cooldown-retry.
2. Genuine throttle wording ("too many requests", "per minute") keeps the existing cooldown-retry path.
3. Spin-loop guard: cap consecutive cooldown-ignoring retries per request (router.py:445-459 deliberately ignores cooldown when all candidates are cooling); terminal-classified errors bypass the fallback cooldown-retry entirely.
4. Normalize synthetic's string-error shape (`{"error": "<string>"}`) in body parsing as needed.
5. Decide/document whether vision-required requests need a vision-capable fallback route (config concern).

Classifier patterns drafted from the known body + oh-my-pi precedent; final patterns pinned against real captured bodies from TASK-20.01 logging where available.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Billing/quota-worded 429s (e.g. "exceeded your subscription rate limits") classified as terminal/billing errors: hard-fail to client or forced failover, not standard cooldown-retry.
- [ ] #2 Genuine throttle wording ("too many requests", "per minute") keeps the existing cooldown-retry path unchanged.
- [ ] #3 Terminal-classified errors bypass the fallback cooldown-retry path; consecutive cooldown-ignoring router retries are capped per request.
- [ ] #4 Synthetic string-error shape ({"error": "<string>"}) handled in body parsing.
- [ ] #5 Tests cover: billing 429 vs throttle 429 classification boundaries, terminal-error bypass, and streak cap - using captured bodies from TASK-20.01 logging where available.
- [ ] #6 Behavior change documented in code comments and README; vision-capable fallback route need decided and documented.
<!-- AC:END -->
