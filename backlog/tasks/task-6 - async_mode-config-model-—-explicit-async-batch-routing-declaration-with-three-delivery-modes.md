---
id: TASK-6
title: >-
  async_mode config model — explicit async/batch routing declaration with three
  delivery modes
status: To Do
assignee: []
created_date: '2026-08-18 13:13'
updated_date: '2026-08-18 13:13'
labels: []
dependencies: []
priority: medium
type: feature
ordinal: 10000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add a first-class `async_mode` config model to OptiProxAI so that async/batch routing (e.g. OpenAI `service_tier: flex`, model-suffix conventions, custom headers) is explicitly declared in config rather than hidden inside the generic `extra_body` escape hatch.

## Problem
Currently async is configured via `ModelRuleEntry.extra_body` — a generic dict merged into the request body. This works but:
- No explicit "this model supports async" flag
- Only body injection — some providers need a header instead
- Provider-specific quirks (model-suffix naming, header-based APIs) are buried in `extra_body` with no semantic meaning
- The proxy can't tell whether a request is async or why, making dashboard tracking and per-turn overrides impossible

## Proposed config model

### AsyncModeConfig (new Pydantic model)
```python
class AsyncModeConfig(BaseModel):
    enabled: bool = False
    delivery: Literal["body", "header", "model_suffix"] = "body"
    field: str = ""      # for body/header: the field/header name
    value: str = ""      # for body/header: the value to send
    suffix: str = ""     # for model_suffix: the suffix to append (e.g. "flex")
```

### Placement
Add optional `async_mode: AsyncModeConfig | None = None` to three config models:
1. `ProviderConfig` — provider-level default (e.g. OpenAI defaults to body + `service_tier: flex`)
2. `ModelEntry` — per-model override in tier config
3. `ModelRuleEntry` — prefix-matched rule override

### Delivery modes
| `delivery` | How it works | Example |
|---|---|---|
| `body` | Injects `{field: value}` into request body JSON | OpenAI `service_tier: flex` |
| `header` | Injects HTTP header `{field: value}` on upstream request | Provider needing custom header |
| `model_suffix` | Appends `:{suffix}` to model name sent upstream | Provider using `model:flex` naming |

### Resolution order
`ModelEntry.async_mode` -> `ModelRuleEntry.async_mode` -> `ProviderConfig.async_mode` -> no-op (graceful, route sync)

This mirrors the existing `_get_model_reasoning_style` resolution pattern (model-specific -> provider-level -> none).

## Proxy changes
- New `_resolve_async_mode(model, provider_name, runtime) -> AsyncModeConfig | None` function following the same prefix/provider scoring as `_get_model_extra_body`
- `_apply_async_mode(body, headers, model, async_mode) -> (body, headers, model)` applies the delivery mechanism
- `_prepare_body_for_candidate` calls `_resolve_async_mode` then `_apply_async_mode`, replacing the current `extra_body` path for async-specific fields
- `extra_body` remains as a general-purpose escape hatch for non-async body fields

## Config example
```yaml
providers:
  openai:
    name: openai
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY}
    async_mode:
      enabled: true
      delivery: body
      field: service_tier
      value: flex

profiles:
  auto:
    tiers:
      REASONING:
        primary:
          - model: gpt-4o
            # inherits async_mode from provider
          - model: some-other-model
            async_mode:
              enabled: false  # explicitly disable async for this model
```
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 AsyncModeConfig Pydantic model exists with enabled, delivery, field, value, and suffix fields
- [ ] #2 ProviderConfig has optional async_mode field
- [ ] #3 ModelEntry has optional async_mode field
- [ ] #4 ModelRuleEntry has optional async_mode field
- [ ] #5 _resolve_async_mode function resolves async_mode using resolution order: ModelEntry -> ModelRuleEntry -> ProviderConfig -> None, with same prefix/provider scoring as _get_model_extra_body
- [ ] #6 _apply_async_mode function applies the three delivery modes: body (inject field into JSON body), header (add HTTP header), model_suffix (append :suffix to model name)
- [ ] #7 _prepare_body_for_candidate uses _resolve_async_mode and _apply_async_mode instead of extra_body for async-specific fields
- [ ] #8 extra_body remains functional for non-async body field injection
- [ ] #9 When no async_mode is configured at any level, request routes sync with no error (graceful no-op)
- [ ] #10 When async_mode.enabled is false at model level but provider has async_mode.enabled true, the model-level false wins (explicit disable)
- [ ] #11 config.example.yaml updated with async_mode examples for provider-level and model-level
- [ ] #12 README.md documents async_mode config model, delivery modes, and resolution order
- [ ] #13 uv run ruff check src/ passes
- [ ] #14 uv run ruff format --check src/ tests/ passes
- [ ] #15 uv run pyright src/ passes
- [ ] #16 uv run pytest tests/ -q passes
- [ ] #17 Tests cover: each delivery mode, resolution order (model overrides rule overrides provider), explicit disable (enabled: false wins), no-op when unconfigured, body/header/model_suffix injection correctness
<!-- AC:END -->
