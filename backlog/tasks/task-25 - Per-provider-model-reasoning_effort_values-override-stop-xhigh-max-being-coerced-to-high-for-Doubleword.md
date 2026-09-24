---
id: TASK-25
title: >-
  Per-provider/model reasoning_effort_values override (stop xhigh/max being
  coerced to high for Doubleword)
status: Done
assignee: []
created_date: '2026-09-24 12:30'
updated_date: '2026-09-24 13:13'
labels: []
dependencies: []
references:
  - src/optiproxai/config.py
  - src/optiproxai/router.py
  - src/optiproxai/proxy.py
  - TASK-24
  - tests/test_reasoning_effort_values.py
  - 'branch: task/TASK-25'
documentation:
  - >-
    decisions/doc-18 -
    Decision-reasoning_effort_values-vocabulary-override-—-precedence-empty-list-semantics-TASK-24-boundary.md
priority: medium
type: enhancement
ordinal: 25000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Reasoning effort is normalized against a hard-coded per-style allow-list. The `xai` style is capped at none/low/medium/high, so any `xhigh` or `max` value is silently coerced to `high`. Doubleword's API accepts the full vocabulary — verified live 2026-09-24 against deepseek-ai/DeepSeek-V4.1-Flash: `none, minimal, low, medium, high, xhigh, max` (case-sensitive strings only; numbers and capitalised values return HTTP 400). Because that vocabulary is provider-specific while the style is shared, the config's tier-level `reasoning_effort: "max"` never reaches a Doubleword model as `max`, so the top tiers cannot be differentiated and users pay for a rung that is never sent. The allow-list therefore needs to be overridable per provider and per model rule instead of being fixed to the style. Related: TASK-24 covers per-candidate effort VALUE; this task covers the per-candidate allowed VOCABULARY.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 ProviderConfig accepts an optional reasoning_effort_values list; when unset, normalization is unchanged and the style default allow-list applies
- [x] #2 ModelRuleEntry accepts the same optional field, and precedence is model rule > provider > style default
- [x] #3 Reasoning-effort normalization uses the resolved allow-list: a value present is passed through unchanged (so max/xhigh reach Doubleword), a value absent keeps the existing safe coercion (xhigh/max to high, otherwise medium)
- [x] #4 An empty reasoning_effort_values list suppresses reasoning-effort injection for that provider/model, documented as equivalent to a none style
- [x] #5 config.example.yaml declares Doubleword's full supported vocabulary (none, minimal, low, medium, high, xhigh, max) and README/config docs describe the field and its precedence
- [x] #6 Tests cover: model rule wins over provider; provider wins over style default; in-list pass-through; out-of-list coercion; empty list suppresses injection; unresolvable/invalid values handled as today; both the primary and fallback injection paths
- [x] #7 The tier-level /optiproxai:<tier> override path resolves and applies the same allow-list
- [x] #8 Regression coverage confirms providers without the field keep current behaviour on the xai default set (none, low, medium, high)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
# TASK-25 Plan — Per-provider/model reasoning_effort_values (vocabulary override)

## Objective
Make the reasoning-effort **allow-list** overridable per provider and per model rule so Doubleword's full vocabulary (`none, minimal, low, medium, high, xhigh, max`) is reachable — letting the tier-level `reasoning_effort: max` actually reach a Doubleword model as `max` instead of being coerced to `high`. Unset config keeps today's behaviour byte-for-byte.

## Scope boundary (TASK-24)
- This task owns the per-candidate **vocabulary** (which labels are permitted). TASK-24 owns the per-candidate effort **value** (`ModelEntry.reasoning_effort`). They are separate layers and are NOT folded (see decisions/doc-18).
- No `ModelEntry` field is added here; no router/`RoutingDecision` schema change (resolution happens at injection time, where model + provider + config are all available).
- The tier-level `/optiproxai:<tier>` override needs no special-casing: it resolves through the same `decision.reasoning_effort` injection path, so it inherits the allow-list automatically (AC #7 verified by test).

## Design

### 1. Config models (`src/optiproxai/config.py`)
- `ProviderConfig`: add `reasoning_effort_values: list[str] | None = Field(default=None, description=...)`.
- `ModelRuleEntry`: add the same field.
- Both default `None`; neither model sets `extra="forbid"`, so the field must be declared to be read (today an undeclared key would be silently ignored).
- Free-form `list[str]` — the vocabulary is provider-specific, so no enum.

### 2. Resolution + normalization (`src/optiproxai/proxy.py`)
- New `_get_model_reasoning_effort_values(model, provider_name, runtime) -> list[str] | None`: mirrors `_get_model_reasoning_style`'s prefix/provider scoring exactly, but treats `entry.reasoning_effort_values is not None` as a declaration (so a matching **empty list wins** over provider/ style default) and returns the best match.
- New `_get_provider_reasoning_effort_values(provider_name, runtime) -> list[str] | None`: provider field or `None`.
- New `_resolve_reasoning_effort_values(model, provider_name, runtime) -> list[str] | None`: rule > provider > `None` (sentinel for "use style default").
- `_normalize_reasoning_effort(style, effort, allowed_values: list[str] | None = None) -> str`: when `allowed_values is None`, use the existing hard-coded style table; otherwise use `{v.strip().lower() for v in allowed_values}`. Coercion for an absent value is unchanged (`xhigh`/`max` -> `high`, else `medium`).
- `_apply_reasoning_for_style(body, style, effort="medium", allowed_values: list[str] | None = None)`: add the param, thread it into `_normalize_reasoning_effort`, and return the body unchanged (no injection) when `allowed_values == []` — documented as equivalent to a `none` style. Existing `style == "none"` and `_has_explicit_reasoning_control` short-circuits stay first.

### 3. Injection call sites (primary + fallback)
- **Primary** (`proxy.py` ~2350): resolve `allowed = _resolve_reasoning_effort_values(decision.model, decision.provider, state)`; pass it to `_apply_reasoning_for_style` and to the log-only `_normalize_reasoning_effort` call. When `allowed == []`, log `REASONING_CONTROL suppressed ...` instead of `injected`.
- **Fallback** (`proxy.py` ~1170): resolve the allow-list per fallback candidate (`fb.model`, `fb.provider`) — each candidate may sit behind a different provider — and pass it through the same two calls. Suppress + log the same way.
- Fallback base-body logic is unchanged (still styled from the pre-injection `fallback_body_base`).

### 4. Docs (AC #5)
- `config.example.yaml`: comment examples of `reasoning_effort_values` on a `model_rules` entry and on a `providers` entry, declaring Doubleword's full vocabulary (`none, minimal, low, medium, high, xhigh, max`) and noting the empty list suppresses injection.
- `README.md`: new "Reasoning effort" subsection (after capability routing / near async) documenting: the tier-level `reasoning_effort` field, the `reasoning_effort_values` overrides, precedence (model rule > provider > style default), the empty-list suppression rule, and the built-in per-style default table.

## Files to create / modify
- Modify: `src/optiproxai/config.py` (two fields)
- Modify: `src/optiproxai/proxy.py` (3 new helpers, 2 changed signatures, 2 call sites)
- Modify: `config.example.yaml`, `README.md`
- Create: `tests/test_reasoning_effort_values.py`

## Sub-steps (with dependencies)
1. Add the two config fields (+ descriptions). Depends: —
2. Add resolvers, extend `_normalize_reasoning_effort` / `_apply_reasoning_for_style`, wire the primary + fallback call sites + suppression logging. Depends: 1
3. Write tests (unit resolution + normalization + primary/fallback end-to-end + tier-override path + regression for providers without the field). Depends: 2
4. Docs in `config.example.yaml` and `README.md`. Depends: 1

## Verification
- `uv run pytest tests/test_reasoning_effort_values.py -q` then the full suite.
- `uv run ruff check src/`; `uv run ruff format --check src/ tests/`; `uv run pyright src/`.
- `uv run opx config` still strict-loads the example config.
- Manual/regression: a provider without `reasoning_effort_values` on the `xai` style still emits at most `high`.

## Constraints / risks / open questions
- **Three-state `None` vs `[]`** is the main correctness risk — tests must assert a matching empty-list rule beats a provider value and a style default.
- **Double normalization**: the callers' log-only `_normalize_reasoning_effort` and the in-function one must receive the same resolved allow-list so the logged `normalized_effort` matches what is sent.
- **No router change** — if a future need arises to show the vocabulary on `RoutingDecision`, that is follow-up work.
- No open questions; the fold decision is resolved (decisions/doc-18).

## Branching
Single cohesive change: cut `task/TASK-25` from `main`, one PR to `main`. Subtasks not required (manifest rows are tightly coupled through the proxy functions).

## Task Manifest
| # | Title | Files | Depends On | Labels | Acceptance Criterion |
|---|---|---|---|---|---|
| 1 | Add reasoning_effort_values to ProviderConfig and ModelRuleEntry | src/optiproxai/config.py | — | logic | Both models accept an optional reasoning_effort_values list and an unset value leaves normalization unchanged |
| 2 | Implement vocabulary resolution, allow-list normalization, and primary/fallback injection | src/optiproxai/proxy.py | 1 | logic | Rule > provider > style-default precedence resolves, in-list values pass through (max reaches Doubleword), out-of-list values coerce as today, and an empty list suppresses injection on both the primary and fallback paths |
| 3 | Add reasoning-effort vocabulary tests | tests/test_reasoning_effort_values.py | 2 | test | Tests cover precedence, pass-through, coercion, empty-list suppression, invalid/unresolvable values, the tier-override path, and the no-field xai regression, and all pass |
| 4 | Document reasoning_effort_values and precedence | config.example.yaml, README.md | 1 | docs | The example config declares Doubleword's full vocabulary and the README documents the field, its precedence, and empty-list suppression |
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Branch task/TASK-25 cut from main (4af1401). Files: src/optiproxai/config.py (ProviderConfig + ModelRuleEntry reasoning_effort_values), src/optiproxai/proxy.py (added _get_model_reasoning_effort_values, _get_provider_reasoning_effort_values, _resolve_reasoning_effort_values; extended _normalize_reasoning_effort + _apply_reasoning_for_style with allowed_values; wired primary + fallback call sites with suppression logging), tests/test_reasoning_effort_values.py (new), config.example.yaml, README.md.

Precedence implemented as model rule > provider > style default. None = inherit style default; [] = suppress injection (treated like style none); non-empty list = allow-list with case/whitespace-insensitive matching. Model-rule matching reuses the existing prefix/provider scoring (provider-specific beats prefix specificity); an empty-list rule still wins as a declaration (checked with `is not None`).

Injection is resolved per candidate at inject time (primary uses decision.model/provider, fallback uses fb.model/fb.provider), so the /optiproxai:<tier> override path needs no special-casing and picks up the allow-list automatically.

Verification: pytest 471 passed (23 new). ruff check src/ pass; ruff format --check src/ tests/ 41 formatted; pyright 0 errors; uv build OK; yaml.safe_load(config.example.yaml) OK. Pre-existing ruff issues in tests/ (test_input_limit_routing.py, test_agentic_training_script.py) are unrelated and not on the CI path (CI lints src/ only).

No commit or PR created (not explicitly requested).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Implemented per-provider and per-model-rule reasoning-effort allow-list overrides (reasoning_effort_values) so Doubleword's full vocabulary (none, minimal, low, medium, high, xhigh, max) is reachable and a tier-level reasoning_effort: max is no longer coerced to high. Added optional reasoning_effort_values to ProviderConfig and ModelRuleEntry (config.py); added resolvers _get_model_reasoning_effort_values / _get_provider_reasoning_effort_values / _resolve_reasoning_effort_values and extended _normalize_reasoning_effort and _apply_reasoning_for_style to take an explicit allow-list (proxy.py); wired both the primary and fallback injection paths to resolve the allow-list per candidate, with suppression logging for an empty list. Precedence is model rule > provider > style default; None inherits the style default, [] suppresses injection (equivalent to a none style), a non-empty list is the allow-list (case/whitespace-insensitive). Unset config keeps the exact prior behaviour. Documented the field, precedence, empty-list semantics, and the per-style default table in README.md and config.example.yaml. Added tests/test_reasoning_effort_values.py (23 tests) covering precedence, pass-through, coercion, empty-list suppression, invalid/unresolvable values, the no-field xai regression, and both injection paths including the /optiproxai:<tier> override. Full CI gate green (ruff, format, pyright, 471 pytest, uv build). No deviations from the approved plan; no commit/PR created.
<!-- SECTION:FINAL_SUMMARY:END -->
