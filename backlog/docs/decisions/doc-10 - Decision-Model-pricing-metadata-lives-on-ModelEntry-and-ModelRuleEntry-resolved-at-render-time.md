---
id: doc-10
title: >-
  Decision: Model pricing metadata lives on ModelEntry and ModelRuleEntry,
  resolved at render time
type: other
created_date: '2026-08-28 18:39'
---
# Decision: Model pricing metadata lives on ModelEntry and ModelRuleEntry, resolved at render time

**Status:** Accepted  
**Date:** 2026-08-28  
**Task:** TASK-12 (Dashboard cache-hit metrics section)

## Context

The TASK-12 cost-impact estimate needs per-model pricing (input price, cache-read price, cache-write price) to convert cache tokens into estimated dollar savings. No pricing metadata exists anywhere in the config today.

Options considered:

1. New top-level `pricing:` config section mapping model names to prices.
2. Optional `pricing` block on `ModelEntry` and `ModelRuleEntry` (next to `async_mode` / `max_input_tokens`).
3. Skip the cost estimate entirely.

## Decision

Option 2 — optional `pricing: ModelPricingConfig | None` field on `ModelEntry` and `ModelRuleEntry`, resolved by the dashboard at render time via `load_config()`.

## Rationale

- Pricing is a property of a model at a provider, which is exactly what ModelEntry/ModelRuleEntry already describe; `async_mode` and `max_input_tokens` set the precedent for per-entry metadata.
- ModelEntry has `extra="forbid"`, so the field must be declared explicitly — a one-line addition per model.
- A top-level pricing map would duplicate model identity in a second location and drift from tier definitions.
- The dashboard already calls `load_config()` at render/query time (`_infer_provider` in dashboard.py), so render-time resolution follows existing precedent and avoids propagating pricing through RoutingDecision/FallbackEntry (doc-4 machinery) for what is a display-only concern.

## Semantics

- `ModelPricingConfig`: `input_per_mtok`, `cache_read_per_mtok`, `cache_write_per_mtok` — all `float | None`, USD per 1M tokens.
- Resolution at render time: exact model-name match across profile tiers (ModelEntry) first, then longest-prefix match over model_rules (ModelRuleEntry). No match → no pricing → savings rendered as em-dash (graceful absence).
- Savings formula (when prices available):  
  `savings = (input_per_mtok - cache_read_per_mtok) * cache_read_tokens / 1e6 - (cache_write_per_mtok - input_per_mtok) * cache_creation_tokens / 1e6`, treating missing components as 0 and never going below the read-savings component alone if write price is absent.

## Consequences

- config.example.yaml and README.md gain a pricing example.
- Dashboard cost column is informational only; routing behavior is unaffected.
- Future per-provider pricing (provider-level default) can be added later without breaking the per-model shape.
