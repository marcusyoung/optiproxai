---
id: TASK-2
title: 'Per-turn async model preference via /optiproxai:<tier>:async modifier'
status: To Do
assignee: []
created_date: '2026-08-17 17:40'
updated_date: '2026-08-18 22:25'
labels:
  - logic
  - test
  - docs
dependencies:
  - TASK-10
references:
  - src/optiproxai/router.py
  - src/optiproxai/proxy.py
  - src/optiproxai/config.py
  - src/optiproxai/cli.py
priority: medium
type: feature
ordinal: 6000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Extend the /optiproxai:<tier> override token to accept an optional `:async` modifier (e.g. `/optiproxai:complex:async`) that biases model selection within the selected tier toward models served asynchronously via Doubleword (or other providers with `service_tier: flex` or an explicit async flag).

## Context

optiproxai already routes to async-capable Doubleword models (DeepSeek-V4-Pro, GLM-5.2-FP8, Kimi-K3) as fallbacks in normal routing. These models use `service_tier: flex` in `extra_body` to signal async submission. There is currently no way for a client to express a per-turn preference for async delivery.

This feature is a sibling to TASK-1 (per-turn tier override). It extends the same parser introduced by TASK-1.1 (`parse_tier_override`) to capture a second capture group from the token, so the syntax becomes `/optiproxai:<tier>:<modifier>` where `async` is the first supported modifier.

## Design considerations

- **Parser extension**: The regex changes from `^/optiproxai:(\w+)\s*` to `^/optiproxai:(\w+)(?::(\w+))?\s*`. The helper returns an additional value (e.g. a modifier string or a dataclass) so call sites can act on it. TASK-1.1 ships the helper; this task extends its signature. If TASK-1.1 has already been implemented by the time this task starts, the signature change must be backward-compatible or all call sites updated together.

- **Async model identification (resolved by TASK-10)**: Two approaches were discussed:
  1. Infer from config — any model entry whose `extra_body` contains `service_tier: flex` is treated as async. No schema change, but fragile.
  2. Explicit flag — add an `async: true` (or `mode: async`) field to model entries or provider definitions in the config schema. More work but self-documenting and extensible if other modifiers are added later (e.g. `:vision`, `:cheap`).

  **Resolution**: TASK-10 introduces the opt-in model where model/rule-level `async_mode.enabled: true` declares async intent. After TASK-10, the clean way to identify async-capable models is to check whether a model's resolved `async_mode` has `enabled: true` — no `extra_body` sniffing needed, no additional schema field required. Approach 2 (explicit flag) is the natural fit; TASK-10's `async_mode.enabled` IS that flag. The plan phase should use the resolved `async_mode` (post-TASK-10 merge semantics) as the async identification signal.

- **Routing behavior**: When `:async` is set, prefer async-capable models (those with resolved `async_mode.enabled == true`) in the selected tier's primary/fallback list. If no async model exists for that tier, warn and fall through to normal routing (same graceful-degradation pattern as invalid tier in TASK-1, per decision record `decisions/invalid-tier-warn-vs-error`).

- **Scope**: This is a routing-preference signal, not a different response handling path. The proxy still returns a standard OpenAI-compatible streaming or non-streaming response. No protocol change.

## Dependencies

- TASK-10 must be complete — this task relies on TASK-10's opt-in model where `async_mode.enabled` at the model/rule level identifies async-capable models. TASK-10 itself depends on TASK-6 (the original async_mode implementation), so the chain TASK-2 → TASK-10 → TASK-6 is preserved.
- TASK-1 must be complete or at least TASK-1.1 must be complete, because this task extends `parse_tier_override` (introduced by TASK-1.1) and shares the same proxy/CLI call sites.

## References

- `src/optiproxai/router.py` — `parse_tier_override` helper and `Router.route()`
- `src/optiproxai/proxy.py` — `chat_completions()` and `route_debug()`
- `src/optiproxai/config.py` — config schema for model entries and `async_mode` (post-TASK-10 opt-in shape)
- `src/optiproxai/cli.py` — `route` command
- Decision records: `decisions/invalid-tier-warn-vs-error`, `decisions/tier-override-token-position`
- Related task: TASK-10 (async_mode mechanism/opt-in split) — provides the `async_mode.enabled` opt-in flag used for async model identification
- Config: `C:\Users\myoun\.config\optiproxai\config.yaml` — Doubleword model entries with `service_tier: flex`
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A `/optiproxai:<tier>:async` token at the start of the latest user message biases model selection toward async-capable models within the selected tier in the chat completions proxy
- [ ] #2 The `:async` modifier is parsed case-insensitively (e.g. `:async`, `:Async`, `:ASYNC`)
- [ ] #3 The `/optiproxai:<tier>:async` token is stripped from the message before forwarding upstream
- [ ] #4 When no async-capable model exists for the selected tier, routing falls through to normal model selection with a warning logged (graceful degradation)
- [ ] #5 The `:async` modifier is honored by `optiproxai route` CLI and the /v1/route debug endpoint
- [ ] #6 An unknown modifier (e.g. `/optiproxai:complex:foo`) degrades gracefully — invalid modifier is ignored or warned, tier override still applies if the tier is valid
- [ ] #7 A `/optiproxai:<tier>:async` token in assistant/history messages does not trigger the modifier
- [ ] #8 Tests cover: valid async modifier per tier, case-insensitivity, stripping, no-async-model fallback, unknown modifier handling, history-token non-triggering
- [ ] #9 README documents the `:async` modifier alongside the `/optiproxai:<tier>` feature
<!-- AC:END -->
