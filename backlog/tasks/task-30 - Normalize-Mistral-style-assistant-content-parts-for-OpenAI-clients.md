---
id: TASK-30
title: Normalize Mistral-style assistant content parts for OpenAI clients
status: Done
assignee: []
created_date: '2026-10-30T13:32:00Z'
updated_date: '2026-10-30T13:42:00Z'
labels: []
dependencies: []
references:
  - src/optiproxai/proxy.py
  - src/optiproxai/config.py
  - https://github.com/marcusyoung/optiproxai/pull/26
  - doc-21
  - tests/test_proxy_reload.py
  - config.example.yaml
  - README.md
documentation:
  - doc-21
modified_files:
  - src/optiproxai/config.py
  - src/optiproxai/proxy.py
  - tests/test_proxy_reload.py
  - config.example.yaml
  - README.md
priority: high
type: bug
ordinal: 30000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Cursor treats successful Mistral chat completions as failures because mistral-large-4 returns message.content as an array of parts (type thinking + type text) instead of an OpenAI string. Observed after TASK-29 omit_body_keys fixed the 422 on body.user: proxy and Apache returned 200 with USAGE, but Cursor reported server failed. A live curl to optiproxai/mistral-large returned content parts including thinking and text pong. Existing content_part_policy and reasoning_style only adapt the outbound request, not the completion response. Clients like Cursor need string content (and optionally reasoning_content) on both non-streaming JSON and SSE streams.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Opt-in provider or model config enables assistant content-part flattening for selected upstreams
- [x] #2 Non-streaming completions that return thinking/text content parts are rewritten so client-facing message.content is a string and thinking is not left as a content parts array
- [x] #3 Streaming SSE chunks with the same part shapes are rewritten equivalently before reaching the client
- [x] #4 Providers without the opt-in still pass response bodies through unchanged
- [x] #5 Tests cover non-stream and stream fixtures shaped like Mistral large-4 responses
- [x] #6 README or config.example documents the opt-in with a Mistral example
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
## Approach

Add opt-in response normalization for Mistral-style assistant content parts (doc-21). When enabled for the upstream provider/model, rewrite completion payloads so clients receive OpenAI-shaped `content` (string) and optional `reasoning_content` (string from thinking parts). Wire in `_proxy_upstream` for both non-stream JSON and SSE streams. Do not reuse request-side `content_part_policy`.

## Config

- `ProviderConfig.normalize_assistant_content_parts: bool = False`
- `ModelRuleEntry.normalize_assistant_content_parts: bool | None = None` (when set, overrides provider for matching model/provider best-score, same pattern as other `_get_model_*` helpers)
- Resolve via `_resolve_normalize_assistant_content_parts(model, provider, runtime) -> bool`

## Implementation steps

1. **Config fields** in `src/optiproxai/config.py` with short Field descriptions (TASK-30).
2. **Pure helpers** in `proxy.py`:
   - `_flatten_assistant_content_parts(content) -> tuple[str | None, str | None]` — if content is not a list of dict parts, return unchanged signal (None,None) / leave as-is; if list: extract `type==thinking` → reasoning text, `type==text` (and other non-thinking) → content string via `_text_from_content_part` / nested thinking text.
   - `_normalize_assistant_message(message: dict) -> dict` — copy-on-change; set string `content`; set `reasoning_content` when thinking text non-empty; leave tool_calls alone.
   - `_normalize_completion_payload(payload: dict) -> dict` — walk `choices[].message`.
   - `_normalize_stream_chunk(chunk: dict) -> dict` — walk `choices[].delta` the same way (`delta.content` list → string; thinking → `delta.reasoning_content` or message-level if that's what Mistral uses — match observed stream fixtures; if stream unknown, probe live or use non-stream shape as default and add stream test with synthetic list deltas).
3. **Wire `_proxy_upstream`**:
   - Resolve opt-in from `actual_provider` + `body["model"]` (post-rewrite model id) / decision model.
   - Non-stream: after successful_failure check, if enabled: `resp_data = _normalize_completion_payload(resp_data)` then JSONResponse.
   - Stream: when parsing a `data:` JSON line in `_stream()` (and when yielding buffered lines), if enabled rewrite chunk then `yield "data: " + json.dumps(chunk, ensure_ascii=False, separators=(",", ":")) + "\n"`; pass through non-JSON / `[DONE]` unchanged. Keep usage extraction on rewritten or original chunk (usage usually separate).
4. **Logging**: INFO once per request when a rewrite occurs (request_id, provider, stream bool) — no content values.
5. **Tests** (`tests/test_proxy_reload.py` or dedicated file):
   - Fixture from live curl: non-stream Mistral-shaped message → string content `"pong"`, thinking not in content array; optional reasoning_content present.
   - Opt-in off → payload unchanged.
   - Stream: synthetic SSE lines with list `delta.content` parts → client sees string deltas.
   - Config load defaults false.
6. **Docs**: `config.example.yaml` mistral block with `normalize_assistant_content_parts: true` next to `omit_body_keys`; README short note under omit/client compatibility.
7. **Operator**: after merge, set flag on live VPS `providers.mistral` and restart (with omit_body_keys already set).

## Files

- `src/optiproxai/config.py`
- `src/optiproxai/proxy.py`
- `tests/test_proxy_reload.py` (or new focused test module)
- `config.example.yaml`, `README.md`

## Constraints / risks

- Streaming re-serialization changes byte identity of SSE (acceptable).
- Partial stream deltas: if Mistral streams string fragments already, leave them; only flatten when content is a list.
- Do not strip `tool_calls`.
- Keep `deploy/config.vps.yaml` local drift out of this PR unless intentionally syncing the mistral flags.
- Rotate any API keys previously pasted in chat (ops, not this PR).

## Validation

- Unit/integration tests green; full CI bar.
- Live: curl `optiproxai/mistral-large` returns string `content`; Cursor agent turn no longer "server failed".

## Out of scope

- Changing request-side `content_part_policy`.
- Disabling Mistral thinking at the API (prefer normalize what we get).
- `optiproxai/<provider>/<model>` virtual paths.

## Task Manifest

| # | Title | Files | Depends On | Labels | Acceptance Criterion |
|---|---|---|---|---|---|
| 1 | Add normalize_assistant_content_parts + response rewrite (stream+non-stream) + tests + docs | src/optiproxai/config.py, src/optiproxai/proxy.py, tests/test_proxy_reload.py, config.example.yaml, README.md | — | logic, test, docs | Opt-in flattens Mistral-style assistant content parts to string content (and reasoning_content) for non-stream and SSE; off leaves bodies unchanged; tests and docs cover it |

Single-task plan: no Phase 2 subtasks. Branch `task/TASK-30` already cut from main.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented opt-in normalize_assistant_content_parts on ProviderConfig (default false) and ModelRuleEntry override; resolve via _resolve_normalize_assistant_content_parts.

Added flatten helpers and wired _proxy_upstream for non-stream JSON and SSE rewrite with one INFO log per rewritten request (no content values).

Tests: 8 new cases covering flatten helpers, opt-in on/off non-stream, and stream delta parts. Full suite 536 passed; ruff/pyright clean.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added opt-in normalize_assistant_content_parts so Mistral-style assistant content parts (thinking + text arrays) are flattened to OpenAI string content plus optional reasoning_content for Cursor clients. Provider default is false; model_rules can override. Wired in _proxy_upstream for both non-stream JSON and SSE. Documented in config.example.yaml and README next to omit_body_keys. Tests cover helpers, opt-in on/off, and stream rewrite. Full suite 536 passed; ruff/pyright clean. Operator follow-up after merge: enable the flag under live providers.mistral and restart; rotate any API key pasted during earlier curl smoke tests.
<!-- SECTION:FINAL_SUMMARY:END -->
