---
id: TASK-17
title: Opt-in image-history stripping for non-vision candidates (config policy)
status: Done
assignee: []
created_date: '2026-08-28 15:24'
updated_date: '2026-09-04 13:45'
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
# TASK-17 Implementation Plan — Opt-in image-history stripping for non-vision candidates

## Approach

Two coupled changes (decision doc-13):

1. **Vision-scope softening** in `_detect_required_capabilities` (proxy.py ~433): images in the latest user message are a hard `vision` requirement; images only in older history are soft when the policy is enabled — `vision` is then not added to `required_capabilities`, so the router's existing capability filter stops pinning the session to vision models. Policy disabled (default) = bit-identical current behavior.
2. **Per-candidate stripping** in the `_prepare_body_for_candidate` chain (proxy.py ~1746): new `_strip_image_history_for_candidate` runs after reasoning-content sanitize, before content-part normalization and cache_control injection. Applies iff policy enabled AND candidate lacks `vision` capability. Fallbacks covered automatically (per-candidate dispatch at ~967 and ~1045).

Last-context cache interaction (decision doc-14): keep the inflated cached estimate on stripped turns; no cache invalidation. One escalated turn, self-healing on the next provider-reported prompt_tokens.

## User-facing config options

All under `smart_proxy:` in the user config file:

```yaml
smart_proxy:
  image_history_stripping:
    enabled: true                # default false — opt-in; false/absent = current behavior
    keep_recent_images: 1        # keep the N most recent image-bearing user messages
                                 # in history; strip image parts from older ones (gt=0)
    placeholder: "[image omitted]"  # text replacing a stripped image part;
                                    # empty string "" drops the part silently
```

- `enabled: false` or the key omitted entirely → no detection softening, no stripping (bit-identical to current behavior).
- `keep_recent_images` — how many of the most recent image-bearing user messages survive in history. The latest user message is always protected regardless of this value.
- `placeholder` — text sent to the model in place of a stripped image. Empty string = silently remove the part.
- No per-provider or per-model config: stripping is driven purely by the candidate's declared `vision` capability in `model_rules` (fail-closed: models with no matching rule are treated as non-vision and get stripped bodies), so no new rule fields.

## Files to modify

- `src/optiproxai/config.py` — new `ImageHistoryStrippingConfig` BaseModel (enabled: bool = False; keep_recent_images: int = Field(default=1, gt=0); placeholder: str = "[image omitted]"); field `image_history_stripping: ImageHistoryStrippingConfig | None = None` on `SmartProxyConfig` (None = off, preserves config compat; presence = opt-in). Update `config.example.yaml`.
- `src/optiproxai/proxy.py`:
  - `_detect_required_capabilities(body, tools_policy, *, image_stripping_enabled: bool = False)` — new keyword-only param (default False keeps all existing callers/tests green); move image detection into a helper that classifies each `image_url` block as latest-user-message vs history. Call site at ~1954 passes `image_stripping_enabled=bool(state.config.smart_proxy.image_history_stripping and ...enabled)`.
  - New `_get_model_vision_capability(model, provider_name, runtime) -> bool` — prefix/provider matched over model_rules (same best-score pattern as `_get_model_content_part_policy`); returns True when a matching rule declares `vision` in capabilities. Fail-closed: no matching rule → False (non-vision, strippable), per user decision 2026-09-04.
  - New `_strip_image_history_for_candidate(body, model, provider_name, runtime) -> dict` — walks messages EXCLUDING the latest user message; tracks image-bearing user message indexes from the end; keeps the most recent `keep_recent_images`; replaces older `image_url` parts with `{"type": "text", "text": placeholder}` (empty placeholder → drop part); logs `IMAGE_HISTORY_STRIPPED` INFO.
  - `_prepare_body_for_candidate` — insert strip step between `_sanitize_reasoning_content_for_candidate` and `_normalize_message_content_for_candidate`.
- `tests/test_proxy_reload.py` — new `TestImageHistoryStripping` class (see tests below).
- `README.md` — Capability-aware routing section: new "Image-history stripping" subsection documenting config, semantics, quality caveat, one-turn escalation consequence; references doc-12/13/14.
- `config.example.yaml` — documented example block under `smart_proxy:`.

## Sub-steps (ordered)

1. Config model + validation + example yaml (no behavior change without enablement).
2. `_detect_required_capabilities` softening + call-site update.
3. `_get_model_vision_capability` helper (fail-closed default).
4. `_strip_image_history_for_candidate` sanitizer + `_prepare_body_for_candidate` wiring.
5. Tests.
6. README docs.

## Session-sticky behavior across tier changes (verified in router.py, answered for user)

Session-sticky selection is a deterministic hash of the session key over the *current* filtered candidate list (router.py `_select_primary_candidate`, ~826-860) — there is no persisted session→model state. On a tier change (scorer moves the session to another tier, or input-limit escalation kicks in), selection simply re-runs over the new tier's candidate list:

- Same vision model present in both tiers (common here: syn:large:vision appears in multiple tiers): the hash may land on it again — accepted by user ("if that was the selected tier model then no problem").
- Different candidate sets: a new model is selected; if it lacks `vision`, stripping applies and the request works correctly.

No additional handling needed; behavior is correct by construction. doc-13 known limitation updated accordingly.

## Constraints / risks

- Ordering matters: strip BEFORE normalization so `content_part_policy` cannot resurrect/duplicate placeholder text, and before cache_control so markers land on the final body (README 476 invariant preserved).
- Fallback bodies are deep-copied from the ORIGINAL body (`fallback_body_base`), so stripping must be idempotent and re-applied per candidate — it is, being in `_prepare_body_for_candidate`.
- Byte-stability: stripping happens every turn for non-vision candidates while images remain beyond the window, so the stripped prefix IS stable turn-over-turn (placeholder text is deterministic) — provider caches see a stable byte prefix after the first stripped turn.
- Session-sticky primary may keep the session on the vision model after images age out (doc-13 known limitation, accepted for v1 by user).
- Non-goal: no changes to router.py, scorer.py, dashboard, or CLI.

## Tests (TestImageHistoryStripping)

- detection: images only in history + policy enabled → no `vision` in required; latest-message image → `vision` required regardless; policy disabled → current behavior.
- strip: non-vision candidate gets placeholder for images beyond window; latest user message never touched; `keep_recent_images=2` keeps two most recent image-bearing messages; empty placeholder drops parts; model with no matching rule is treated as non-vision (fail-closed).
- per-candidate: vision-capable candidate body unchanged for same request; non-vision fallback of vision primary gets stripped body.
- ordering: placeholder text survives content_part_policy normalization; cache_control injection unaffected (existing TestCacheControlInjection suite must stay green).
- logging: IMAGE_HISTORY_STRIPPED emitted with counts.
- config: gt=0 validation on keep_recent_images; disabled-by-default no-op.

## Task Manifest

| # | Title | Files | Depends On | Labels | Acceptance Criterion |
|---|---|---|---|---|---|
| 1 | Add ImageHistoryStrippingConfig model and config plumbing | src/optiproxai/config.py, config.example.yaml | — | logic, config | OptiproxaiConfig accepts smart_proxy.image_history_stripping with enabled/keep_recent_images(gt=0)/placeholder defaults and validation rejects keep_recent_images=0 |
| 2 | Soften vision detection for history-only images | src/optiproxai/proxy.py | 1 | logic | With policy enabled, history-only images do not add vision to required capabilities; latest-message images always do; disabled policy is bit-identical to current behavior |
| 3 | Add per-candidate image-history sanitizer in prep chain | src/optiproxai/proxy.py | 1 | logic | Non-vision candidates receive history-stripped bodies (placeholder applied, latest user message untouched, keep_recent_images window honored, fail-closed for undeclared models); vision-capable candidates unchanged; runs before normalization and cache_control |
| 4 | Add TestImageHistoryStripping suite | tests/test_proxy_reload.py | 2, 3 | test | All new tests pass and full existing suite (incl. TestCacheControlInjection) stays green |
| 5 | Document image-history stripping | README.md | 1, 2, 3 | docs, style | README Capability-aware routing section documents config options, semantics, quality caveat, and one-turn escalation consequence; ruff and pyright clean |
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
