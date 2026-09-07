---
id: doc-14
title: 'Decision: last-context cache keeps inflated estimate on stripped turns'
type: other
created_date: '2026-09-04 12:43'
updated_date: '2026-09-07 17:03'
---
# Decision: last-context cache keeps inflated prompt estimate on stripped turns (err-toward-escalation preserved)

**Date**: 2026-09-04
**Status**: Accepted
**Related**: TASK-17, doc-8 (session-keyed last-context token cache), doc-13 (stripping mechanism)

## Context

The last-context cache (doc-8) records provider-reported `prompt_tokens` per session and the router takes `max(cached, live_estimate)` when gating `max_input_tokens`. Image tokens are included in provider prompt_tokens, so after stripping a 100K-token image from history, the live estimate shrinks but the cached value stays inflated, forcing input-limit escalation for one more turn and partially deferring the routing savings.

## Decision

**Keep the inflated cached value on stripped turns. Do not reset or shrink last-context cache entries when stripping occurs.**

## Rationale

1. **Consistency with TASK-016/doc-8 err-toward-escalation bias**: the cache exists to prevent underestimating prompt size (reasoning_content and provider template overhead are invisible to live estimation). Resetting on stripped turns reintroduces exactly that underestimation risk, for a different reason.
2. **Cost asymmetry**: an unnecessary escalation to a larger-context (typically pricier) model is a bounded, one-turn cost; an underestimation that lets an oversized prompt hit a small-context model produces a hard upstream failure plus a retry — worse for latency and often for cost too.
3. **Self-healing within one turn**: after the first stripped turn reaches a provider, the provider-reported prompt_tokens for THAT turn (now image-free) overwrites the cache entry. So the inflated value only gates one turn — the very turn where stripping happened. The penalty is bounded and small by construction.
4. **Simplicity**: no coupling between the stripping sanitizer and the cache; no new invalidation semantics to test or reason about.

## Consequence

Post-image sessions pay one escalated turn after images leave the strip window, then route normally. Accepted. If measurement shows this materially delays hand-back in long sessions, a follow-up could add a conservative discount (e.g. subtract an estimated image-token budget from the cached value, never below the live estimate) — explicitly deferred.

## Amendment — TASK-23 (2026-09-07)

The inflation observed on TASK-17 image sessions was not caused by the cache
retaining a stale value (doc-8 `max(cached, live)` semantics are unchanged and
correct). The root cause was token estimation itself: `_estimate_tokens`
tokenized each `image_url` data URI as prose text, inflating a ~22–25K-token
prompt to ~339K tokens. That made every candidate fail the input-limit filter
and forced fallback promotion every turn — independent of the last-context cache.

TASK-23 fixes the root cause: image parts now contribute a fixed
`_IMAGE_PART_TOKEN_ESTIMATE = 2048` constant instead of being tokenized. With a
sane ~24K live estimate, doc-8's `max(cached, live)` behaves exactly as
intended (errs high, never underestimates).

Consequences for this decision:
- The self-healing assumption in Rationale #3 holds under the fixed estimate: a
  stripped turn's provider-reported (image-free) `prompt_tokens` overwrites the
  cache entry, so any inflated value gates at most one turn.
- The deferred-discount follow-up in Consequence is now **moot**: the live
  estimate is no longer pathologically inflated, so there is no systematic
  over-escalation to discount away.
