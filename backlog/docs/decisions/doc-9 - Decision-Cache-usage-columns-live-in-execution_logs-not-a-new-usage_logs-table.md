---
id: doc-9
title: >-
  Decision: Cache usage columns live in execution_logs, not a new usage_logs
  table
type: other
created_date: '2026-08-28 18:39'
---
# Decision: Cache usage columns live in execution_logs (no new usage_logs table)

**Status:** Accepted  
**Date:** 2026-08-28  
**Task:** TASK-12 (Dashboard cache-hit metrics section)

## Context

TASK-12 needs to persist per-request cache token data (cached_tokens, cache_read_input_tokens, cache_creation_input_tokens) for dashboard queries. Two options:

1. Add three columns to the existing `execution_logs` table via the `_ensure_column` migration pattern.
2. Create a new `usage_logs` table keyed by request_id, joined to execution_logs at query time.

## Decision

Option 1 — extend `execution_logs`.

## Rationale

- Cache tokens are **per-execution usage attributes**, exactly like the existing prompt_tokens/completion_tokens/total_tokens columns. They are produced at the same point (`_log_usage` in proxy.py) and have the same cardinality (one row per execution).
- A separate table would require a join key. `request_id` is nullable in execution_logs (pass-through requests may lack it), so a join key is not guaranteed to exist.
- `_insert_execution_record`, `ingest_execution_logs`, and `_window_summary` already operate on execution_logs; extending the row keeps the single-insert path and the unique index `idx_execution_unique` intact.
- The `_ensure_column` migration pattern exists precisely for this; existing DBs migrate transparently.
- No query in the dashboard needs usage data at higher granularity than per-execution.

## Consequences

- `execution_logs` gains: `cached_tokens INTEGER DEFAULT 0`, `cache_read_input_tokens INTEGER DEFAULT 0`, `cache_creation_input_tokens INTEGER DEFAULT 0`.
- The JSONL execution record (log_execution_event) carries the same fields so JSONL re-ingest remains lossless.
- Raw provider field fidelity is preserved: columns store values as reported; normalization (e.g. `cache_read_input_tokens` OR nested `prompt_tokens_details.cached_tokens`) happens in the query layer, not at write time.
