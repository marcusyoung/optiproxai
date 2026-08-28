---
id: TASK-19
title: Provider billing flag — subscription-aware savings suppression in dashboard
status: To Do
assignee: []
created_date: '2026-08-28 19:05'
labels: []
dependencies: []
priority: medium
type: feature
ordinal: 16500
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add a `billing` field to ProviderConfig (subscription | pay_as_you_go, default pay_as_you_go) so the dashboard can record and display which providers are on flat-rate subscriptions, and suppress misleading per-token savings estimates for included models.

## Semantics (agreed 2026-08-28)

- Subscriptions are flat/unlimited — tokens cost nothing extra at the margin. No cap tracking, no plan metadata.
- Billing is provider-level (like async_mode / cache_control), configured on ProviderConfig.
- Precedence rule: explicit per-model `pricing` (ModelEntry/ModelRuleEntry, doc-10) beats the provider's subscription flag. A model with explicit pricing on a subscription provider still renders dollar estimates (metered); all other models on that provider render "included".
- Resolution order for savings display: ModelEntry/ModelRuleEntry pricing -> metered, use pricing -> provider billing=subscription -> "included" -> no pricing + pay_as_you_go -> dash.
- Display: "included" in the Est. savings column; subscription indicator in the Cache by Model table AND the Model/Provider Usage table.
- Render-time resolution only (same pattern as _resolve_model_pricing via load_config) — no new log columns. Flag changes apply retroactively to historical rows (accepted, display-only data).

## Background

Follow-on discussion from TASK-12 (dashboard cache-hit metrics). The Est. savings dollar figure is misleading for subscription providers since tokens do not cost per-unit; suppressing to "included" is the more honest cost story. TASK-12 shipped _resolve_model_pricing/_estimate_cache_savings, which this task extends.

## References

- src/optiproxai/config.py — ProviderConfig (billing field), ModelPricingConfig (doc-10)
- src/optiproxai/dashboard.py — _resolve_model_pricing, _estimate_cache_savings, _render_cache_model_table, _render_model_usage_table
- Decision records: doc-10 (pricing metadata), doc-9 (execution_logs schema)
- Related: TASK-12 (cache-hit metrics), TASK-13 (cache response headers)
<!-- SECTION:DESCRIPTION:END -->
