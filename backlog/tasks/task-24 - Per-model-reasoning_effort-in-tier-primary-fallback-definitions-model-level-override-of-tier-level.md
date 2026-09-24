---
id: TASK-24
title: >-
  Per-model reasoning_effort in tier primary/fallback definitions (model-level
  override of tier-level)
status: To Do
assignee: []
created_date: '2026-09-08 00:16'
labels: []
dependencies: []
references:
  - src/optiproxai/config.py
  - src/optiproxai/router.py
  - src/optiproxai/proxy.py
priority: medium
type: enhancement
ordinal: 24000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Currently `reasoning_effort` is only supported at the tier level (TierModelConfig, config.py ~line 280). There is no per-model field on ModelEntry (which supports only model, provider, max_input_tokens, async_mode, pricing). This forces the user to pick reasoning-effort-compatible models within a tier when a tier-level effort is set, since one value is injected for whichever candidate is selected.

Goal: support per-model `reasoning_effort` under `primary` and `fallback` entries in the tier definition.

Scope to investigate/decide:
- Add optional `reasoning_effort` field to ModelEntry (config.py).
- Precedence rule: model-level value overrides tier-level; fall back to tier-level when unset (confirm with user).
- Router resolution: thread per-candidate effort through ResolvedModelCandidate / RoutingDecision so the proxy injects the selected candidate's effort (router.py ~544/~631, proxy.py ~2350 and fallback path ~1170).
- Config validation + docs: update config.example.yaml and README.
- Tests: model-level override wins; tier-level fallback applied when model-level unset; both primary and fallback candidates; provider-style normalization still applies.
- Check interaction with tier override (/optiproxai:<tier> modifier) — tier-level effort should still apply when candidate has none.
<!-- SECTION:DESCRIPTION:END -->
