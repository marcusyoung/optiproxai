---
id: doc-6
title: 'Decision: async_mode splits provider mechanism from model opt-in'
type: other
created_date: '2026-08-18 22:14'
---
# Decision: async_mode splits provider mechanism from model opt-in

**Date:** 2026-08-18
**Status:** Decided
**Task:** TASK-10
**Author:** dev (optiproxai/code)

## Context
TASK-6 shipped `async_mode` with single-resolution semantics: the highest-precedence level with `async_mode` wins entirely (enabled flag + mechanism together), and provider-level `enabled: true` made all models on the provider async by default (opt-out). The user's config has 3 async + 1 sync model on Doubleword; opt-out forces the sync model to declare `enabled: false` and repeats the identical mechanism 3 times. TASK-10 flips to opt-in: provider declares the mechanism, model/rule declares intent.

## Options Considered

| Option | Pros | Cons |
--------|------|------|
| Split: provider mechanism + model/rule opt-in (chosen) | Matches user's config shape; sync is default; no opt-out burden; mechanism declared once | Breaking change to TASK-6 provider-level `enabled` semantics |
| Keep TASK-6 opt-out semantics | No breaking change | Repeats mechanism per rule; sync model must opt out |
| Provider `enabled: true` = provider-wide default (middle ground) | Backward-compatible-ish | Reintroduces opt-out burden for the sync model; contradicts the opt-in goal |

## Decision
1. Provider declares mechanism only (`delivery`/`field`/`value`/`suffix`). Provider-level `enabled` is ignored (vestigial).
2. Model/rule declares intent: `enabled: true` opts in; `enabled: false` explicitly opts out. Default is `false` (sync).
3. Resolution merges field-by-field: mechanism fields from entry → rule → provider (highest level that explicitly sets a field wins); `enabled` from entry → rule only.
4. Application requires merged `enabled: true` AND a complete mechanism. Enabled without mechanism → warning log + no-op.
5. Validator relaxes: `{enabled: true}` alone is valid (pure flag); partial mechanisms (some mechanism fields set but incomplete for the effective delivery) are rejected at config load.

## Rationale
The user's stated goal: "provider declares the mechanism, model/rule declares the intent. Default is enabled: false (sync). No need to opt out of something you never opted into." Provider-level enabled-as-default would force the sync Hy3 model to opt out — the exact burden being removed. Field-by-field merge with `model_fields_set` distinguishes explicit from default, keeping the validator able to catch partial mechanisms without knowing provider context.

## Consequences
- Breaking change: TASK-6 configs with provider-level `enabled: true` no longer make models async; models must opt in. Documented in README + config.example.yaml.
- `_resolve_async_mode` returns a merged config (or None when not applicable) instead of the raw highest-precedence config.
- Runtime warning when `enabled: true` but no mechanism resolves.
- User's config migration: doubleword provider gains mechanism; 3 rules gain `enabled: true`; Hy3 untouched.
