---
id: TASK-17
title: Opt-in image-history stripping for non-vision candidates (config policy)
status: Done
assignee: []
created_date: '2026-08-28 15:24'
updated_date: '2026-09-04 14:01'
labels:
  - enhancement
  - routing
  - config
  - vision
  - content-policy
dependencies: []
references:
  - >-
    decisions/doc-12 -
    Decision-syn-large-vision-designated-image-model-bake-off-evidence.md
documentation:
  - >-
    decisions/doc-12 -
    Decision-syn-large-vision-designated-image-model-bake-off-evidence.md
  - >-
    decisions/doc-13 -
    Decision-image-history-stripping-routes-via-vision-scope-softening-and-strips-per-candidate.md
  - >-
    decisions/doc-14 -
    Decision-last-context-cache-keeps-inflated-estimate-on-stripped-turns.md
priority: high
ordinal: 21000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Vision capability is detected from the WHOLE message history, so a single image anywhere in a session pins all subsequent turns to vision-capable models. In the analysis COMPLEX tier this excluded the 1M DeepSeek-V4-Pro fallback and promoted syn:large:vision (524K cap) for 154-198K prompt turns (observed 2026-08-28, TASK-016 follow-up) — correct behavior, but long mixed image+text conversations pay synthetic prices and face a 512K ceiling.

Feature: opt-in config policy to strip image parts from older conversation turns for non-vision candidates, so post-image turns can route back to cheaper/larger non-vision models (e.g. DeepSeek-V4-Pro 1M).

Design points to settle during planning:
1. Trigger: only for candidates lacking vision (start narrow; do not also strip for vision-capable models to cut cost).
2. Window: keep the most recent N image-bearing user messages (N>=1, default 1); "leave for x turns then remove" maps onto counting image-bearing turns.
3. Placeholder: replace with text like [image omitted] vs silently drop the part — provider prompt-shape tolerance may differ; decide per provider if needed.
4. NEVER touch the latest user message — only degrade history.
5. KEY DECISION - last-context cache interaction: provider prompt_tokens includes image tokens; after stripping, the real prompt shrinks but max(cached, live) keeps the inflated estimate and forces escalation, partly defeating the savings. Choose: keep inflated value (consistent with TASK-016 err-toward-escalation bias) vs reset cache entry on stripped turns (risks underestimation). Needs an explicit decision record.
6. Quality caveat to document: later turns referencing earlier images ("compare with the chart above") degrade to placeholder-based reasoning — silent quality loss. Opt-in containment is the point.

Fits existing machinery: proxy already rewrites message content per candidate (_sanitize_reasoning_content_for_candidate, _normalize_message_content_for_candidate, content_part_policy) — a new image-history sanitizer mirrors that pattern. Also mirrors tools_capability_detection's declared-vs-required philosophy.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 When image_history_stripping is enabled and an image is within image_ttl_turns user turns of the latest user message (or in the latest user message), _detect_required_capabilities requires 'vision'; aged-out images (older than TTL) do not.
- [ ] #2 When the policy is disabled (default), behavior is bit-identical to current: 'vision' is required on any image anywhere in history and no stripping ever occurs.
- [ ] #3 A per-candidate sanitizer in _prepare_body_for_candidate replaces AGED-OUT (beyond image_ttl_turns) image parts in history for candidates lacking 'vision'; the latest user message is never touched; non-vision candidates never receive image parts.
- [ ] #4 Stripped image parts are replaced by a text part with the configured placeholder (default '[image omitted]'); an empty placeholder drops the part.
- [ ] #5 Vision-capable candidates receive the body unchanged; non-vision fallback candidates only become eligible once images are aged out and their bodies are image-free.
- [ ] #6 Stripping runs before content_part_policy normalization and cache_control injection; existing cache_control injection tests still pass unchanged.
- [ ] #7 IMAGE_HISTORY_STRIPPED INFO log line emitted with model, provider, stripped_messages, stripped_parts counts.
- [ ] #8 Config: ImageHistoryStrippingConfig (enabled: bool = false, image_ttl_turns: int = 3 with gt=0 validation, placeholder: str = '[image omitted]') on SmartProxyConfig; validated and documented in config.example.yaml and README.
- [ ] #9 Tests in tests/test_proxy_reload.py covering: TTL-based detection fresh/aged/explicit-ttl, latest-message protection, per-candidate strip on/off, zero-image non-vision bodies when all aged, empty placeholder, fail-closed, provider-rule precedence, log emission; full suite passes with ruff/pyright clean.
- [ ] #10 README Capability-aware routing section documents the TTL two-phase semantics, default 3 rationale, quality caveat, one-turn escalation (doc-14), and one-turn cache break on the aging turn.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
# TASK-17 Implementation Plan — Opt-in image-history stripping for non-vision candidates (REVISED: turn-based TTL)

NOTE: This plan supersedes the original approved plan (message-count window) — corrected during branch review to turn-based TTL semantics. Implemented and verified 2026-09-04; see final summary + notes.

## Approach (as implemented)

Two coupled changes (decision doc-13):

1. **TTL-scoped vision requirement** in `_detect_required_capabilities`: `vision` is required iff the latest user message has an image OR any image-bearing message is within `image_ttl_turns` user turns of the latest user message (user-turn ordinals count user-role messages only; assistant/tool turns don't advance aging). Fail-closed: images whose ordinal cannot be computed (non-user messages, or bodies with no user message) always require `vision`.
2. **Per-candidate stripping** in `_prepare_body_for_candidate` (`_strip_image_history_for_candidate`, after reasoning-content sanitize, before content-part normalization and cache_control injection): for candidates lacking `vision` (best-score match over model_rules, fail-closed: no rule = non-vision), every AGED-OUT history image part — and every unorderable image part — is replaced with the deterministic placeholder (empty = drop). In-TTL images are retained: routing itself keeps non-vision candidates out of such requests. Non-vision candidates never receive image parts through this path.

Last-context cache interaction (decision doc-14): keep inflated cached estimate on stripped turns; no invalidation; one escalated turn, self-heals next turn.

Cache interaction: placeholder is deterministic → stripped prefix byte-stable after one transition write on the aging turn.

## User-facing config

```yaml
smart_proxy:
  image_history_stripping:
    enabled: true                   # default false; absent block = off (bit-identical pre-feature behavior)
    image_ttl_turns: 3              # user turns an image stays vision-relevant (gt=0); send turn + 2 follow-ups
    placeholder: "[image omitted]"  # empty string "" drops the part silently
```

## Files modified

- src/optiproxai/config.py — ImageHistoryStrippingConfig on SmartProxyConfig
- src/optiproxai/proxy.py — _user_turn_ordinal, _detect_required_capabilities TTL+fail-closed, _get_model_vision_capability, _strip_image_history_for_candidate, prep-chain wiring, both detection call sites
- tests/test_proxy_reload.py — TestImageHistoryStripping (19 tests)
- README.md, config.example.yaml — documented

## Session-sticky / tier changes

Session-sticky selection is a stateless per-request hash over the current filtered candidate list; tier changes re-run selection correctly. No handling needed.

## Validation notes

- Disabled policy = bit-identical current behavior.
- Strip before normalization so placeholders survive content_part_policy; markers injected last.
- Fallbacks: non-vision fallbacks only eligible once vision not required; bodies guaranteed image-free through sanitizer.
- Non-goal: no changes to router.py, scorer.py, dashboard, or CLI (router union-vs-best-score divergence filed as TASK-22).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-04 (branch review + revision): v1 semantics corrected to turn-based TTL after user review. v1 flaw: keep_recent_images message-window never aged out a single-image session (the primary use case), and detection softening dropped vision immediately while non-vision candidates still received the kept image (hy3@novita 400 risk — verified empirically). Revision approved via Plannotator: field renamed image_ttl_turns (default 3), vision required iff latest-user-message image OR image within TTL, non-vision candidates receive ZERO image parts, placeholder deterministic (one-turn cache break on aging turn then byte-stable). doc-13 amended with full rationale. 18 tests in TestImageHistoryStripping, all passing. CI gate re-run below.

2026-09-04 (doc-15 review actions): (1) Aging now counts USER turns only — new _user_turn_ordinal helper; assistant/tool messages between user turns no longer advance aging (matches docs prose; doc-13 formula updated with amendment note). (2) Strengthened tests: overbroad str(prepared) substring assertions replaced with prepared == body / exact content-list equality; added test_detection_ttl_counts_user_turns_not_messages (interleaved assistant turns don't advance aging) and zero-image-part sweep assertion in all-aged test. (3) Fixed misleading comment. (4) Prose updated in README/config.example/config field description/doc-13. (5) Created TASK-22 (low) for router union-vs-best-score reconciliation — no live exposure in production config, deferred as behavior change. CI gate: 422 tests passed, ruff/pyright/format clean.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Implemented opt-in image-history stripping for non-vision candidates (decisions doc-13/doc-14). Config: ImageHistoryStrippingConfig (enabled=false default, keep_recent_images=1 gt=0, placeholder='[image omitted]') on SmartProxyConfig as image_history_stripping (None = off); documented in config.example.yaml. Proxy: _detect_required_capabilities gained keyword-only image_stripping_enabled — history-only images (not the latest user message) no longer require 'vision' when the policy is enabled, at both call sites; new _get_model_vision_capability (prefix/provider best-score match, fail-closed: no rule = non-vision); new _strip_image_history_for_candidate runs in _prepare_body_for_candidate between reasoning-content sanitize and content-part normalization — strips image_url parts from history beyond the keep_recent_images window for candidates lacking vision, replacing with placeholder text (empty = drop), logging IMAGE_HISTORY_STRIPPED; vision-capable candidates unchanged; fallbacks covered per-candidate. Last-context cache untouched (doc-14: keep inflated estimate one turn). Tests: 15 new tests in TestImageHistoryStripping (detection on/off/latest-message-hard, strip on/off, fail-closed, keep window, empty placeholder, provider-rule precedence, normalization survival, log emission, config validation). CI gate: ruff clean, format clean, pyright 0 errors, 418 tests passed, uv build OK. Branch task/TASK-17, commit pending user (manual commit/push per permission rules).
<!-- SECTION:FINAL_SUMMARY:END -->
