---
id: TASK-5
title: >-
  Dashboard routing behavior section — override, async, escalation, fallback
  metrics
status: To Do
assignee: []
created_date: '2026-08-18 13:08'
updated_date: '2026-08-18 13:13'
labels: []
dependencies:
  - TASK-2
  - TASK-6
priority: medium
type: feature
ordinal: 9000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add a new "Routing Behavior" section to the OptiProxAI dashboard that surfaces four routing-decision metrics currently invisible to users.

## Metrics

1. **Tier overrides** — count and % of requests where the client sent `/optiproxai:<tier>`, with breakdown by target tier. Data already exists in `routing_logs.signals` JSON blob as `["tier_override"]`.

2. **Async requests** — count and % of requests sent in async mode (e.g. `service_tier: flex`), with breakdown by model. Requires `async_mode` config model (separate task) and new `execution_logs` columns to persist the effective service tier / async flag.

3. **Tier escalations** — count and % of requests where the router escalated from the scored tier to a higher tier (input-limit escalation), with from→to table. Requires `routing_logs.scored_tier` column to preserve the original scorer decision.

4. **Fallback usage** — count and % of requests where the primary model failed and a fallback model was used, with primary→actual pairs table. Requires `execution_logs.fallback_used` + `primary_model` columns.

## Section layout
- Stat cards row: 4 cards (override, async, escalation, fallback), each with count + % of total
- Override breakdown by target tier (bar chart)
- Async breakdown by model (bar chart)
- Escalation from→to table
- Fallback pairs table (primary → actual, count)

## Schema changes
- `routing_logs`: add `tier_override BOOLEAN DEFAULT 0`, `tier_override_target TEXT`, `scored_tier TEXT` (via `_ensure_column` migration)
- `execution_logs`: add `service_tier TEXT`, `async_mode BOOLEAN DEFAULT 0`, `fallback_used BOOLEAN DEFAULT 0`, `primary_model TEXT` (via `_ensure_column` migration)

## Data plumbing
- `router.py`: pass `scored_tier` and `tier_override`/`tier_override_target` to `RoutingLogger.log_decision`
- `proxy.py`: pass effective `service_tier`/`async_mode` from `_prepare_body_for_candidate` to `log_execution_event`; pass `fallback_used`/`primary_model` from `_try_with_fallbacks` to `log_execution_event`
- `dashboard.py`: new query functions for each metric; new render functions for section; update `get_dashboard_stats` and `render_dashboard_html`

## Dependencies
- Async metric requires the `async_mode` config model task (provider-level default, model-level override, three delivery modes: body/header/model_suffix). Other three metrics (override, escalation, fallback) are independent and can proceed regardless.

## References
- Existing dashboard: `src/optiproxai/dashboard.py` — `_init_dashboard_db` (schema), `get_dashboard_stats` (queries), `render_dashboard_html` (rendering)
- Override data: `src/optiproxai/router.py` line 267 sets `signals=["tier_override"]`; `src/optiproxai/proxy.py` line 1459 calls `parse_tier_override`
- Escalation: `src/optiproxai/router.py` `_escalation_path` (~line 348) — scored tier is lost, only resolved tier stored
- Fallback: `src/optiproxai/proxy.py` `_try_with_fallbacks` (line 886) — only winning model recorded
- Extra body / async: `src/optiproxai/proxy.py` `_get_model_extra_body` (line 1052), `_prepare_body_for_candidate` (line 1290)
- Related backlog tasks: TASK-2 (per-turn async modifier), TASK-3 (input-limit escalation spike)
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Routing Behavior section renders in the dashboard with four stat cards (override, async, escalation, fallback) showing count and % of total requests
- [ ] #2 Tier override card shows count and percentage of requests where signals contains tier_override; bar chart breaks down overrides by target tier
- [ ] #3 Async card shows count and percentage of requests sent in async mode; bar chart breaks down async requests by model
- [ ] #4 Escalation card shows count and percentage of requests where scored_tier differs from resolved tier; table shows from→to pairs with counts
- [ ] #5 Fallback card shows count and percentage of requests where a fallback model was used; table shows primary→actual model pairs with counts
- [ ] #6 routing_logs has tier_override, tier_override_target, and scored_tier columns added via _ensure_column migration
- [ ] #7 execution_logs has service_tier, async_mode, fallback_used, and primary_model columns added via _ensure_column migration
- [ ] #8 router.py passes scored_tier and tier_override/tier_override_target to RoutingLogger.log_decision
- [ ] #9 proxy.py passes effective service_tier/async_mode from _prepare_body_for_candidate to log_execution_event
- [ ] #10 proxy.py passes fallback_used/primary_model from _try_with_fallbacks to log_execution_event
- [ ] #11 Existing dashboard sections (window cards, charts, model usage, daily trends) remain unchanged
- [ ] #12 uv run ruff check src/ passes
- [ ] #13 uv run ruff format --check src/ tests/ passes
- [ ] #14 uv run pyright src/ passes
- [ ] #15 uv run pytest tests/ -q passes
- [ ] #16 Tests cover each metric with sample DB rows and verify rendered HTML output contains expected values
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Design context (2026-08-18 session)

### Origin
User asked about dashboard enhancements that show optiproxai-specific features not in the kani original. Four invisible metrics identified: tier overrides, async requests, tier escalations, and fallback usage.

### Async config model (separate task, prerequisite for async metric)
Three delivery modes: `body` (inject field into request JSON, e.g. OpenAI `service_tier: flex`), `header` (inject HTTP header), `model_suffix` (append `:suffix` to model name). Resolution order: ModelEntry.async_mode -> ModelRuleEntry.async_mode -> ProviderConfig.async_mode -> no-op (graceful). This mirrors the existing reasoning_style resolution pattern in `_get_model_reasoning_style`.

### Auto-async routing (deferred, future task)
Router-driven async where scorer signals (tier, agentic_score) determine async eligibility. Needs a policy knob in config (`routing.auto_async.strategy: cost_optimized | latency_optimized | never`). Precedence: per-turn :async override -> per-turn :sync override -> router auto-async -> config default. Deferred because it builds on async_mode config + TASK-2 + async tracking.

### Key files
- `src/optiproxai/dashboard.py` — schema init (line 44), insert functions (line 174+), `get_dashboard_stats` (line 800), `render_dashboard_html` (line 1169+)
- `src/optiproxai/router.py` — `parse_tier_override` (line 112), `route()` with `tier_override` param (line 211), `_escalation_path` (~line 348)
- `src/optiproxai/proxy.py` — `parse_tier_override` call (line 1459), `_try_with_fallbacks` (line 886), `_get_model_extra_body` (line 1052), `_prepare_body_for_candidate` (line 1290), `log_execution_event` (line 367 in dashboard.py)
- `src/optiproxai/logger.py` — `RoutingLogger.log_decision` (class method, records signals as JSON)

### Existing patterns to follow
- `_ensure_column(conn, table, column, definition)` for schema migrations
- `_window_summary` / `_model_usage_rows` / `_daily_trends` query patterns with profile filtering
- `_render_bar_chart` / `_render_simple_table` / `_render_window_cards` rendering patterns
- D3 trend chart with tooltip pattern for new bar charts if needed
<!-- SECTION:NOTES:END -->
