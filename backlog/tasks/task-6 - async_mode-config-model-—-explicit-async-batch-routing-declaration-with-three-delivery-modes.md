---
id: TASK-6
title: >-
  async_mode config model — explicit async/batch routing declaration with three
  delivery modes
status: To Do
assignee: []
created_date: '2026-08-18 13:13'
updated_date: '2026-08-18 18:59'
labels: []
dependencies: []
documentation:
  - >-
    decisions/doc-4 -
    Decision-ModelEntry-async_mode-propagates-via-RoutingDecision-and-FallbackEntry.md
  - >-
    decisions/doc-5 -
    Decision-Candidate-preparation-returns-body-plus-upstream-headers-tuple-return.md
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

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
# TASK-6 Implementation Plan: async_mode config model

## Approach

Add a first-class `AsyncModeConfig` Pydantic model and wire it through the three config levels (`ProviderConfig`, `ModelEntry`, `ModelRuleEntry`), then apply it in the proxy's candidate-preparation step. The design mirrors two existing patterns: prefix/provider scoring for rule-level resolution (`_get_model_extra_body`, proxy.py:1053) and per-candidate metadata propagation (`max_input_tokens`: ModelEntry -> ResolvedModelCandidate -> RoutingDecision/FallbackEntry).

Two design decisions are recorded in linked docs and are **load-bearing** for this plan:

- **doc-4** — ModelEntry.async_mode propagates via RoutingDecision/FallbackEntry (not re-lookup at proxy time). Rationale: by proxy time the profile/tier context is gone; the same model could have different async_mode in different tiers, so re-derivation is ambiguous. Follows the max_input_tokens precedent.
- **doc-5** — `_prepare_body_for_candidate` returns `tuple[dict[str, Any], dict[str, str]]` (body, extra_upstream_headers). Rationale: header delivery mode cannot be expressed through a body-only return; `_proxy_upstream` builds its own headers internally and must receive async headers explicitly. model_suffix mutates `body["model"]` in place — the body is the single source of truth for the model name (no third return value).

## Resolution semantics

`_resolve_async_mode(model, provider_name, runtime, entry_async_mode=None) -> AsyncModeConfig | None`:

1. **ModelEntry level**: if `entry_async_mode is not None` (supplied by caller from RoutingDecision/FallbackEntry), it wins — presence of the field, not `enabled` truthiness. This is what makes model-level `enabled: false` beat provider-level `enabled: true` (AC #10).
2. **ModelRuleEntry level**: scan `runtime.config.model_rules` with the same scoring as `_get_model_extra_body` — `(1 if entry.provider else 0, 0 if entry.prefix == "*" else len(entry.prefix))`, skip entries with `async_mode is None`.
3. **ProviderConfig level**: `runtime.config.providers[provider_name].async_mode`.
4. Otherwise `None` — graceful no-op, request routes sync (AC #9).

Application happens only when the resolved config has `enabled: true`. A resolved config with `enabled: false` means "explicitly disabled at this level" — do not fall through to lower levels.

## Files to modify

### `src/optiproxai/config.py`
- New `AsyncModeConfig(BaseModel)`: `enabled: bool = False`, `delivery: Literal["body", "header", "model_suffix"] = "body"`, `field: str = ""`, `value: str = ""`, `suffix: str = ""`. Place near `ContentPartPolicy` (top of file, before first use).
- Add `async_mode: AsyncModeConfig | None = None` to `ProviderConfig` (line 78), `ModelEntry` (line 91), `ModelRuleEntry` (line 300).
- `ModelEntry` has `extra="forbid"` — the field must be declared, which this plan does.

### `src/optiproxai/router.py`
- Add `async_mode: AsyncModeConfig | None = None` to `FallbackEntry` (line 64) and `RoutingDecision` (line 74).
- `route()` (line ~496): pass `async_mode=primary_candidate.async_mode` into RoutingDecision; pass `async_mode=fallback_candidate.async_mode` in the FallbackEntry loop (line 468-476).
- `resolve_model()` (line ~512): same two propagation lines.
- Import `AsyncModeConfig` from config.

### `src/optiproxai/config.py` — `ResolvedModelCandidate` (line 105)
- Add `async_mode: AsyncModeConfig | None = None`.
- `_resolve_candidate_entry` (line 162): pass `async_mode=entry.async_mode` when entry is a ModelEntry.

### `src/optiproxai/proxy.py`
- New `_resolve_async_mode(model, provider_name, runtime, *, entry_async_mode=None) -> AsyncModeConfig | None` — resolution logic per above, placed near `_get_model_extra_body`.
- New `_apply_async_mode(body: dict, headers: dict[str, str], async_mode: AsyncModeConfig) -> tuple[dict, dict[str, str]]` — applies the resolved config's delivery mechanism (called only when `enabled` is true). Returns mutated `(body, headers)`.
- `_prepare_body_for_candidate` (line 1291): signature gains `entry_async_mode: AsyncModeConfig | None = None` keyword param; return type changes to `tuple[dict[str, Any], dict[str, str]]`. Order of operations: sanitize -> normalize -> extra_body merge (unchanged) -> async resolve + apply. Async body injection merges **after** `extra_body` so explicit async config wins on conflict. Log with `ASYNC_MODE model=... provider=... delivery=...` at INFO when applied.
- `_try_with_fallbacks` (line 887): primary call site unpacks `(primary_body, primary_headers) = _prepare_body_for_candidate(body, decision.model, decision.provider, runtime, entry_async_mode=decision.async_mode)` and passes `extra_headers=primary_headers` to `_proxy_upstream`. Fallback loop (line 973): same pattern with `entry_async_mode=fb.async_mode`.
- `_proxy_upstream` (line 593): new keyword param `extra_headers: dict[str, str] | None = None`; after building the default headers dict (line 610-612), `if extra_headers: headers.update(extra_headers)`.

### `config.example.yaml`
- Provider-level `async_mode` example (OpenAI-style body + service_tier: flex).
- Model-level `enabled: false` override example inside a tier's primary list.
- Header and model_suffix delivery examples with comments.

### `README.md`
- Document `async_mode` in the model_rules / config section: the three delivery modes table, resolution order, and note that `extra_body` remains the escape hatch for non-async fields.

### `tests/test_proxy_reload.py`
- Update `TestModelExtraBody` direct calls to unpack tuple: `prepared, _ = proxy_mod._prepare_body_for_candidate(...)`.

### `tests/test_async_mode.py` (new)
Cover, using the same `RuntimeState` construction pattern as `TestModelExtraBody`:
- Each delivery mode applies correctly: body injects field into body; header appears in returned headers dict; model_suffix changes `body["model"]`.
- Resolution order: ModelEntry beats ModelRuleEntry beats ProviderConfig.
- Explicit disable: model-level `enabled: false` + provider-level `enabled: true` -> no async applied (AC #10).
- No-op: no async_mode anywhere -> body unchanged, headers empty (AC #9).
- extra_body still merges for non-async fields alongside async body injection (AC #8).
- Rule-level provider-specific outranks provider-agnostic (mirrors existing scoring test).

## Constraints / Risks

- **Breaking change is internal only**: `_prepare_body_for_candidate` is private; its direct test callers are updated. `_proxy_upstream`'s new param is optional — the no-decision pass-through path (line 1578+) is untouched.
- **FallbackEntry async_mode** must be set in both `route()` and `resolve_model()` or the fallback path silently drops model-level async config. Both call sites are in this plan.
- **Ordering guarantee**: async body injection must come after `extra_body` merge so async config semantics win over the generic escape hatch. Documented in `_prepare_body_for_candidate`.
- **model_suffix changes body["model"]**: this affects the model name sent upstream AND the name `_proxy_upstream` reads from body for usage logging. That is intended — the upstream sees the suffixed name, and logging should reflect what was actually sent.

## Task Manifest

| # | Title | Files | Depends On | Labels | Acceptance Criterion |
|---|---|---|---|---|---|
| 1 | Add AsyncModeConfig model + fields on ProviderConfig/ModelEntry/ModelRuleEntry | src/optiproxai/config.py | — | logic | AsyncModeConfig exists with enabled/delivery/field/value/suffix; all three config models accept optional async_mode and config loads without error |
| 2 | Propagate async_mode through ResolvedModelCandidate, RoutingDecision, FallbackEntry | src/optiproxai/config.py, src/optiproxai/router.py | 1 | logic | route() and resolve_model() produce decisions/fallbacks carrying ModelEntry.async_mode; max_input_tokens propagation unchanged |
| 3 | Implement _resolve_async_mode + _apply_async_mode and wire into _prepare_body_for_candidate | src/optiproxai/proxy.py | 2 | logic | Resolution order ModelEntry->ModelRuleEntry->ProviderConfig->None holds; all three delivery modes apply; enabled:false blocks lower levels; no-op when unconfigured |
| 4 | Pass async headers through _try_with_fallbacks into _proxy_upstream | src/optiproxai/proxy.py | 3 | logic | Primary and fallback calls pass extra_headers; _proxy_upstream merges them after defaults; pass-through path unaffected |
| 5 | Write tests for async_mode resolution, delivery modes, and precedence | tests/test_async_mode.py, tests/test_proxy_reload.py | 3,4 | test | All AC #17 scenarios pass; existing TestModelExtraBody updated to tuple unpack and still passes |
| 6 | Update config.example.yaml and README.md | config.example.yaml, README.md | 1 | docs | Both files document async_mode with delivery modes and resolution order examples |
| 7 | Run full CI gates | (no file changes) | 1-6 | test | `uv run ruff check src/`, `ruff format --check`, `pyright src/`, `pytest tests/ -q` all pass |
<!-- SECTION:PLAN:END -->
