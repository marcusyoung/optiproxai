---
id: doc-22
title: >-
  Decision: opt-in unwrap of OpenAI image_url object to Mistral string wire
  format
type: other
created_date: '2026-10-30T16:03:00Z'
tags:
  - TASK-31
  - mistral
  - vision
  - proxy
  - request
---
# Decision: opt-in unwrap of OpenAI image_url object to Mistral string wire format

**Date:** 2026-10-30
**Status:** Decided
**Task:** TASK-31

## Context
Cursor sends OpenAI-style multimodal parts: `{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."}}`. Mistral chat-completions vision docs show the same part type but with `image_url` as a **string**: `{"type":"image_url","image_url":"data:..."}`. The object shape yields 422 `content should be a valid string` even for vision-capable `mistral-large-4` (model card input: text+image). The pinned profile `optiproxai/mistral-large` always routes to provider `mistral`, so capability routing cannot avoid this. TASK-30 fixed the response side; this is the request side.

## Options Considered

| Option | Pros | Cons |
|---|---|---|
| Always unwrap dict image_url to string for all providers | No config | Changes Doubleword/OpenRouter wire shape unintentionally |
| Extend request `content_part_policy` | Reuses existing hook | Wrong semantics: policy is part filtering/normalization, not provider wire format |
| Opt-in provider flag + optional model_rule override | Matches omit_body_keys / normalize_assistant_content_parts patterns; safe default | Needs config on live Mistral |

## Decision
Add opt-in `unwrap_image_url_objects` (bool, default false) on `ProviderConfig`, overridable on `ModelRuleEntry` (nullable bool; rule wins when set). When enabled for the candidate, rewrite every `image_url` part whose `image_url` is an object with a string `url` into `{"type":"image_url","image_url": <url string>}` before upstream send. Text parts and other part types untouched. Apply in `_prepare_body_for_candidate` after `content_part_policy` normalization (so policy output is normalized first), covering routed, fallback, and passthrough.

## Rationale
Keeps OpenAI-compatible providers byte-identical; Mistral gets its documented string shape; single choke point covers all paths.

## Consequences
Live Mistral must enable the flag after deploy. Non-URL shapes (e.g. `file_id`) are left unchanged.
