---
id: TASK-12
title: >-
  Dashboard cache-hit metrics section — cached tokens, cache read/write per
  provider and model
status: To Do
assignee: []
created_date: '2026-08-19 14:44'
labels: []
dependencies: []
priority: medium
type: feature
ordinal: 16000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add a cache-hit metrics section to the OptiProxAI dashboard that surfaces prompt-caching activity per provider/model, since opencode/openchamber do not render cache fields from generic OpenAI-compatible endpoints.

## Background (2026-08-19 testing)
Direct provider tests showed cache token reporting varies by provider: Doubleword returns `cache_read_input_tokens`/`cache_creation_input_tokens` + `prompt_tokens_details.cached_tokens` (opt-in via cache_control); Requesty auto-caches a 2048-token prefix and reports `prompt_tokens_details.cached_tokens` with a `cost` delta; Synthetic reports `cached_tokens` but no cost field; ollamacloud reports nothing. optiproxai already forwards the upstream `usage` object verbatim to clients, but does not surface cache data in its own tooling.

## Metrics
1. **Cache read tokens** — per request and aggregated, with % of input tokens served from cache (cached_tokens / prompt_tokens)
2. **Cache creation tokens** — per request and aggregated (first call in a TTL window writes)
3. **Warm-up detection** — first call for a given (provider, model, prefix fingerprint) writes cache, subsequent identical prefixes read
4. **Cost impact estimate** — where provider cache-read price differs from input price, estimate savings; optional, per-model pricing metadata needed

## Data plumbing
- `logger.py`/`dashboard.py`: `_log_usage` (proxy.py ~line 548) currently extracts only prompt/completion/total from `usage` and passes them to `log_execution_event`. Add cache fields: `cached_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens` (with tolerance for provider-specific field names — Anthropic uses `cache_read_input_tokens` directly, OpenAI-compatible nests under `prompt_tokens_details.cached_tokens`).
- `routing_logs` or new `usage_logs` columns via `_ensure_column` migration
- `dashboard.py`: new query functions, new render section following `_model_usage_rows`/`_render_simple_table` patterns

## Section layout
- Stat card(s): cache read tokens (24h/7d/30d), cache hit rate % of input tokens, total estimated savings
- Table: per provider/model cache reads, cache creations, hit rate, cost estimate
- Optionally a daily trend of cache hit rate

## References
- `src/optiproxai/proxy.py` `_log_usage` (~line 548) — usage extraction point
- `src/optiproxai/dashboard.py` — `log_execution_event` (~line 174), `_model_usage_rows` (~line 701), `get_dashboard_stats` (~line 800), `render_dashboard_html` (~line 1169), `_ensure_column` migration pattern
- `src/optiproxai/logger.py` — routing log JSONL
- Related backlog: TASK-5 (dashboard routing behavior section, separate scope: override/async/escalation/fallback)
<!-- SECTION:DESCRIPTION:END -->
