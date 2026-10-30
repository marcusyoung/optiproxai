---
id: TASK-31
title: >-
  Unwrap OpenAI image_url object to Mistral string wire format for
  vision-capable upstreams
status: Done
assignee: []
created_date: '2026-10-30T16:03:00Z'
updated_date: '2026-10-30T16:06:00Z'
labels:
  - logic
  - test
  - docs
dependencies: []
references:
  - https://github.com/marcusyoung/optiproxai/pull/27
  - src/optiproxai/proxy.py
  - src/optiproxai/config.py
  - doc-22
  - tests/test_proxy_reload.py
  - config.example.yaml
  - README.md
documentation:
  - doc-21
  - doc-22
modified_files:
  - src/optiproxai/config.py
  - src/optiproxai/proxy.py
  - tests/test_proxy_reload.py
  - config.example.yaml
  - README.md
priority: high
type: bug
ordinal: 31000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Cursor sends OpenAI-style multimodal parts: {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."}}. Mistral chat-completions expects the same type but with image_url as a STRING (docs: {"type":"image_url","image_url":"data:..."}). Sending the object shape yields a 422 ('content should be a valid string') even for vision-capable models like mistral-large-4 (model card: input text+image). Route: pinned profile optiproxai/mistral-large -> provider mistral, model mistral-large-4, so capability routing cannot avoid this. Need an opt-in request-side rewrite that unwraps image_url dict->string for selected providers/models, without changing text parts or affecting other providers. This is separate from TASK-30 response flattening.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Opt-in config enables image_url object->string unwrapping for selected provider/model
- [x] #2 Non-stream and stream request bodies with {url:...} image parts are rewritten before upstream send
- [x] #3 Providers without the opt-in forward image parts unchanged
- [x] #4 Text parts and non-image_url parts are untouched
- [x] #5 Tests cover Mistral-shaped image fixtures plus opt-in off
- [x] #6 README/config.example document the opt-in with a Mistral example
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
## Approach

Add opt-in request-side rewrite that unwraps OpenAI-style `image_url` objects to Mistral's string wire format (doc-22). When enabled for the candidate provider/model, every `image_url` content part whose `image_url` is a dict with a string `url` becomes `{"type":"image_url","image_url": <url>}`. Text parts and other part types are untouched. Separate from TASK-30 response flattening.

## Config

- `ProviderConfig.unwrap_image_url_objects: bool = False`
- `ModelRuleEntry.unwrap_image_url_objects: bool | None = None` (rule wins when set; same best-score pattern as other `_get_model_*` helpers)
- Resolve via `_resolve_unwrap_image_url_objects(model, provider, runtime) -> bool`

## Implementation steps

1. **Config fields** in `src/optiproxai/config.py` with short Field descriptions (TASK-31).
2. **Pure helpers** in `proxy.py`:
   - `_unwrap_image_url_objects_in_body(body) -> dict` — copy-on-change; walk `messages[].content[]`; rewrite only `type == "image_url"` parts with dict `image_url` containing string `url`; log one INFO with counts when changed (no URLs).
3. **Wire `_prepare_body_for_candidate`**: after `_normalize_message_content_for_candidate`, if `_resolve_unwrap_image_url_objects(model, provider_name, runtime)` is true, apply the unwrap. This covers routed, fallback, and passthrough since all go through `_prepare_body_for_candidate`.
4. **Tests** (`tests/test_proxy_reload.py` or focused module):
   - Cursor-shaped fixture with `{"image_url":{"url":"data:image/jpeg;base64,AAA"}}` -> string `image_url` when enabled.
   - Opt-in off -> part unchanged.
   - Text parts and non-image parts untouched; `file_id`-only parts unchanged.
   - Config load defaults false; rule override wins.
5. **Docs**: `config.example.yaml` mistral block with `unwrap_image_url_objects: true` next to `normalize_assistant_content_parts`; README short note.
6. **Operator**: after merge, set flag on live VPS `providers.mistral` and restart.

## Files

- `src/optiproxai/config.py`
- `src/optiproxai/proxy.py`
- `tests/test_proxy_reload.py` (or new focused test module)
- `config.example.yaml`, `README.md`

## Constraints / risks

- Only unwrap dicts with string `url`; leave `file_id` and other shapes alone.
- Must run after `content_part_policy` normalization so policy-shaped output is also unwrapped.
- Keep `deploy/config.vps.yaml` local drift out of this PR.

## Validation

- Unit/integration tests green; full CI bar.
- Live: Cursor image turn to `optiproxai/mistral-large` no longer 422s; curl with image part returns 200.

## Out of scope

- Response-side changes (TASK-30 done).
- Changing `content_part_policy` semantics.
- Non-vision routing changes.

## Task Manifest

| # | Title | Files | Depends On | Labels | Acceptance Criterion |
|---|---|---|---|---|---|
| 1 | Add unwrap_image_url_objects + apply in _prepare_body_for_candidate + tests + docs | src/optiproxai/config.py, src/optiproxai/proxy.py, tests/test_proxy_reload.py, config.example.yaml, README.md | — | logic, test, docs | Opt-in unwraps image_url objects to strings for Mistral-shaped image parts before upstream send; off leaves bodies unchanged; tests and docs cover it |

Single-task plan: no Phase 2 subtasks. Cut branch `task/TASK-31` from main at implementation.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented opt-in unwrap_image_url_objects on ProviderConfig (default false) and ModelRuleEntry override; resolve via _resolve_unwrap_image_url_objects.

Added _unwrap_image_url_objects_in_body and wired after content_part_policy normalization in _prepare_body_for_candidate; INFO log with counts only (no URLs).

Tests: 4 new cases covering unwrap, identity for string/file_id/text parts, enabled and disabled prepare paths. Full suite 541 passed; ruff/pyright clean.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added opt-in unwrap_image_url_objects so Cursor's OpenAI image_url object shape is rewritten to Mistral's string wire format before upstream send, fixing 422 on vision-capable mistral-large-4 image turns. Provider default false; model_rules can override. Applied in _prepare_body_for_candidate after content_part_policy normalization, covering routed/fallback/passthrough. Documented in config.example.yaml and README. Tests cover unwrap on/off and untouched part shapes. Full suite 541 passed; ruff/pyright clean. Operator follow-up after merge: enable unwrap_image_url_objects: true under live providers.mistral and restart.
<!-- SECTION:FINAL_SUMMARY:END -->
