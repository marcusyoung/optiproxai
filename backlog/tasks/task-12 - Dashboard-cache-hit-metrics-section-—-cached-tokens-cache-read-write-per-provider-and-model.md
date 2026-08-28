---
id: TASK-12
title: >-
  Dashboard cache-hit metrics section — cached tokens, cache read/write per
  provider and model
status: Done
assignee: []
created_date: '2026-08-19 14:44'
updated_date: '2026-08-28 18:56'
labels: []
dependencies: []
documentation:
  - >-
    docs/decisions/doc-9 -
    Decision-Cache-usage-columns-live-in-execution_logs-not-a-new-usage_logs-table.md
  - >-
    docs/decisions/doc-10 -
    Decision-Model-pricing-metadata-lives-on-ModelEntry-and-ModelRuleEntry-resolved-at-render-time.md
modified_files:
  - src/optiproxai/config.py
  - src/optiproxai/proxy.py
  - src/optiproxai/dashboard.py
  - config.example.yaml
  - README.md
  - tests/test_dashboard.py
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

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Cache-Hit Metrics section renders in the dashboard with stat cards for cache read tokens (24h/7d/30d), cache hit rate % of input tokens, and total estimated savings
- [x] #2 Cache read tokens are extracted from the upstream usage object with tolerance for provider field names (Anthropic cache_read_input_tokens; OpenAI-compatible prompt_tokens_details.cached_tokens; Requesty prompt_tokens_details.cached_tokens; Synthetic cached_tokens; ollamacloud none) and persisted to the logs DB
- [x] #3 Cache creation tokens are extracted from the upstream usage object (cache_creation_input_tokens / prompt_tokens_details) and persisted to the logs DB
- [x] #4 Cache hit rate is computed as cached_tokens / prompt_tokens and surfaced as a percentage in the stat card and per-provider/model table
- [x] #5 A per provider/model table shows cache reads, cache creations (warm-up writes), hit rate, and cost estimate
- [x] #6 Cost impact estimate is shown per model where the provider cache-read price differs from the input price, using per-model pricing metadata; gracefully absent when pricing metadata is unavailable
- [x] #7 Cache columns (cached_tokens, cache_read_input_tokens, cache_creation_input_tokens) are added to the appropriate logs table via the _ensure_column migration (routing_logs or a new usage_logs table)
- [x] #8 proxy.py _log_usage passes the extracted cache fields through to log_execution_event so dashboard.py can query them
- [x] #9 Provider field-name tolerance is verified with test fixtures covering Anthropic, OpenAI-compatible, Requesty, Synthetic, and ollamacloud (no cache data) shapes
- [x] #10 Provider/model breakdown is computed with the same profile-filtering pattern as _model_usage_rows
- [x] #11 Existing dashboard sections (window cards, routing-behavior if present, model usage, daily trends) remain unchanged
- [x] #12 uv run ruff check src/ passes
- [x] #13 uv run ruff format --check src/ tests/ passes
- [x] #14 uv run pyright src/ passes
- [x] #15 uv run pytest tests/ -q passes
- [x] #16 Tests cover each metric with sample DB rows and verify the rendered HTML output contains the expected cache values
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
# TASK-12 Implementation Plan: Dashboard cache-hit metrics section

## Approach

Persist per-request cache token data alongside existing usage columns in `execution_logs`, then surface it as a new dashboard section following the established query/render patterns. Two design decisions are recorded and load-bearing:

- doc-9: Cache columns live in `execution_logs` (three `_ensure_column` migrations), not a new `usage_logs` table. Cache tokens are per-execution usage attributes produced at the same point as prompt/completion tokens; `request_id` is nullable so a join key for a separate table is not guaranteed.
- doc-10: Pricing metadata is an optional `pricing: ModelPricingConfig` on `ModelEntry` and `ModelRuleEntry`, resolved at dashboard render time via `load_config()` (same precedent as `_infer_provider`). Display-only; no routing impact.

## Cache field extraction (write time)

New helper in proxy.py:

    _extract_cache_usage(usage: dict[str, Any] | None) -> tuple[int, int, int]

Returns (cached_tokens, cache_read_input_tokens, cache_creation_input_tokens):

- cached_tokens = usage["cached_tokens"] or usage["prompt_tokens_details"]["cached_tokens"] (OpenAI-compatible, Requesty, Synthetic, Doubleword)
- cache_read_input_tokens = usage["cache_read_input_tokens"] (Anthropic, Doubleword)
- cache_creation_input_tokens = usage["cache_creation_input_tokens"] (Anthropic, Doubleword)
- Missing keys / non-int values coerce to 0. Tolerates prompt_tokens_details being absent or None.

Double-count guard (query layer): Doubleword reports both prompt_tokens_details.cached_tokens AND cache_read_input_tokens for the same request. Queries compute hit tokens as CASE WHEN cached_tokens > 0 THEN cached_tokens ELSE cache_read_input_tokens END so a request is never counted twice. Raw columns keep provider fidelity; normalization happens only in queries.

## Files to modify

### src/optiproxai/config.py
- New ModelPricingConfig(BaseModel): input_per_mtok, cache_read_per_mtok, cache_write_per_mtok (float | None, ge=0; USD per 1M tokens).
- pricing: ModelPricingConfig | None = None on ModelEntry (~line 200) and ModelRuleEntry (~line 420). ModelEntry has extra="forbid" so explicit declaration is required.

### src/optiproxai/proxy.py
- New _extract_cache_usage helper near _log_usage (line 550).
- _log_usage: call the helper, extend the USAGE log line with cache_read=/cache_write= when non-zero, pass the three values to log_execution_event as new keyword args.
- Both call sites (streaming last_usage ~line 717, non-streaming resp_data.get("usage") ~line 761) already pass the full usage dict - no call-site changes needed.

### src/optiproxai/dashboard.py
- log_execution_event (line 367): new keyword params cached_tokens, cache_read_input_tokens, cache_creation_input_tokens (int = 0); include in the JSONL record (keeps re-ingest lossless).
- _init_dashboard_db (line 44): add the three columns to the CREATE TABLE statement AND as _ensure_column migrations for existing DBs.
- _insert_execution_record (line 281): insert the three new columns with record.get(...) or 0 tolerance so old JSONL lines ingest cleanly.
- New query helpers following _model_usage_rows (line 701) patterns, all with _profile_filter_clause profile filtering and a cutoff:
  - _cache_summary(conn, hours, profiles): totals - sum of hit tokens (double-count-guarded CASE), sum of cache_creation tokens, hit rate = hit_tokens / NULLIF(prompt_tokens, 0), request counts.
  - _cache_model_rows(conn, hours, profiles, limit): GROUP BY model, provider - cache reads, cache creations, hit rate %, estimated savings (needs pricing resolution).
- Pricing resolution helper _resolve_model_pricing(model, provider) -> ModelPricingConfig | None: exact model-name match across all profile tiers ModelEntries first, then longest-prefix match over config.model_rules (ModelRuleEntry). Uses load_config() with graceful exception fallback (same pattern as _infer_provider).
- Savings formula (doc-10): savings = (input - cache_read) * read_tokens / 1e6 - (cache_write - input) * creation_tokens / 1e6, missing components treated as 0; render as em-dash when no pricing resolves.
- get_dashboard_stats (line 800): add cache_summary (per window like model_usage: 24h/7d/30d) and cache_model_usage keys.
- render_dashboard_html (line 1171): new Cache Metrics section after model usage - stat cards (hit tokens, hit rate %, creations, est. savings) following _render_window_cards patterns, plus a _render_simple_table per-model table. All values escaped; em-dash for missing pricing.

### config.example.yaml
- Model-level pricing: example inside a tier primary list; rule-level pricing example in model_rules comments.

### README.md
- Extend the existing Prompt caching section (~line 429) and Dashboard section (~line 504): cache metrics surfaced, pricing config shape, savings formula, graceful absence behavior.

### tests/test_dashboard.py
- New test class covering: extraction tolerance per provider shape (Anthropic / OpenAI-nested / Requesty / Synthetic / ollamacloud-none); _insert_execution_record + JSONL round-trip of cache fields; _cache_summary hit-rate math incl. zero-prompt and double-report guard; _cache_model_rows grouping; pricing resolution (ModelEntry exact match, rule prefix match, no match renders em-dash); rendered HTML contains expected cache values.

## Constraints / Risks

- Unique index unchanged: idx_execution_unique covers (timestamp, model, provider, profile, prompt_tokens, completion_tokens, total_tokens) - cache columns are NOT added to it, so re-ingesting old JSONL stays idempotent and new columns never cause duplicate suppression.
- Schema migration order: _ensure_column calls must run before any query touches the new columns; they live in _init_dashboard_db, which every entry point already calls.
- Old rows: existing execution_logs rows get DEFAULT 0 - hit-rate denominators use prompt_tokens, so old rows simply read as 0% cache; no backfill needed.
- ingest_jsonl_logs / ingest_execution_logs: both route through _insert_execution_record, so one change covers live logging and re-ingest.
- Pricing is display-only: resolved at render time; no changes to RoutingDecision/FallbackEntry (doc-4 machinery untouched).
- Warm-up detection (metric 3 in the description) is surfaced as the cache-creations column/counts - first-call writes vs subsequent reads; no prefix fingerprinting in this task (would require request-body hashing; out of scope, possible follow-up).

## Out of scope

- TASK-13 (X-Optiproxai-Cache-* response headers) - separate task.
- Prefix fingerprinting for true warm-up attribution.
- Provider-level pricing defaults.

## Sub-steps (dependency order)

1. Config: ModelPricingConfig + pricing fields (no deps)
2. Schema + persistence: dashboard.py columns, insert, JSONL record (no deps)
3. Extraction + plumbing: proxy.py helper + _log_usage (depends on 2 for the receiving signature)
4. Queries + stats keys (depends on 2)
5. Render section + pricing resolution (depends on 1, 4)
6. Docs: config.example.yaml + README.md (depends on 1, 5)
7. Tests (depends on 2-5)
8. CI gates: ruff check/format, pyright, pytest (depends on all)

## Task Manifest

| # | Title | Files | Depends On | Labels | Acceptance Criterion |
|---|---|---|---|---|---|
| 1 | Add ModelPricingConfig and pricing fields to ModelEntry and ModelRuleEntry | src/optiproxai/config.py | - | logic | ModelPricingConfig exists with input_per_mtok/cache_read_per_mtok/cache_write_per_mtok; ModelEntry and ModelRuleEntry accept optional pricing and config loads without error |
| 2 | Add cache columns to execution_logs schema, insert, and JSONL record | src/optiproxai/dashboard.py | - | database | execution_logs gains cached_tokens, cache_read_input_tokens, cache_creation_input_tokens via _ensure_column; _insert_execution_record and log_execution_event persist them; old JSONL lines still ingest |
| 3 | Extract cache usage in _log_usage and pass through to log_execution_event | src/optiproxai/proxy.py | 2 | logic | _extract_cache_usage handles Anthropic, OpenAI-nested, Synthetic, and absent shapes; _log_usage forwards the three values |
| 4 | Add cache query functions and stats keys | src/optiproxai/dashboard.py | 2 | database | _cache_summary and _cache_model_rows return correct totals, hit rate, and profile filtering; get_dashboard_stats exposes cache_summary and cache_model_usage |
| 5 | Render cache metrics section with pricing-based savings | src/optiproxai/dashboard.py | 1, 4 | view | render_dashboard_html shows stat cards and per-model table; savings renders when pricing resolves, em-dash otherwise |
| 6 | Document pricing and cache metrics | config.example.yaml, README.md | 1, 5 | infra | config.example.yaml has model-level and rule-level pricing examples; README documents cache metrics and pricing shape |
| 7 | Add cache metric tests | tests/test_dashboard.py | 2, 3, 4, 5 | test | Provider-shape tolerance, hit-rate math, pricing resolution, and rendered HTML assertions all pass |
| 8 | Run full CI gates | (no file changes) | 1-7 | test | ruff check, ruff format --check, pyright, and pytest all pass |
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Execution structure (approved 2026-08-28 via Plannotator review)

Scope approved as a SINGLE task - no Phase 2 decomposition into Backlog subtasks. The Task Manifest in the Implementation Plan serves as the todo checklist (8 sequential items). Rationale: steps are small, tightly coupled through the execution_logs schema change, and produce no independently shippable intermediate state.

Branching: single branch task/TASK-012 cut from main, one commit sequence, one PR to main (SDD single-task model).

Load-bearing decision records: doc-9 (cache columns in execution_logs, not a new usage_logs table) and doc-10 (pricing on ModelEntry/ModelRuleEntry resolved at render time).

## Implementation complete (2026-08-28)

All 16 acceptance criteria checked. CI: ruff clean, format clean, pyright 0 errors, pytest 392 passed (+19 new cache tests). One plan addition: pricing propagates through ResolvedModelCandidate so _resolve_model_pricing can read it from tier candidate entries.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
## TASK-12 Complete: Dashboard cache-hit metrics section

### What was built

Cache token data is now extracted from upstream usage objects, persisted to the dashboard DB, and surfaced as a new Cache Metrics section (stat cards + per-model table with pricing-based savings estimates).

### Files changed

- `src/optiproxai/config.py` - new `ModelPricingConfig` (input_per_mtok/cache_read_per_mtok/cache_write_per_mtok, ge=0); `pricing` field on `ModelEntry`, `ModelRuleEntry`, and `ResolvedModelCandidate` (propagated in `_resolve_candidate_entry`, mirroring the async_mode/max_input_tokens precedent)
- `src/optiproxai/proxy.py` - new `_extract_cache_usage()` with provider field-name tolerance (Anthropic top-level, OpenAI/Requesty/Synthetic nested or top-level cached_tokens, junk->0); `_log_usage` extracts and forwards cache fields to `log_execution_event`, extends USAGE log line with cache_read=/cache_write= when non-zero; both call sites unchanged
- `src/optiproxai/dashboard.py` - execution_logs gains cached_tokens/cache_read_input_tokens/cache_creation_input_tokens (CREATE TABLE + `_ensure_column` migrations); `_insert_execution_record` and `log_execution_event` persist them (old JSONL ingests as 0); new `_cache_summary` and `_cache_model_rows` query helpers with a SQL double-count guard (CASE WHEN cached_tokens > 0 THEN cached_tokens ELSE cache_read_input_tokens END) for providers reporting both fields; `_resolve_model_pricing` (exact ModelEntry match then longest-prefix ModelRuleEntry match via load_config, graceful fallback) and `_estimate_cache_savings` (doc-10 formula); `get_dashboard_stats` exposes cache_summary and cache_model_usage (24h/7d/30d); `render_dashboard_html` renders Cache Metrics section (hit-rate stat cards + Cache by Model table) between Model Usage and Daily Rollup
- `config.example.yaml` - model-level pricing example (claude-sonnet tier entry), rule-level pricing example in model_rules comments
- `README.md` - new "Cache metrics in the dashboard" and "Estimating cache savings (pricing)" subsections in the Prompt caching section; Dashboard section updated
- `tests/test_dashboard.py` - 19 new tests in 4 classes: TestCacheUsageExtraction (6 provider shapes), TestCachePersistence (insert, legacy JSONL, log_execution_event), TestCacheQueries (double-report guard, zero-prompt, grouping/profile filter, stats keys), TestCachePricingAndRender (entry/rule resolution, savings math, unpriced em-dash, rendered HTML)

### Design decisions followed

- doc-9: cache columns in execution_logs (per-execution usage attributes; nullable request_id makes a separate table un-joinable)
- doc-10: pricing on ModelEntry/ModelRuleEntry, resolved at render time, display-only
- Double-count guard lives in the query layer; raw columns preserve provider fidelity

### Deviations from plan

One addition not in the plan: `pricing` also propagates through `ResolvedModelCandidate` (`_resolve_candidate_entry`) because `_resolve_model_pricing` reads candidates via `resolve_primary_candidate_entries()`/`resolve_fallback_candidate_entries()`, which normalize ModelEntry and would have dropped the field.

### CI gates

- ruff check src/: passed
- ruff format --check src/ tests/: 38 files formatted (2 auto-reformatted during work)
- pyright src/: 0 errors, 0 warnings
- pytest tests/ -q: 392 passed (baseline was 373; +19 new)
- uv build: succeeded
<!-- SECTION:FINAL_SUMMARY:END -->
