---
id: TASK-23
title: >-
  Fix image-session input-limit inflation: image-aware token estimate +
  provider-reported-overrides sizing
status: To Do
assignee: []
created_date: '2026-09-04 14:45'
updated_date: '2026-09-04 14:45'
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

Root cause, two compounding layers:
1. tokens.py `_estimate_tokens` tokenizes image_url data-URI payloads (base64, ~1.3 MB) as text, inflating the estimate to ~339K tokens. Real provider-side image cost is ~2K tokens (doc-12 bake-off: image_tokens=2058 on syn:large:vision; providers run their own vision pipeline, not text tokenization). Vision-capable candidates are also wrongly excluded: tencent/hy3 (160000), tencent/Hy3-FP8 (160000), mistral-medium-3.5 (262144) all skipped; fallback promotion fires every turn.
2. router.py route() combines sizes as max(last-context provider-reported, live estimate) (TASK-16 / doc-8 semantics). Because the client (opencode) resends the full image every turn, the inflated live estimate always wins, so the provider-reported ~22K never governs. doc-14's assumption that the inflated estimate 'self-heals next provider-reported turn' is FALSE for image sessions.

Agreed design (2026-09-04 session, user-approved direction):
- Part 1: _estimate_tokens counts image parts with a fixed per-image constant (~2048 default, calibrated from doc-12; err-toward-larger per doc-8 convention) instead of tokenizing data URIs as text.
- Part 2: last_context_cache keyed by (session_key, model, provider) — provider-reported size is authoritative per model@provider and OVERRIDES the live estimate (replacing max()) when present; cache entry invalidated when the body's image signature changes between turns (image added / aged out / stripped), because a stripped turn's reported size understates a later vision turn and vice versa. Live image-aware estimate is the fallback path (preserves TASK-16's undercount protection).
- Part 3: amend decision doc doc-14 (self-heal assumption false) and append override semantics to doc-8.

Note: per-image cost is provider-specific; the constant is a conservative baseline only — precision comes from the provider-reported override layer.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 _estimate_tokens does not tokenize image_url data payloads as text; each image part contributes a fixed per-image constant (default ~2048) to the estimate
- [ ] #2 Input-limit eligibility no longer rejects ~22-25K image-session prompts against 160K/262K caps: hy3, Hy3-FP8, and mistral-medium-3.5 are eligible candidates on image sessions in routing tests
- [ ] #3 last_context_cache entries are keyed by (session_key, model, provider); a provider-reported size only governs eligibility for the same model@provider
- [ ] #4 Provider-reported size overrides the live estimate (replacing max()) when a same-model@provider entry exists and the image signature is unchanged; live estimate governs on first turn, cache miss, or signature change
- [ ] #5 Cache entry is invalidated when the body's image signature changes between turns (image added, aged out, or stripped), verified by routing tests
- [ ] #6 Existing max()-semantics tests are updated; full suite passes; ruff/pyright/format/build clean (CI bar)
- [ ] #7 Decision docs updated: doc-14 amended (self-heal assumption false) and doc-8 amended with provider-reported-overrides semantics
<!-- AC:END -->
