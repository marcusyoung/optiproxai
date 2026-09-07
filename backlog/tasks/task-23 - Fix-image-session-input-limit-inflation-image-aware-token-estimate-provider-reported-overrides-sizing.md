---
id: TASK-23
title: >-
  Fix image-session input-limit inflation: image-aware token estimation (flat
  2048 constant)
status: In Progress
assignee: []
created_date: '2026-09-04 14:45'
updated_date: '2026-09-04 16:39'
labels:
  - routing
  - input-limit
  - token-estimate
  - image
  - fallback
dependencies: []
references:
  - >-
    docs/decisions/doc-8 -
    Session-keyed-last-context-token-cache-for-input-limit-gating.md
  - docs/decisions/doc-12 - image model bake-off.md
  - >-
    docs/decisions/doc-14 - last-context cache inflated estimate on stripped
    turns.md
  - >-
    backlog/tasks/task-16 -
    Fix-Hy3-input-limit-cap-misfire-session-keyed-last-context-token-cache.md
documentation:
  - src/optiproxai/tokens.py
  - src/optiproxai/router.py
  - src/optiproxai/last_context_cache.py
  - src/optiproxai/proxy.py
priority: high
type: bug
ordinal: 24000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Production evidence (2026-09-04 session logs, resuming handoff 079cbc4d TASK-17 image-history stripping): during image sessions every candidate is rejected by the input-limit filter and routing is forced to fallback promotion every turn, even though the real prompt is ~22-25K tokens.

Root cause: tokens.py `_estimate_tokens` tokenizes image_url content parts as text. An `image_url` part is a dict; `_count_str_tokens` `str()`s it, so the entire base64 data-URI payload (~1.3 MB) passes through the tokenizer as prose, inflating the estimate to ~339K tokens. Real provider-side image cost is small — providers price images by resolution via their own vision pipeline, not by file size (doc-12 bake-off: image_tokens=2058 for a ~1.3MB image on syn:large:vision; documented provider formulas range 85 to ~6K tokens per image). Vision-capable candidates are wrongly excluded too: tencent/hy3 (160000), tencent/Hy3-FP8 (160000), mistral-medium-3.5 (262144) all skipped every turn; fallback promotion fires.

Design (approved via Plannotator plan review 2026-09-04):
- _estimate_tokens counts each image_url content part as a fixed flat constant (_IMAGE_PART_TOKEN_ESTIMATE = 2048, calibrated from doc-12 measurement 2058 and cross-checked against documented provider formulas via web search) instead of tokenizing the data URI. URL-reference images count the constant too. No routing restructuring, no cache-semantics change: doc-8 max(cached, live) semantics are UNCHANGED — with a sane ~24K estimate, max() errs high as doc-8 intended.
- Stripping (TASK-17) is a capability constraint feature (non-vision models 400 on image parts), NOT a token-savings feature — retained image cost is only ~2-3K tokens provider-side. TASK-23 fixes estimation; the two features are complementary.
- Amend doc-14: the self-heal assumption was false only because the inflated estimate never healed (client resends the image every turn); under the fixed estimate the assumption holds; doc-14's deferred-discount follow-up is moot.

Explicitly out of scope: cache re-keying to (session_key, model, provider); provider-reported-overrides replacing max(); image-signature cache invalidation; doc-8 changes.

Full approved plan: ~/.plannotator/plans/task-23-plan-image-aware-token-2026-09-04-approved.md
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 _estimate_tokens does not tokenize image_url data payloads as text; each image_url content part contributes a fixed flat constant (_IMAGE_PART_TOKEN_ESTIMATE = 2048) to the estimate; URL-reference images also count the constant
- [ ] #2 Input-limit eligibility no longer rejects ~22-25K image-session prompts against 160K/262K caps: hy3, Hy3-FP8, and mistral-medium-3.5 are eligible candidates on image sessions in routing tests
- [ ] #3 Near-cap guard test: session estimated near a model's input cap plus a large image payload keeps the capped model ineligible (under-count protection)
- [ ] #4 doc-8 max(cached, live) semantics unchanged and existing cache tests pass without semantic updates
- [ ] #5 doc-14 amended: root cause was estimation tokenizing base64 as text; self-heal assumption holds under fixed estimate; deferred-discount follow-up noted moot
- [ ] #6 Full suite passes; ruff check, ruff format --check, pyright, pytest, uv build all clean (CI bar)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
# Implementation Plan — TASK-23: Image-aware token estimation

## Problem
`_estimate_tokens` (src/optiproxai/tokens.py) tokenizes image content parts as text. An `image_url` content part is a dict; `_count_str_tokens` `str()`s it, so the entire base64 data URI (~1.3 MB) passes through the tokenizer as prose, inflating the estimate to ~339K tokens. The real provider-side image cost is ~2K tokens (doc-12: image_tokens=2058 on syn:large:vision for a ~1.3MB image; providers price images via their own vision pipeline, not text tokenization).

Production consequence (2026-09-04 logs): during image sessions the input-limit filter rejects every candidate (tencent/hy3 @160000, tencent/Hy3-FP8 @160000, mistral-medium-3.5 @262144 — all against a true ~22-25K prompt) and routing is forced to fallback promotion every turn.

## Approach
Single change: image parts contribute a fixed per-image constant to the estimate instead of their data-URI text. No routing restructuring, no cache-semantics change.

Why per-candidate stripping-then-estimating is NOT needed: an in-TTL image makes `vision` a required capability, so non-vision candidates are already excluded by the capability filter before input-limit filtering runs. The only non-vision candidates that can reach input-limit filtering with images in the body are aged-out cases, which get stripped later — a small constant over-count (constant vs true 0) for those is harmless and errs high. For vision candidates the constant approximates the real ~2K cost. One image-aware estimate covers both.

Cache semantics (doc-8 `max(cached, live)`) are UNCHANGED. The pathology was the 339K estimate always winning max(); with a sane ~24K estimate, max() errs high as doc-8 intended. doc-14's 'self-heals next provider-reported turn' assumption remains correct under the fixed estimate (it was false only because the inflated estimate never healed — see amendment note).

## Design detail — per-image constant
The image-part token cost is provider-specific (tile-based providers scale with resolution; measured data point: 2058 tokens for a ~1.3MB image on Synthetic). Two shapes:
- **Flat**: one constant (e.g. 2048) for every image part.
- **Size-banded**: constant chosen by data-URI byte length (e.g. <200KB → 1024, <1MB → 2048, >1MB → 8192). Approximates resolution-based pricing; errs high exactly for large payloads, which is the safe direction near caps.

Under-count risk exists only for vision-capable candidates receiving an image near the model's input cap. In the current config all vision-capable models have 1M caps, so flat is defensible; banded is cheap insurance for configs with small-capped vision models.

**The user decides flat vs banded before implementation.**

## Files to modify
- `src/optiproxai/tokens.py` — in `_estimate_tokens`, when walking message content, detect `image_url`-type content parts (list-of-parts content, same shape `_message_has_image` uses in proxy.py) and count each as the per-image constant instead of tokenizing the part dict. URL-reference images (http URLs, short strings) also use the constant (not tokenized as text). Add module constant(s) at top, UPPER_SNAKE_CASE.
- `tests/test_input_limit_routing.py` — extend: image-session prompts with data-URI images estimate ~22-25K-scale (not 300K+) and capped candidates (160000/262144) remain eligible; near-cap guard test: session estimated near a cap + large image payload keeps the capped model ineligible.
- `tests/test_scorer.py` or a tokens test — unit tests for `_estimate_tokens` image handling (image part counted as constant, not tokenized; text parts unaffected; tools/reasoning_content counting unchanged).
- `backlog/docs/decisions/doc-14 ...md` — append amendment: root cause of the observed inflation was estimation tokenizing base64 as text (this task); the self-heal assumption holds under the fixed estimate; the doc's deferred-discount follow-up is moot.

## Constraints / risks
- No config surface changes; constant is a module-level default (not user-configurable) unless implementation reveals a need.
- Flat under-counts large images on tile-based providers; bounded by banded option if chosen. Over-count direction is always safe (skips a candidate, falls to next).
- `max(cached, live)` semantics preserved: first image turn uses the fixed estimate; turn 2+ provider-reported values govern as today.
- doc-8 semantics untouched; no cache keying or invalidation work.

## Sub-steps
1. tokens.py image-constant estimation (shape TBD by user: flat or banded).
2. Unit tests for image-part estimation.
3. Input-limit routing tests: image-session eligibility + near-cap guard.
4. doc-14 amendment.
5. CI bar: ruff check, ruff format --check, pyright, full pytest, uv build.

## Task Manifest

| # | Title | Files | Depends On | Labels | Acceptance Criterion |
|---|---|---|---|---|---|
| 1 | Add image-aware estimation to _estimate_tokens | src/optiproxai/tokens.py, tests/test_scorer.py | — | logic | _estimate_tokens counts each image_url content part as a fixed constant (shape per user decision) instead of tokenizing its data URI; text/tool/reasoning counting unchanged; unit tests pass |
| 2 | Image-session input-limit routing tests | tests/test_input_limit_routing.py | 1 | test | Data-URI image sessions estimate at true-prompt scale; capped candidates (160000/262144) remain eligible; near-cap session + large image keeps the capped model ineligible; all input-limit tests pass |
| 3 | Amend doc-14 and run CI bar | backlog/docs/decisions/doc-14 - Decision-last-context-cache-keeps-inflated-estimate-on-stripped-turns.md | 1, 2 | docs | doc-14 amended with estimation root cause; ruff/pyright/format/pytest/build all clean |
<!-- SECTION:PLAN:END -->
