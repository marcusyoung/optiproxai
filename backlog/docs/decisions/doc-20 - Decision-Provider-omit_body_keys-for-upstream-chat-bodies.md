---
id: doc-20
title: 'Decision: Provider omit_body_keys for upstream chat bodies'
type: other
created_date: '2026-10-30T12:56:00Z'
updated_date: '2026-10-30T12:59:00Z'
tags:
  - TASK-29
  - proxy
  - cursor
  - mistral
  - config
---
# Decision: Provider omit_body_keys for upstream chat bodies

**Date:** 2026-10-30
**Status:** Decided
**Task:** TASK-29
**Author:** agent

## Context
Cursor injects a top-level OpenAI-compatible `user` field on chat completions. Mistral rejects it with 422 `extra_forbidden`. Other strict OpenAI-compatible APIs may reject additional client-only keys later. The field is request-only and is not echoed in completion responses.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Always strip `user` at `_proxy_upstream` | Tiny change; no config | Affects every provider; not reusable for the next forbidden key |
| Per-provider `omit_body_keys` list (top-level) | Opt-in; reusable; matches existing ProviderConfig patterns | Needs config field + VPS/example wiring |
| Strip in `_prepare_body_for_candidate` only | Reuses prepare path | Passthrough never calls prepare |

## Decision
Add optional `ProviderConfig.omit_body_keys: list[str]` (default empty). At the start of `_proxy_upstream`, for the resolved `actual_provider`, shallow-copy-omit those top-level keys from the outbound body when any are present.

## Rationale
Operators opt in per provider (e.g. mistral: `[user]`). Empty list preserves today’s behaviour for Doubleword/OpenRouter/etc. The same mechanism covers future Cursor/OpenAI-only fields without code changes. `_proxy_upstream` remains the single outbound choke point for routed, fallback, and passthrough attempts.

## Consequences
Live Mistral (and `config.example` / deploy sample) must set `omit_body_keys: [user]` or the 422 remains. Nested JSON paths and glob patterns are out of scope. Do not log omitted key values (may be PII).
