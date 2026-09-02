---
id: TASK-21
title: >-
  Conversation (last-message) cache_control breakpoint for multi-turn prompt
  caching (Doubleword)
status: Done
assignee: []
created_date: '2026-09-02 16:38'
updated_date: '2026-09-02 17:04'
labels:
  - caching
milestone: m-0
dependencies: []
references:
  - 'https://docs.doubleword.ai/inference-api/prompt-caching'
  - >-
    backlog/tasks/task-14 -
    Inject-cache_control-markers-into-stable-prefix-for-opt-in-caching-providers-Doubleword.md
documentation:
  - >-
    decisions/doc-7 -
    Decision-cache_control-resolves-highest-precedence-wins-no-ModelEntry-propagation.md
  - >-
    decisions/doc-11 -
    Decision-cache_control-multi-target-breakpoints-via-targets-list-field.md
priority: high
type: feature
ordinal: 18100
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Follow-up to TASK-14 (cache_control marker injection, PR #7). Production diagnosis (2026-09-02) showed that `target: system` only caches tools + system prompt (~2k tokens), while real opencode coding sessions send ~140k-token multi-turn conversations that stay uncached — per-call cost pinned at ~$0.12 on DeepSeek-V4-Pro routes. The original TASK-14 plan assumed 'the user turn stays outside the cache', which holds for short prompts but not for append-only multi-turn conversations.

Extend the cache_control mechanism so the growing conversation prefix can be cached: add a conversation/last-message breakpoint target, allow multiple breakpoints per request (Doubleword allows up to 4; config max_breakpoints already defaults to 4), and configure a DeepSeek-V4-Pro model rule that marks tools + system + last message.

Constraints carried over from TASK-14 / decision doc-7:
- Resolution stays presence-based highest-precedence-wins: ModelRuleEntry.cache_control → ProviderConfig.cache_control → none. No ModelEntry level, no router.py changes.
- Injection runs last in _prepare_body_for_candidate (after sanitize/normalize/extra_body/async_mode) so normalization cannot strip markers.
- Skip injection entirely if the client already sent any cache_control marker (body-wide check).
- Never mutate the caller's body in place.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 CacheControlConfig.target accepts a conversation/last-message target in addition to system and tools (config.py)
- [x] #2 A conversation target places a cache_control marker on the final message of each request so the multi-turn conversation prefix is cached on the next turn
- [x] #3 A model_rules override for deepseek-ai/DeepSeek-V4-Pro (doubleword provider) resolves and applies tools + system + last-message breakpoints (multiple markers per request)
- [x] #4 max_breakpoints is enforced when multiple targets are active (no more than max_breakpoints markers injected per request)
- [x] #5 Client-provided cache_control markers anywhere in the body still suppress injection entirely (existing body-wide check preserved)
- [x] #6 No injection when the body has no messages to mark for the configured target (no system message / no tools / no messages)
- [x] #7 Original request body is never mutated in place; only shallow copies of mutated containers are returned
- [x] #8 config.example.yaml and README cache_control documentation updated with the new target and the DeepSeek-V4-Pro rule example
- [x] #9 New tests cover: last-message marker on string and array content, multi-target breakpoints (tools+system+last message), max_breakpoints truncation, client-marker suppression with multi-target config, no-messages skip, and rule>provider precedence for the new target
- [x] #10 uv run ruff check src/ passes
- [x] #11 uv run ruff format --check src/ tests/ passes
- [x] #12 uv run pyright src/ passes
- [x] #13 uv run pytest tests/ -q passes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
# Implementation Plan — TASK-21 conversation (last-message) cache_control breakpoint

## Approach
Extend the TASK-14 cache_control mechanism with a conversation breakpoint and multi-breakpoint support, per decision doc-11. Design: add `targets: list[Literal["system", "tools", "last_message"]] | None = None` to `CacheControlConfig` and extend `target`'s Literal with `"last_message"`. Effective targets = `targets` when set, else `[target]`. `_apply_cache_control` rewrites to apply effective targets in canonical request order (tools → system → last_message), skipping missing containers, stopping at `max_breakpoints`, and returning a body copy only when at least one marker was injected (skip paths keep returning the original body — identity semantics preserved).

Single-marker injection per target reuses the existing string→array conversion and last-block marker logic; the `last_message` path is the same shape as the `system` path but selects `messages[-1]` instead of the first system message. Resolution (`_resolve_cache_control`), wiring in `_prepare_body_for_candidate` (last step), and the body-wide client-marker suppression are all unchanged.

## Files
- `src/optiproxai/config.py` — extend `CacheControlConfig`: widen `target` Literal with `"last_message"`, add `targets` list field, docstring update.
- `src/optiproxai/proxy.py` — rewrite `_apply_cache_control` into a multi-target applier (per-target helpers for system/tools/last_message); update the CACHE_CONTROL log line in `_prepare_body_for_candidate` to report the effective target list.
- `tests/test_proxy_reload.py` — extend `TestCacheControlInjection` with the new scenarios below.
- `config.example.yaml` + `README.md` — document `targets`, `target: last_message`, and the DeepSeek-V4-Pro rule example.
- Production config note (NOT in repo): user's `~/.config/optiproxai/config.yaml` gets a DeepSeek-V4-Pro rule override after merge — separate manual step, documented in the task's final summary.

## Config shape after change
```yaml
model_rules:
  - prefix: "deepseek-ai/DeepSeek-V4-Pro"
    provider: "doubleword"
    cache_control:
      enabled: true
      ttl: "1h"
      targets: [tools, system, last_message]   # up to 4 breakpoints; max_breakpoints defaults to 4
```
Provider-level block stays as-is (`target: system`), so non-DeepSeek Doubleword models keep today's behavior.

## Injection order and semantics
Canonical request order is tools → system message → conversation messages. Breakpoints are cumulative prefix markers: tools caches tool defs, system caches tools+system, last_message caches everything up to the final message. Applying in canonical order keeps marker placement deterministic and matches how Anthropic-style prefix caching bills. `last_message` on an empty `messages` array or absent messages → skip that target. `max_breakpoints` checked per injected marker (counter, not pre-check), so 3 targets with max_breakpoints 2 injects tools + system only.

## Steps
1. Config model changes in `config.py` (target Literal + targets field + docstring).
2. `_apply_cache_control` multi-target rewrite in `proxy.py` + log line update in `_prepare_body_for_candidate`.
3. Tests: extend `TestCacheControlInjection` with: last-message marker on string content; last-message marker on array content; multi-target tools+system+last_message (3 markers); max_breakpoints truncation (targets [tools, system, last_message] with max_breakpoints=2 → 2 markers); client-marker suppression with multi-target config (no injection); empty messages skip for last_message; rule>provider precedence with `targets`; single-target derivation (`targets` unset → behaves exactly as `target`, incl. existing 13 tests passing unchanged); no in-place mutation for the multi-target path.
4. Docs: config.example.yaml (targets example + DeepSeek-V4-Pro rule), README cache_control section.
5. Validate: ruff check, ruff format --check, pyright, targeted tests (`uv run pytest tests/test_proxy_reload.py -q -k cache_control`), full suite.

## Risks / notes
- `targets` with duplicates (e.g. [system, system]) — dedupe while preserving first occurrence; harmless otherwise.
- The `last_message` marker moves turn to turn; Doubleword's prefix cache invalidates from the marked block forward, which is exactly the desired semantics — prior conversation turns stay cached, only the new tail re-bills.
- Behavior for existing configs is bit-identical (single-target derivation); the 13 existing tests must pass unmodified.

## Task Manifest

| # | Title | Files | Depends On | Labels | Acceptance Criterion |
|---|---|---|---|---|---|
| 1 | Extend CacheControlConfig with last_message target and targets list | src/optiproxai/config.py | — | logic | CacheControlConfig accepts target: last_message and an optional targets list; single-target derivation returns [target] when targets is None; existing configs validate unchanged |
| 2 | Rewrite _apply_cache_control for multi-target injection | src/optiproxai/proxy.py | 1 | logic | Multi-target config injects markers in canonical order (tools, system, last_message), respects max_breakpoints, skips missing containers, never mutates the caller's body; log line reports effective targets |
| 3 | Extend TestCacheControlInjection for new scenarios | tests/test_proxy_reload.py | 2 | test | New tests cover last-message string/array content, multi-target breakpoints, max_breakpoints truncation, client-marker suppression, empty-messages skip, rule>provider precedence; full suite passes |
| 4 | Document targets and DeepSeek-V4-Pro rule example | config.example.yaml, README.md | 1 | docs | config.example.yaml and README show targets list form and a DeepSeek-V4-Pro rule with targets [tools, system, last_message] |
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implementation on branch task/TASK-21 (2026-09-02). One production bug found and fixed during development: the multi-target accumulator in _apply_cache_control was seeded with messages=None instead of body's messages, so the first target always skipped; caught by 4 failing TASK-14 tests, fixed by seeding from body.get(messages).

Test-authoring corrections (test bugs, not production bugs): truncation test asserted .get() on string content (last message untouched when budget exhausted); dedupe test body lacked a system message so only 1 marker was possible. Both fixed.

Full CI gate: ruff check clean, ruff format 38 files already formatted, pyright 0 errors, pytest 402 passed (22 in TestCacheControlInjection: 13 original unchanged + 9 new), uv build succeeded.

git commit/push blocked by local permission rules — user commits manually.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Implemented the conversation (last_message) cache_control breakpoint (TASK-21) on branch task/TASK-21.

What changed:
- config.py: CacheControlConfig.target extended with "last_message"; new optional `targets: list[Literal["system", "tools", "last_message"]]] | None` field (doc-11). targets wins when set; unset targets derives [target] so existing configs are bit-identical.
- proxy.py: _apply_cache_control rewritten as a multi-target applier — per-target helpers (_mark_content_last_block, _inject_target_marker), effective targets resolved by _effective_cache_targets (canonical order tools -> system -> last_message, duplicates deduped), max_breakpoints enforced per injected marker, body-wide client-marker suppression unchanged, skip paths return original body. CACHE_CONTROL log line reports the effective target list.
- tests: 9 new tests in TestCacheControlInjection (last-message string/array content, 3-marker multi-target, max_breakpoints truncation, client-marker suppression with multi-target config, no-messages skip, rule>provider precedence with targets, duplicate dedupe, no in-place mutation). 13 original tests pass unchanged. Full suite 402 passed.
- docs: config.example.yaml (target enum comment + DeepSeek-V4-Pro multi-target rule example) and README (new "Multi-turn conversations (targets)" subsection, updated target table and injection rules).

Deviations: none from the approved plan. One production bug found and fixed during dev (accumulator seeded with None instead of body's messages — first target always skipped); two test-authoring bugs corrected.

Post-merge follow-up (outside repo): add the DeepSeek-V4-Pro rule override to ~/.config/optiproxai/config.yaml (targets: [tools, system, last_message]) and verify conversation tokens are discounted via usage.cache_read_input_tokens. Commit/push of task/TASK-21 blocked by local permission rules — user commits manually.
<!-- SECTION:FINAL_SUMMARY:END -->
