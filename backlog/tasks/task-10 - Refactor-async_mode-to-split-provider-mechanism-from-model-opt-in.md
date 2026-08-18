---
id: TASK-10
title: Refactor async_mode to split provider mechanism from model opt-in
status: To Do
assignee: []
created_date: '2026-08-18 22:03'
labels:
  - refactor
dependencies:
  - TASK-6
documentation:
  - >-
    decisions/doc-4 -
    Decision-ModelEntry-async_mode-propagates-via-RoutingDecision-and-FallbackEntry.md
  - >-
    decisions/doc-5 -
    Decision-Candidate-preparation-returns-body-plus-upstream-headers-tuple-return.md
priority: medium
type: enhancement
ordinal: 14000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Problem

TASK-6 shipped `async_mode` with a single-resolution model: the highest-precedence level that has `async_mode` wins entirely (enabled flag + mechanism together). This means every model on an async-capable provider that should NOT be async must explicitly declare `enabled: false`.

The user's config has 3 models on Doubleword that are async and 1 (`tencent/Hy3-FP8`) that is sync. With the current opt-out model, the 3 async rules repeat the identical mechanism (`delivery: body, field: service_tier, value: flex`) and the sync model needs an explicit `enabled: false`.

A cleaner split: **provider declares the mechanism** (delivery/field/value/suffix — "this provider supports async via body+service_tier:flex"), **model/rule declares the intent** (`enabled: true` — "this model opts in"). Default is `enabled: false` (sync). No need to opt out of something you never opted into.

## Proposed change

### Resolution semantics (new)

`_resolve_async_mode` merges two concerns:

1. **Mechanism** (delivery/field/value/suffix): resolved from ModelEntry -> ModelRuleEntry -> ProviderConfig. The highest-precedence level that declares mechanism fields wins. A model/rule CAN override the provider's mechanism (e.g. a model needing `header` instead of `body`).
2. **Enabled flag**: resolved from the highest-precedence level that sets `enabled`. Default is `false` (sync). A model/rule with `enabled: true` opts in. A model/rule with `enabled: false` explicitly opts out (blocks fall-through to provider's `enabled: true`).

Application happens only when the merged result has `enabled: true` AND a complete mechanism. If `enabled: true` but no mechanism anywhere (no delivery/field/value at any level) -> warning log + no-op.

### Config validation changes

`AsyncModeConfig` validator relaxes: `{enabled: true}` with no mechanism fields is valid (pure flag). The field/value/suffix requirement only fires when the config declares a `delivery` AND mechanism fields are needed AND `enabled: true` AND there is no provider-level mechanism to inherit. Validation at config-load time can't know the provider context, so the "enabled but no mechanism" check moves to runtime (proxy resolution) as a warning.

Alternatively: keep the validator strict per-level (a level that has `enabled: true` must declare its own mechanism), and rely on tests to prove the merge works. This is simpler but less flexible. The task plan should pick one.

### Config example (new shape)

```yaml
providers:
  doubleword:
    name: doubleword
    base_url: "https://api.doubleword.ai/v1"
    api_key: "${DOUBLEWORD_API_KEY}"
    async_mode:
      delivery: body
      field: service_tier
      value: flex

model_rules:
  - prefix: "moonshotai/kimi-k3"
    provider: "doubleword"
    async_mode:
      enabled: true    # opts in; inherits mechanism from provider
  - prefix: "tencent/Hy3-FP8"
    provider: "doubleword"
    # no async_mode -> sync (default false, no opt-out needed)
```

### What changes in code

- `src/optiproxai/config.py` — `AsyncModeConfig` validator: allow `enabled: true` without mechanism fields (pure flag). Keep strict validation only when mechanism fields are partially declared.
- `src/optiproxai/proxy.py` — `_resolve_async_mode`: split into mechanism resolution + enabled resolution, then merge. Log warning if `enabled: true` but no mechanism resolved.
- `tests/test_async_mode.py` — update resolution tests for merge semantics; add test for "enabled at model, mechanism at provider"; add test for "model overrides provider mechanism".
- `tests/test_proxy_reload.py` — `TestModelExtraBody` may need adjustment if the `_prepare_body_for_candidate` behavior changes.
- `config.example.yaml` — rewrite examples to show the split.
- `README.md` — rewrite async/batch routing section: provider = capability + mechanism, model/rule = opt-in flag, default false.
- Decision record (doc-6) documenting the semantics change from TASK-6's opt-out to opt-in.

### Breaking change assessment

This changes the meaning of `ProviderConfig.async_mode.enabled: true`. In TASK-6, that meant "all models on this provider are async by default." After this change, it means "this provider supports async via this mechanism" (the `enabled` flag at provider level becomes vestigial or ignored — mechanism is declared, intent is at model level). Existing configs with `provider.async_mode.enabled: true` would need migration.

### Dependencies

Depends on TASK-6 (shipped). Branch off `task/TASK-6` (or `main` after TASK-6 merges).
<!-- SECTION:DESCRIPTION:END -->
