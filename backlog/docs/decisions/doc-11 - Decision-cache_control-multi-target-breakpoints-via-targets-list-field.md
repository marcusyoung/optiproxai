---
id: doc-11
title: 'Decision: cache_control multi-target breakpoints via targets list field'
type: other
created_date: '2026-09-02 16:39'
---
# Decision: cache_control multi-target breakpoints via `targets` list field

**Date:** 2026-09-02
**Status:** Decided
**Task:** TASK-21
**Author:** build (optiproxai/code)

## Context
TASK-21 extends TASK-14's cache_control injection to cover the multi-turn conversation (the ~130k-token bulk of production calls). TASK-14's `CacheControlConfig.target` is a single `Literal["system", "tools"]`, so at most one breakpoint lands per request. TASK-21 needs up to three breakpoints (tools + system + last message) per the diagnosis handoff (2026-09-02), while `max_breakpoints: 4` headroom was already reserved.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| New optional `targets: list[Literal["system", "tools", "last_message"]]` field (chosen) | Backward compatible: `target` keeps working for existing configs; expresses any combination of breakpoints; pyright-friendly; default derived from `target` when `targets` is unset | Two fields for one concept; needs a precedence rule |
| Change `target` to accept a list (`list[Literal[...]]`) | Single field | Breaking change to every existing config using `target: system` / `target: tools` (Pydantic coercion of a string into a list of enums fails for `Literal` unions); contradicts doc-7's frozen shape |
| Keep single `target` and add boolean flags (`cache_tools`, `cache_system`, `cache_last_message`) | Flat | Three interacting flags with unclear precedence; diverges from Doubleword's breakpoint mental model |

## Decision
1. Add `targets: list[Literal["system", "tools", "last_message"]] | None = None` to `CacheControlConfig`. When `None`, effective targets are `[target]` (existing single-target behavior, unchanged).
2. When both `target` and `targets` are set, `targets` wins; the effective target list is `targets` as given. No merge, no error — `targets` is strictly the higher-fidelity form (presence-based, consistent with doc-7).
3. `target` keeps `Literal["system", "tools", "last_message"]` (extended with `last_message`) so single-target configs can also use the conversation breakpoint without the list form.
4. `_apply_cache_control` applies effective targets in canonical request order — tools, system, last_message — skipping any target whose container is missing/empty, and stops once `max_breakpoints` markers have been injected. Existing body-wide client-marker suppression is unchanged.
5. `last_message` marks the last content block of the final message (string content converted to array form, same as the `system` path).
6. No router.py / ModelEntry changes; resolution stays ModelRuleEntry → ProviderConfig → none (doc-7 unchanged).

## Rationale
A list field is the minimal non-breaking expression of "multiple breakpoints", which the TASK-14 plan already anticipated via `max_breakpoints: 4`. Keeping `target` avoids breaking the production config and TASK-14's documented semantics; deriving defaults from it means zero config changes for existing Doubleword setups (behavior identical to today until `targets` or `target: last_message` is adopted).

## Consequences
- Existing configs with `target: system` behave identically after upgrade (single-target derivation).
- The DeepSeek-V4-Pro conversation-caching configuration uses the list form: `targets: [tools, system, last_message]`.
- The injection log line must report the effective target list, not the scalar `target`.
- A future fifth target kind (e.g. per-N-messages) extends the `Literal` union only.
