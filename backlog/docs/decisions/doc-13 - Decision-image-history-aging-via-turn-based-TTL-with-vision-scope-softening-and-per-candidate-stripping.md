---
id: doc-13
title: >-
  Decision: image-history aging via turn-based TTL with vision-scope softening
  and per-candidate stripping
type: other
created_date: '2026-09-04 12:43'
updated_date: '2026-09-04 13:24'
---
# Decision: image-history aging via turn-based TTL with vision-scope softening and per-candidate stripping

**Date**: 2026-09-04 (amended same day — v1 semantics corrected)
**Status**: Accepted (revised)
**Related**: TASK-17, doc-12 (designated image model), doc-8 (last-context cache), doc-11 (cache_control targets)

## Decision (revised)

Opt-in image-history aging (TASK-17) is implemented as two coupled changes with **turn-based TTL semantics**:

1. **TTL-scoped vision requirement in capability detection.** `_detect_required_capabilities` requires `vision` iff the latest user message has an image OR any image-bearing message is within `image_ttl_turns` user turns of the latest user message (message_index > latest_user_idx - ttl). While images are in TTL, the session routes to vision-capable models with the full body — no stripping occurs in this phase. Once all images are aged out, `vision` is no longer required and non-vision candidates (e.g. DeepSeek-V4-Pro 1M) become eligible.
2. **Per-candidate stripping in `_prepare_body_for_candidate`.** A sanitizer runs in the candidate prep chain (after reasoning-content sanitize, before content-part normalization and cache_control injection). It applies iff the policy is enabled AND the selected candidate lacks the `vision` capability (matched via model_rules prefix/provider, same pattern as `_get_model_content_part_policy`). Non-vision candidates NEVER receive image parts: every aged-out history image part is replaced with the configured placeholder (empty placeholder = drop). Vision-capable candidates always receive the body unchanged. Fallback candidates are covered automatically (non-vision fallbacks only enter the pool once vision is not required, and their bodies are then image-free).

## Why v1 was corrected (message-window → turn-based TTL)

The approved v1 used a `keep_recent_images` message-count window: "keep the most recent N image-bearing messages". For the primary use case (one QGIS/dashboard screenshot per session) the most recent image-bearing message is that screenshot for the ENTIRE session — it never aged out. Combined with detection softening that dropped `vision` immediately for history-only images, non-vision candidates were eligible from the first follow-up turn AND received the still-present image part. Consequences: (a) the feature's core behavior (hand-back to non-vision models) never actually happened for single-image sessions; (b) strict non-vision providers (verified: tencent/hy3 @ novita returns non-retryable 400 "model features vision not support") would fail the request. User review caught this; semantics corrected to turn-based TTL per the user's original intent: "retain the image and image capability for x turns, then remove the image from context so non-vision models can be selected".

Field renamed `keep_recent_images` → `image_ttl_turns` (default 3: send turn + two follow-up exchanges, matching a paste-screenshot-ask-questions arc; user-tunable).

## Semantics

- Never touch the latest user message; an image in it always requires `vision` regardless of TTL.
- An image is vision-relevant while (latest_user_turn_ordinal - image_user_turn_ordinal) <= image_ttl_turns, where turn ordinals count **user-role messages only** — assistant/tool messages between user turns do not advance aging. (Amended same session: v1 first used raw message-index distance, which under-counted turns for interleaved assistant turns and disagreed with the "user turns" prose; corrected to user-turn ordinals during branch review.)
- Non-vision candidates never receive image parts — no reliance on provider image tolerance.
- Placeholder default `[image omitted]`, deterministic, so the stripped prefix is byte-stable turn-over-turn; provider prefix cache breaks once on the aging turn then resumes (more cache-friendly overall than image-bearing prefixes, which broke reads entirely in the kimi incident).
- Strip order before normalization and cache_control injection so markers survive and normalization cannot resurrect stripped content.
- Stripping is logged (`IMAGE_HISTORY_STRIPPED model= provider= stripped_messages= stripped_parts=`) at INFO.

## Known limitation (accepted for v1)

Session-sticky primary selection may keep a session on the vision model it first routed to even after images age out; stripping still guarantees correctness (non-vision fallbacks work) and the hand-back improves as stickiness windows expire. Revisit if observed.
