---
id: TASK-18
title: 'Promoted fallbacks must respect config order, not primary_selection policy'
status: To Do
assignee: []
created_date: '2026-08-28 16:29'
labels:
  - bug
  - routing
  - fallback
  - priority-order
dependencies: []
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
