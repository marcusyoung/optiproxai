---
id: doc-4
title: >-
  Decision: ModelEntry async_mode propagates via RoutingDecision and
  FallbackEntry
type: other
created_date: '2026-08-18 18:57'
---
# Decision: ModelEntry async_mode propagates via RoutingDecision and FallbackEntry

**Date:** 2026-08-18
**Status:** Decided
**Task:** TASK-6
**Author:** dev (optiproxai/code)

## Context
TASK-6 adds `async_mode` to three config levels: `ModelEntry`, `ModelRuleEntry`, and `ProviderConfig`. `ModelRuleEntry` and `ProviderConfig` values can be resolved at proxy time from `(model, provider_name, runtime)` using the existing prefix/provider scoring pattern (`_get_model_extra_body`). `ModelEntry.async_mode` cannot: `ModelEntry` lives in `TierModelConfig.primary`/`fallback` lists and is normalized into `ResolvedModelCandidate` (which currently carries only `model`, `provider`, `max_input_tokens`). By the time `_prepare_body_for_candidate` runs in `proxy.py`, the proxy knows only the model name and provider — not which profile/tier `ModelEntry` produced the candidate. The same model name could appear in multiple profiles/tiers with different `async_mode` settings, so re-deriving the entry by scanning tier configs at proxy time would be ambiguous.

## Options Considered

| Option | Pros | Cons |
--------|------|------|
| Propagate async_mode through ResolvedModelCandidate -> RoutingDecision/FallbackEntry | Mirrors existing `max_input_tokens` propagation exactly; unambiguous (value travels with the chosen candidate); proxy-side resolver stays simple | Adds a field to 4 model classes; slightly more plumbing in router.py route() and resolve_model() |
| Re-lookup ModelEntry at proxy time by scanning profiles/tiers for matching model+provider | No router/decision changes | Ambiguous when the same model appears in multiple profiles/tiers with different async_mode; duplicates router selection logic in proxy; diverges from max_input_tokens precedent |
| Drop ModelEntry level; only ModelRuleEntry + ProviderConfig | Simplest; model-level override achievable via a specific-prefix rule | Breaks TASK-6 acceptance criteria #3 and #5 (ModelEntry level required); per-model override becomes rule-prefix hack |

## Decision
Propagate `async_mode: AsyncModeConfig | None` from `ModelEntry` through `ResolvedModelCandidate` into `RoutingDecision.async_mode` and `FallbackEntry.async_mode`, following the `max_input_tokens` pattern (config.py `_resolve_candidate_entry`, router.py route() and resolve_model()). The proxy-side `_resolve_async_mode` receives the entry-level value from the caller (`_try_with_fallbacks` reads `decision.async_mode` for primary and `fb.async_mode` for fallbacks) and applies resolution order: entry-level -> rule-level -> provider-level -> None. Resolution selects the entry at the highest-precedence level that has `async_mode` set (presence, not `enabled` truthiness); the resolved entry's `enabled` flag then gates application, which makes an explicit model-level `enabled: false` win over a provider-level `enabled: true`.

## Rationale
Consistency with the existing `max_input_tokens` metadata-propagation precedent keeps the codebase's one established pattern for per-candidate config. It also resolves the profile/tier ambiguity correctly: the router already selected a specific candidate entry, so the decision object is the only place that value can travel without loss.

## Consequences
Four model classes gain an optional field (no breaking change — all default None). router.py route() and resolve_model() each gain propagation lines for primary and fallbacks. `_resolve_async_mode` takes an optional `entry_async_mode` parameter supplied by the caller that holds the RoutingDecision/FallbackEntry.
