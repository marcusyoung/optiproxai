---
id: doc-14
title: 'Decision: last-context cache keeps inflated estimate on stripped turns'
type: other
created_date: '2026-09-04 12:43'
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
