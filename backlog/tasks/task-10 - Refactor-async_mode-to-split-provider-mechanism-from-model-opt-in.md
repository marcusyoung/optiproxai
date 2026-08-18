---
id: TASK-10
title: Refactor async_mode to split provider mechanism from model opt-in
status: To Do
assignee: []
created_date: '2026-08-18 22:03'
updated_date: '2026-08-18 22:14'
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
  - >-
    decisions/doc-6 -
    Decision-async_mode-splits-provider-mechanism-from-model-opt-in.md
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

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
# Implementation Plan: async_mode mechanism/opt-in split

## Approach

Flip async_mode from opt-out (TASK-6) to opt-in: **provider declares the mechanism** (delivery/field/value/suffix), **model/rule declares intent** (`enabled: true` opts in, default `false`). Resolution becomes a field-by-field merge instead of "highest-precedence level wins entirely".

### Merge semantics (new `_resolve_async_mode`)

Collect candidate configs by precedence: ModelEntry (via `entry_async_mode`) → best ModelRuleEntry (existing prefix/provider scoring) → ProviderConfig. Merge field-by-field using `model_fields_set` to distinguish explicit from default:

- **Mechanism fields** (`delivery`, `field`, `value`, `suffix`): highest-precedence level that explicitly sets the field wins. A model/rule CAN override the provider's mechanism (e.g. rule sets `delivery: header` while provider declares body).
- **`enabled`**: resolved from entry → rule only. **Provider-level `enabled` is ignored (vestigial)** — the provider declares capability, not intent. Default `false`.
- Return the merged `AsyncModeConfig` when any level declared anything (enabled or mechanism); return `None` when nothing is set anywhere.

Application gate (unchanged caller shape): `async_mode is not None and async_mode.enabled` → `_apply_async_mode`. If merged `enabled: true` but the mechanism is incomplete (no field/value for body/header, no suffix for model_suffix) → `logger.warning` + no-op (return None). This check must live at runtime because config-load time cannot know the provider context.

### Validator decision (open question in task description — resolved)

Relaxed per-level + partial-mechanism rejection, not strict per-level:

- `{enabled: true}` alone is **valid** (pure flag — mechanism may come from the provider).
- When any mechanism field (`delivery`/`field`/`value`/`suffix`) is **explicitly set** (`model_fields_set`), the mechanism must be complete for the effective delivery (body/header → field+value; model_suffix → suffix). Partial mechanisms are rejected at config load regardless of `enabled` — catches typos without needing provider context.
- "enabled but no mechanism anywhere" is only detectable at runtime → warning + no-op.

Rationale: strict per-level validation would force every opt-in rule to repeat the mechanism, defeating the split. The relaxed validator + runtime warning matches the task's proposed change and the user's stated goal.

### Provider-level `enabled` handling

Ignored (vestigial). Documented in README + config.example.yaml as a breaking change from TASK-6: configs with `provider.async_mode.enabled: true` no longer make models async — models must opt in.

## Files

- `src/optiproxai/config.py` — `AsyncModeConfig._validate_enabled_fields` rework: pure-flag acceptance + partial-mechanism rejection via `model_fields_set`.
- `src/optiproxai/proxy.py` — `_resolve_async_mode` rewrite to field-by-field merge; warning on enabled-without-mechanism. `_apply_async_mode` and `_prepare_body_for_candidate` gate unchanged.
- `tests/test_async_mode.py` — rework resolution tests to merge semantics; add: enabled-at-model + mechanism-at-provider, model-overrides-provider-mechanism, provider-mechanism-alone no-ops (opt-in default), enabled-without-mechanism warns + no-ops, pure-flag valid, partial-mechanism rejected.
- `tests/test_proxy_reload.py` — verify `TestModelExtraBody` unaffected (extra_body path unchanged); adjust only if a test breaks.
- `config.example.yaml` — rewrite the three async examples to the split shape (provider mechanism; rule `enabled: true` opt-in).
- `README.md` — rewrite Async / batch routing section: provider = capability + mechanism, model/rule = opt-in flag, default false, merge semantics, warning behavior.
- `docs/decisions/doc-6` — created (Decision: async_mode splits provider mechanism from model opt-in).

## Constraints / Risks / Open Questions

- **Breaking change**: TASK-6 provider-level `enabled: true` semantics change. Documented; user's config migrated post-merge (step 5).
- **Merge completeness**: `delivery` defaults to `body` when unset; completeness is checked against the effective delivery after merge.
- **No open questions** — validator strictness resolved above (relaxed + partial rejection).

## Sub-steps

1. Config validator rework + config-level tests.
2. Proxy merge resolution + application + resolution tests.
3. Docs: README + config.example.yaml.
4. Full CI gates + PR to main.
5. (Post-merge, outside PR) Migrate user's `C:\Users\myoun\.config\optiproxai\config.yaml`: doubleword provider gains mechanism; 3 rules gain `enabled: true`; Hy3 untouched.

## Task Manifest

| # | Title | Files | Depends On | Labels | Acceptance Criterion |
|---|---|---|---|---|---|
| 1 | Rework AsyncModeConfig validator for opt-in semantics | src/optiproxai/config.py, tests/test_async_mode.py | — | logic, test | AsyncModeConfig accepts `{enabled: true}` pure flag and rejects partial mechanisms; config-level tests pass |
| 2 | Rewrite _resolve_async_mode as field-by-field merge | src/optiproxai/proxy.py, tests/test_async_mode.py, tests/test_proxy_reload.py | 1 | logic, test | Merged resolution applies enabled-at-model + mechanism-at-provider, honors model/rule mechanism override, no-ops provider-mechanism-alone, warns + no-ops enabled-without-mechanism; all tests pass |
| 3 | Update docs and example config | README.md, config.example.yaml | 2 | docs | README async section and config.example.yaml show provider mechanism + model/rule opt-in shape |
| 4 | Run full CI gates and open PR | (none) | 3 | infra | ruff check, ruff format --check, pyright, pytest, uv build all pass; PR opened to main |
| 5 | Migrate user config to opt-in shape (post-merge) | C:\Users\myoun\.config\optiproxai\config.yaml | 4 | infra | doubleword provider declares mechanism; 3 rules declare `enabled: true`; Hy3 untouched; `uv run optiproxai config` validates |
<!-- SECTION:PLAN:END -->
