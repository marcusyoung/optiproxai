---
id: doc-16
title: Decision-promoted-fallback-selection-uses-config-order-not-primary_selection
type: other
created_date: '2026-09-07 18:01'
---
# Decision: Promoted fallback selection uses config order, not primary_selection policy

## Context

TASK-18 (bug): when all primary candidates are filtered out and a fallback is promoted to primary at routing time, `Router._select_primary_candidate()` applies the tier's `primary_selection` policy (session_sticky hash or round-robin) to the fallback list instead of respecting the config order in which fallbacks were declared. Observed 2026-08-28: COMPLEX tier, session_sticky — both Hy3 primaries input-limit filtered (>160K); two fallbacks eligible (DeepSeek-V4-Pro@doubleword listed first, syn:large:vision@synthetic second). The session-key hash landed on index 1, picking `syn:large:vision` (524K) over the deliberately-first-listed `DeepSeek-V4-Pro` (1M). The priority order in config was silently overridden.

Retry-time fallback (`proxy.py` retry loop over `decision.fallbacks`) already walks the list in config order. Routing-time promotion used a different selection semantics for the same list — the inconsistency to fix.

## Decision

`Router._select_primary_candidate()` gains a `promoted_from_fallback: bool = False` parameter. When `True`, the method returns `candidates[0]` (config order) and skips the session_sticky hash and round-robin logic. `Router.route()` passes the existing local `promoted_from_fallback` flag (already set in both fallback-promotion branches) into the call.

`primary_selection` (session_sticky / round_robin) continues to apply **only** to actual primary candidates. No new config surface is introduced — list order encodes fallback priority, matching the existing retry path.

## Scope of the flag

The flag is set `True` in exactly the two **fallback**-promotion branches of `route()`:
- input-limit promotion (~router.py:437): `elif fallback_candidates and cooled_fallback_candidates`
- all-fallbacks-cooling ignore-cooldown (~router.py:453): `elif fallback_candidates`

The **primary** ignore-cooldown branch (~router.py:445: `elif primary_candidates`) is NOT a fallback promotion. It keeps normal `primary_selection` behavior (selection_candidates == primary_candidates, the real primaries). The task description's "recommend both" suggestion to also force config order on this branch is **rejected**: doing so would override round-robin/sticky rotation on primaries and is outside the bug's scope ("promoted fallbacks").

## Locality trade-off (accepted)

Promotion can switch the model for the oversized prompt, forfeiting provider prefix cache. But the previously-sticky primary is already excluded (input-limit or cooldown), so a different model would be uncached regardless — priority order wins over cache locality.

## References

- TASK-18 — Promoted fallbacks must respect config order, not primary_selection policy
- router.py `_select_primary_candidate` (line ~826) and promotion branches (lines ~433–459)
- Existing test: tests/test_router_logging.py::test_route_promotes_fallback_when_all_primary_candidates_are_cooling (single-fallback, short-circuits today)
