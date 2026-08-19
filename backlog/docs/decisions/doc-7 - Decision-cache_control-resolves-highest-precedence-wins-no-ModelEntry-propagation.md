---
id: doc-7
title: >-
  Decision: cache_control resolves highest-precedence-wins, no ModelEntry
  propagation
type: other
created_date: '2026-08-19 15:16'
updated_date: '2026-08-19 15:25'
---
# Decision: cache_control resolves highest-precedence-wins, no ModelEntry propagation

**Date:** 2026-08-19
**Status:** Decided
**Task:** TASK-14
**Author:** build (optiproxai/code)

## Context
TASK-14 injects `cache_control` markers into the stable prefix for opt-in caching providers (Doubleword). The task design specifies resolution order: ModelRuleEntry.cache_control → ProviderConfig.cache_control → none. Two design choices needed clarification: (1) merge semantics — field-by-field merge (like async_mode, doc-6) vs highest-precedence-wins; (2) whether ModelEntry needs the field with router.py propagation (like doc-4).

## Options Considered

| Option | Pros | Cons |
--------|------|------|
| Presence-based highest-precedence-wins (chosen) | Simple; matches task's stated order; no partial-config validator needed; config is provider-scoped so one block per provider is the expected usage | Cannot split mechanism across levels (not needed — CacheControlConfig is a pure flag block, not mechanism+intent split) |
| Field-by-field merge (async_mode style) | Uniform with TASK-6/10 machinery | Over-engineered: enabled/ttl/target has no mechanism/intent split; merge would produce surprising partial overrides |
| Add ModelEntry.cache_control + propagate via RoutingDecision/FallbackEntry (doc-4 style) | Per-model entry control in profiles | ModelEntry has extra="forbid" (breaking change to add field); task explicitly scopes to rule+provider; per-model override already achievable via a specific-prefix rule in model_rules; router.py churn not justified |

## Decision
1. `CacheControlConfig` is a pure flag block: `enabled: bool = False`, `ttl: Literal["5m", "1h"] = "5m"`, `target: Literal["system", "tools"] = "system"`, `max_breakpoints: int = 4`.
2. Resolution is presence-based highest-precedence-wins: best-matching ModelRuleEntry.cache_control (same prefix/provider scoring as `_get_model_content_part_policy`) → ProviderConfig.cache_control → None. No field-by-field merge.
3. No ModelEntry level, no router.py/RoutingDecision/FallbackEntry changes. Per-model override via a specific-prefix model rule.
4. Application gates on the resolved config's `enabled: true`.
5. Breakpoint ceiling is tunable via `max_breakpoints` (default 4, Doubleword's documented ceiling).

## Rationale
The async_mode machinery exists because async_mode splits provider mechanism from model opt-in — two concerns that must merge across levels. CacheControlConfig has no such split; it is a single coherent policy per provider (with optional per-model refinement via rules). Presence-based resolution keeps the semantics obvious and the validator surface minimal. Skipping ModelEntry avoids a breaking change to `extra="forbid"` and the whole propagation chain for a feature the task scopes to rule+provider.

## Consequences
- `_resolve_cache_control(model, provider_name, runtime)` returns the winning config or None; no caller-side merge.
- If a future task wants ModelEntry-level cache_control, it must follow doc-4 propagation (ResolvedModelCandidate → RoutingDecision → FallbackEntry) and remove extra="forbid" on ModelEntry.
- Rule-level `enabled: false` explicitly opts out even when provider-level is enabled: true — the rule wins by presence.
- `max_breakpoints` added per plan review feedback (line 44 "so should this be configurable?").
