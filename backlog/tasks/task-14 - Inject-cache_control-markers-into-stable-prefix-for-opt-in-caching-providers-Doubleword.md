---
id: TASK-14
title: >-
  Inject cache_control markers into stable prefix for opt-in caching providers
  (Doubleword)
status: Done
assignee: []
created_date: '2026-08-19 14:55'
updated_date: '2026-08-19 17:11'
labels: []
dependencies: []
references:
  - 'https://docs.doubleword.ai/inference-api/prompt-caching'
documentation:
  - >-
    decisions/doc-7 -
    Decision-cache_control-resolves-highest-precedence-wins-no-ModelEntry-propagation.md
priority: high
type: feature
ordinal: 18000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Inject `cache_control` markers into the stable prefix (system prompt) on routes to providers that require opt-in prompt caching, so cache discounts apply in real opencode traffic without client-side marker support.

## Background (2026-08-19 testing)
Doubleword's prompt caching is opt-in and marker-driven: a request without `cache_control` reads nothing from cache, even if an identical prefix is already cached. opencode does not send `cache_control` markers (treats optiproxai as a generic OpenAI-compatible endpoint), and optiproxai does not inject them. This means Doubleword routes (e.g. `moonshotai/kimi-k3` at $3.00/M input vs $0.30/M cache read — 10x discount) pay full input price on every call in production, despite the discount being available.

By contrast, Requesty auto-caches with no marker needed and already delivers an 85% steady-state cost drop through optiproxai with no changes.

## What exists today
- `extra_body` on `ModelRuleEntry` does a top-level merge into the request body — cannot place `cache_control` inside `messages[].content[].cache_control` or on `tools[].cache_control` where Doubleword requires it.
- `_prepare_body_for_candidate` (proxy.py ~line 1447) already mutates the body (sanitize → normalize → extra_body merge → async_mode). Cache marker injection is the same pattern.
- `_optiproxai_headers` and reasoning-control injection (`_apply_reasoning_for_style`) are existing per-provider/per-model injection patterns to follow.

## Design
### Config model
Add a `cache_control` config block to `ProviderConfig` (and optionally `ModelRuleEntry` for per-model override):

```yaml
providers:
  doubleword:
    cache_control:
      enabled: true
      ttl: "1h"          # "5m" (default) or "1h"
      target: "system"    # "system" (inject on last system content block) or "tools" (inject on last tool object)
```

Resolution order (mirror async_mode/reasoning_style): ModelRuleEntry.cache_control → ProviderConfig.cache_control → none.

### Injection logic
In `_prepare_body_for_candidate` (or a new `_apply_cache_control` helper called there), after sanitize/normalize and before sending upstream:

1. Resolve the effective `cache_control` config for the (model, provider) pair.
2. If `enabled` and `target: "system"`:
   - Find the last content block in the first system message.
   - If system message content is a string, convert to `[{"type":"text","text":<original>}]` and add `cache_control`.
   - If it's already an array, add `cache_control` to the last block.
   - If no system message exists, skip (no prefix to cache).
3. If `enabled` and `target: "tools"`:
   - Add `cache_control` to the last object in the `tools` array.
4. Respect the 4-breakpoint limit (Doubleword allows up to 4 markers per request).
5. Do not inject if the client already sent `cache_control` markers (preserve explicit client controls — same principle as reasoning control, proxy.py line 1752).

### Important: content_part_policy interaction
If `content_part_policy.mode: "normalize"` is set for a model, `_normalize_content_part` reconstructs text parts as `{"type":"text","text":...}` and drops `cache_control`. Injection must happen **after** normalization to survive. Current ordering in `_prepare_body_for_candidate`: sanitize → normalize → extra_body → async_mode. Cache injection should be last (after async_mode) so it's not stripped.

## Acceptance Criteria
- [ ] `cache_control` config block on ProviderConfig with enabled/ttl/target fields
- [ ] Optional `cache_control` override on ModelRuleEntry (same fields)
- [ ] `cache_control` is injected into the system message content (last block) or tools array (last object) per config
- [ ] String system content is converted to array form before injection
- [ ] Client-provided `cache_control` markers are preserved (no double-injection)
- [ ] Injection happens after content_part_policy normalization so markers survive
- [ ] 4-breakpoint limit respected (skip injection if 4 markers already present)
- [ ] No injection when no system message exists (target: system) or no tools array (target: tools)
- [ ] Config example in config.example.yaml updated with doubleword cache_control example
- [ ] uv run ruff check src/ passes
- [ ] uv run ruff format --check src/ tests/ passes
- [ ] uv run pyright src/ passes
- [ ] uv run pytest tests/ -q passes
- [ ] Tests: injection on string system content, array system content, tools target, no system message, client already has markers, content_part_policy normalize interaction, 4-breakpoint limit

## References
- `src/optiproxai/config.py` ProviderConfig (line 139), ModelRuleEntry (line 381), ContentPartPolicy (line 67)
- `src/optiproxai/proxy.py` `_prepare_body_for_candidate` (line 1447), `_normalize_message_content_for_candidate` (line 1392), `_normalize_content_part` (line 1366), `_apply_reasoning_for_style` (existing injection pattern), `_optiproxai_headers` (line 467)
- Doubleword prompt caching docs: https://docs.doubleword.ai/inference-api/prompt-caching
- Related backlog: TASK-12 (dashboard cache metrics — depends on this for real traffic data), TASK-13 (response headers for cache tokens)
- Testing context: 2026-08-19 provider cache tests confirmed Doubleword caches 2005 tokens with marker, 0 without
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 `cache_control` config block on ProviderConfig with enabled/ttl/target fields
- [x] #2 Optional `cache_control` override on ModelRuleEntry (same fields)
- [x] #3 `cache_control` injected into system message content (last block) or tools array (last object) per config target
- [x] #4 String system content converted to array form before injection
- [x] #5 Client-provided `cache_control` markers preserved (no double-injection, body-wide check)
- [x] #6 Injection happens after content_part_policy normalization so markers survive
- [x] #7 4-breakpoint limit respected (skip injection when limit already reached)
- [x] #8 No injection when no system message exists (target: system) or no tools array (target: tools)
- [x] #9 Config example in config.example.yaml updated with doubleword cache_control example
- [x] #10 uv run ruff check src/ passes
- [x] #11 uv run ruff format --check src/ tests/ passes
- [x] #12 uv run pyright src/ passes
- [x] #13 uv run pytest tests/ -q passes
- [x] #14 Tests: injection on string system content, array system content, tools target, no system message, client already has markers, content_part_policy normalize interaction, 4-breakpoint limit
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
# Implementation Plan — TASK-14 cache_control injection (approved 2026-08-19)

## Approach
Mirror the existing per-candidate config resolution pattern (`_resolve_async_mode` / `_get_model_content_part_policy`) and inject `cache_control` markers last in `_prepare_body_for_candidate` so content-part normalization cannot strip them. Resolution order: best-matching `ModelRuleEntry.cache_control` → `ProviderConfig.cache_control` → none. No `ModelEntry` level, so no router.py/RoutingDecision/FallbackEntry changes (decision doc-7).

## What the marker does
Doubleword follows Anthropic's prefix-cache model: a `cache_control` marker caches everything from the start of the request up to and including the marked block. The user turn stays outside the cache. One marker per request is enough to discount the whole stable prefix.

## Why target matters — what gets cached (request order: tools → system message → user messages)

| target | Marker lands on | Cached prefix | Discounted tokens |
|---|---|---|---|
| system (default) | last block of first system message | tools + system prompt | largest stable prefix — system prompt identical every call → biggest savings |
| tools | last object in tools array | tool definitions only | small — system prompt still pays full input price; useful only when system prompt varies but tools are stable |

## Config Shape
```yaml
providers:
  doubleword:
    cache_control:
      enabled: true
      ttl: "1h"            # 5m (default) | 1h
      target: system       # system (last system content block) | tools (last tool object)
      max_breakpoints: 4   # max markers per request (Doubleword ceiling is 4); default 4
```

## Injection Rules
- `target: system`: find first system message; string content → convert to `[{"type":"text","text":...,"cache_control":{"type":"ephemeral","ttl":...}}]`; array content → marker on last block; no system message → skip.
- `target: tools`: marker on last tool object; no/empty tools → skip.
- Client-provided markers preserved: never double-inject.
- Breakpoint limit: skip if body already has ≥ `max_breakpoints` `cache_control` occurrences (config field, default 4).
- Runs after content_part_policy normalize (survives reconstruction).
- Always shallow-copy mutated containers; never mutate caller's body in place.

## Steps
1. Config model: `CacheControlConfig` (enabled/ttl/target/max_breakpoints) + fields on `ProviderConfig`, `ModelRuleEntry`.
2. `_resolve_cache_control` + `_apply_cache_control` in proxy.py; wire into `_prepare_body_for_candidate` (last step).
3. Tests: 11 scenarios (string/array system, tools, no system/no tools, client markers, breakpoint limit, normalize interaction, disabled, rule>provider precedence).
4. Docs: config.example.yaml example + README section.
5. Validate: ruff check, ruff format --check, pyright, targeted then full pytest.

## Risks
- String system content becomes array form upstream (Doubleword is OpenAI-compatible — verified in testing; only affected providers opt in via config).
- `content_part_policy.mode: normalize` with drop_types/unknown: drop can delete content parts during normalization. If it empties the system message, the marker lands on an empty block (harmless, but no caching benefit). Normalization runs before injection, so the marker itself is never stripped. Per-model, user-controlled — no TASK-14 change.
- Breakpoint ceiling is Doubleword-specific; config is provider-scoped and tunable (max_breakpoints), so other providers unaffected.

## Scope
Single-task scope — no backlog subtasks (SDD Phase 2 skipped per "plan fits in a single task"). The five steps are tracked as todos during implementation.

PR review fix (review #7): skip injection when ANY cache_control marker exists anywhere in the body (body-wide check, not just landing block) — preserves client caching boundaries; matches _has_explicit_reasoning_control pattern.

PR review fix (review #7): _apply_cache_control docstring documents identity semantics — skip paths return the original body unchanged; only the injection path returns a shallow copy.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Affected models in current config (2026-08-19)

All Doubleword models in `~/.config/optiproxai/config.yaml` and their cache pricing (input / cache read per 1M tokens):

| Config model | Tier | Async? | Input | Cache read | Discount | Status |
|---|---|---|---|---|---|---|
| `moonshotai/kimi-k3` | REASONING fallback (both profiles) | yes (flex) | $2.15/M | $0.22/M | 90% | **Wasting full input price every call** |
| `deepseek-ai/DeepSeek-V4-Pro` | chat COMPLEX fallback | yes (flex) | $0.98/M | $0.10/M | 90% | **Wasting full input price every call** |
| `zai-org/GLM-5.2-FP8` | code COMPLEX fallback | yes (flex) | $0.70/M | $0.14/M | 80% | **Wasting full input price every call** |
| `tencent/Hy3-FP8` | chat COMPLEX primary | no (sync) | $0.14/M | $0.14/M | 0% | **No cache pricing — markers ignored, no harm** |

Provider-level `cache_control` injection (TASK-14 design) fixes all three caching-capable models with one config flag. Hy3 is unaffected: Doubleword docs confirm markers are silently ignored on models without cache pricing, billed at standard rates — always safe to include.

Prices shown are async (flex) where applicable since all three caching models have `async_mode: enabled: true` in config. Realtime prices are higher but discounts are similar (80-90%).

Production validation (2026-08-19, temp test profile + tier_override through the live proxy, then reverted): kimi-k3 flex cache_read=2005, cache_write=0, prompt=2101; kimi-k3 sync cache_read=2005, cache_write=2005; GLM-5.2-FP8 flex cache_read=1687, cache_write=1687. Cache discounts (90%/80%) confirmed live in both flex and sync.

Direct model-name passthrough bypasses _prepare_body_for_candidate (no injection) — correct; real opencode traffic uses the routing path.

Test count: 13 tests in TestCacheControlInjection (string/array system, tools target, no system/no tools, client marker on landing block, client marker elsewhere, breakpoint limit, normalize interaction, disabled, rule>provider precedence, rule opt-out, no in-place mutation). Full suite 357 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
PR #7 (squash merge ce43e24) — inject cache_control markers into the stable prefix for opt-in caching providers.

What changed:
- Added CacheControlConfig (enabled/ttl/target/max_breakpoints) to ProviderConfig (config.py:191) and optional ModelRuleEntry override (config.py:451). Resolution is presence-based highest-precedence-wins (decision doc-7): best-matching ModelRuleEntry.cache_control → ProviderConfig.cache_control → none. No ModelEntry field, no router.py changes.
- proxy.py: new _resolve_cache_control, _count_cache_control_markers, _apply_cache_control; wired as the final step of _prepare_body_for_candidate (after sanitize/normalize/extra_body/async_mode) so content_part_policy normalization cannot strip markers.
- target system: marker on last content block of the first system message; string content converted to [{type:text,text,cache_control}]. target tools: marker on last tool object. Skips when no system message / no tools array. Skips entirely when ANY client cache_control marker exists in the body (body-wide check, preserves client boundaries). Respects max_breakpoints (default 4). Shallow-copies mutated containers only; original body never mutated.

Docs: config.example.yaml cache_control examples (provider + rule) and README "Prompt caching (cache_control)" section.

Tests: 13 tests in TestCacheControlInjection (tests/test_proxy_reload.py) covering string/array system content, tools target, no system/no tools, client marker on landing block, client marker elsewhere, breakpoint limit, normalize interaction, disabled, rule>provider precedence, rule opt-out, no in-place mutation.

Validation: ruff check, ruff format --check, pyright (0 errors), full suite 357 passed.

Production validation: live proxy test with temp profile + tier_override confirmed 2005-token cache reads on kimi-k3 (90% discount) and 1687 on GLM-5.2-FP8 (80% discount) in flex and sync modes. Permanent cache_control block (enabled true / ttl 1h / target system / max_breakpoints 4) left on doubleword provider only; temp config reverted.

Follow-ups: TASK-12 (dashboard cache metrics), TASK-13 (response headers for cache tokens), monitor Doubleword dashboard for production cache discounts.
<!-- SECTION:FINAL_SUMMARY:END -->
