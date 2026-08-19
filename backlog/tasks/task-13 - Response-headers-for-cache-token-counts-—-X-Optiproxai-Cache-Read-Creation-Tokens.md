---
id: TASK-13
title: >-
  Response headers for cache token counts —
  X-Optiproxai-Cache-Read/Creation-Tokens
status: To Do
assignee: []
created_date: '2026-08-19 14:44'
labels: []
dependencies: []
priority: low
type: feature
ordinal: 17000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Expose prompt-cache token counts as response headers on every proxied chat-completion response, so cache activity is visible without opencode/openchamber UI support or parsing the response body.

## Background (2026-08-19 testing)
opencode/openchamber do not render cache fields (e.g. `prompt_tokens_details.cached_tokens`, `cache_read_input_tokens`) for generic OpenAI-compatible endpoints, even though optiproxai forwards the upstream `usage` object verbatim. Providers vary: Doubleword and Requesty deliver real cache savings (2048-token prefixes at discounted read rates), while others report nothing. Response headers give a lightweight, universally readable signal alongside the existing `X-Optiproxai-*` headers.

## Implementation
- In `_proxy_upstream` (src/optiproxai/proxy.py, ~line 467) where `_optiproxai_headers` builds `X-Optiproxai-Tier/-Model/-Provider/-Score/-Signals`, add two headers derived from the upstream response `usage`:
  - `X-Optiproxai-Cache-Read-Tokens`
  - `X-Optiproxai-Cache-Creation-Tokens`
- Extract from provider-specific usage shapes: `usage.cache_read_input_tokens`, `usage.cache_creation_input_tokens`, and OpenAI-compatible `usage.prompt_tokens_details.cached_tokens` / `cache_write_tokens`.
- Headers should be present even when zero (value 0) so clients can distinguish "no cache" from "missing data".
- Streaming path: cache fields arrive in the final SSE usage chunk (stream_options.include_usage is already injected); the streaming response cannot be modified after headers are sent, so streaming responses cannot carry these headers — document this limitation.

## Acceptance
- Non-streaming responses carry the two headers with correct values
- Zero values are emitted as 0
- Existing X-Optiproxai-* headers unchanged
- Streaming responses document why headers cannot be set (headers sent before usage chunk arrives)
<!-- SECTION:DESCRIPTION:END -->
