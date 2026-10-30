---
id: doc-21
title: 'Decision: Opt-in response flatten for Mistral-style assistant content parts'
type: other
created_date: '2026-10-30T13:33:00Z'
tags:
  - TASK-30
  - proxy
  - mistral
  - cursor
  - response
---
# Decision: Opt-in response flatten for Mistral-style assistant content parts

**Date:** 2026-10-30
**Status:** Decided
**Task:** TASK-30
**Author:** agent

## Context
Mistral large-4 returns `choices[].message.content` as a list of parts (`type: thinking` and `type: text`) instead of an OpenAI string. Cursor treats HTTP 200 responses with that shape as client failures. Request-side `content_part_policy` does not rewrite completions. Both non-stream JSON and SSE streams must be fixed for Cursor agents.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Always detect/flatten any parts-array content | No config | Surprises providers that intentionally return multimodal assistant content |
| Reuse request `content_part_policy` | Less surface | Wrong semantics; policy is inbound multimodal, not completion flatten |
| Opt-in provider default + optional model_rule override | Matches omit_body_keys / reasoning_style patterns; safe default | Config to set on live Mistral |

## Decision
Add opt-in `normalize_assistant_content_parts` (bool, default false) on `ProviderConfig`, overridable on `ModelRuleEntry` (nullable bool; rule wins when set). When enabled for the upstream attempt: rewrite assistant `message`/`delta` so `content` becomes a string of text parts; map `thinking` parts into string `reasoning_content` when present. Apply in `_proxy_upstream` for non-stream JSON and SSE chunk rewrite. Do not reuse inbound `content_part_policy`.

## Rationale
Cursor needs OpenAI-shaped completions. Opt-in avoids changing Doubleword/OpenRouter responses. Provider-level default fits Mistral; model rules cover exceptions. Single choke point `_proxy_upstream` covers routed, fallback, and passthrough.

## Consequences
Live Mistral must enable the flag after deploy. Streaming must re-serialize SSE JSON (not byte-identical). Unknown part types: concatenate extractable text into content or drop with logging of type names only — prefer concatenate via existing `_text_from_content_part` for unknown non-thinking parts.
