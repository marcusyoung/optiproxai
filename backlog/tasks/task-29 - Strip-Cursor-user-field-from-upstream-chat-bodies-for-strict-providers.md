---
id: TASK-29
title: Provider omit_body_keys to drop Cursor-only fields for strict upstreams
status: Done
assignee: []
created_date: '2026-10-30T12:54:00Z'
updated_date: '2026-10-30T13:05:00Z'
labels: []
dependencies: []
references:
  - src/optiproxai/proxy.py
  - src/optiproxai/config.py
  - config.example.yaml
  - README.md
documentation:
  - doc-20
modified_files:
  - src/optiproxai/config.py
  - src/optiproxai/proxy.py
  - tests/test_proxy_reload.py
  - config.example.yaml
  - README.md
priority: high
type: bug
ordinal: 29000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Cursor injects a top-level OpenAI-style `user` field (e.g. auth0|…) on chat completions. OpenAI accepts it; Mistral rejects it with 422 extra_forbidden on body.user, so a working mistral-large profile still fails. The proxy already rewrites the upstream body copy for other adaptations; this field is request-only and is not echoed in completions, so stripping it from the outbound body is safe for Cursor. Observed live: request_id 99b3a6227316, profile mistral-large, provider mistral, model mistral-large-4.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 ProviderConfig accepts optional omit_body_keys (list of top-level body key names; default empty)
- [x] #2 When omit_body_keys is non-empty for the upstream provider, those keys are absent from the body sent by _proxy_upstream (routed, fallback, and passthrough)
- [x] #3 Providers with empty/absent omit_body_keys still forward top-level user unchanged
- [x] #4 A request that includes user still returns a normal completion response when the configured provider omits it and upstream accepts the body
- [x] #5 Tests cover omit-on, omit-off, and copy-on-change behaviour without logging key values
- [x] #6 config.example.yaml and README document omit_body_keys with a Mistral user example
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
## Approach

Add optional per-provider `omit_body_keys: list[str]` (top-level keys only; default `[]`). At the start of `_proxy_upstream`, resolve `actual_provider` → `ProviderConfig` and shallow-copy-drop any listed keys present on the outbound body. Documented in revised doc-20.

This replaces the earlier unconditional always-strip-`user` approach.

## Implementation steps

1. **Config** (`src/optiproxai/config.py`): add `omit_body_keys: list[str] = Field(default_factory=list)` on `ProviderConfig` with a short description. No nested paths. Empty = no-op (backward compatible).
2. **Proxy helper** (`src/optiproxai/proxy.py`): e.g. `_omit_configured_body_keys(body, keys) -> dict` — if no keys or none present, return body unchanged; else shallow copy without those keys. Never log key values.
3. **Wire in `_proxy_upstream`**: look up provider via `_require_runtime_state().config.providers.get(actual_provider)` (or pass runtime if cleaner); apply helper before stream_options/httpx. Missing provider or empty list → no change.
4. **Docs**: `config.example.yaml` mistral (or commented) example with `omit_body_keys: [user]`; brief README note under providers / Cursor guidance.
5. **Tests** (`tests/test_proxy_reload.py` and/or config unit tests):
   - Provider with `omit_body_keys: [user]`: routed + passthrough captured upstream body omits `user`; client still gets normal completion JSON.
   - Provider without the list: `user` still forwarded.
   - Helper: copy-on-change identity when nothing to omit.
6. **Operator note** (implementation notes / deploy): live VPS `config.yaml` mistral block needs `omit_body_keys: [user]` after merge — out of PR code unless `deploy/config.vps.yaml` already tracks mistral (do not commit unrelated local drift).

## Files

- Modify: `src/optiproxai/config.py`
- Modify: `src/optiproxai/proxy.py`
- Modify: `tests/test_proxy_reload.py` (and config test if project pattern prefers)
- Modify: `config.example.yaml`, `README.md`

## Constraints / risks

- Top-level keys only; no `a.b` nested paths.
- Never log omitted values (Cursor `user` is an auth identifier).
- Fallbacks: each `_proxy_upstream` call uses that attempt’s `actual_provider` omit list.
- Forbidding omit of `messages`/`model` is optional hygiene — if added, keep it minimal (warn + skip reserved keys) or leave to operator responsibility; prefer operator responsibility unless review asks otherwise.

## Validation

- Focused pytest for omit on/off + config load.
- CI bar (ruff, format, pyright, full pytest) before PR.
- Live: set mistral `omit_body_keys: [user]`, restart, Cursor → `optiproxai/mistral-large` should not 422 on `body.user`.

## Out of scope

- Unconditional global strip of `user`.
- Nested/glob key paths.
- `optiproxai/<provider>/<model>` virtual model paths.

## Task Manifest

| # | Title | Files | Depends On | Labels | Acceptance Criterion |
|---|---|---|---|---|---|
| 1 | Add omit_body_keys + apply in _proxy_upstream + tests + docs | src/optiproxai/config.py, src/optiproxai/proxy.py, tests/test_proxy_reload.py, config.example.yaml, README.md | — | logic, test, docs | Per-provider omit_body_keys drops listed top-level keys on upstream send when configured, leaves others unchanged, tests and docs cover it |

Single-task plan: no Phase 2 subtasks. Branch `task/TASK-29` already cut from main.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented ProviderConfig.omit_body_keys + _omit_configured_body_keys wired at start of _proxy_upstream (copy-on-change; logs key names only).

Tests: TestOmitConfiguredBodyKeys + TestOmitBodyKeysUpstream in test_proxy_reload.py (omit on/off, copy-on-change, ProviderConfig load).

Docs: config.example.yaml mistral example; README omit_body_keys section.

CI: ruff check/format, pyright clean; 528 pytest passed.

Live follow-up (operator): add omit_body_keys: [user] under mistral in VPS config.yaml and restart.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added optional per-provider omit_body_keys (default empty). _proxy_upstream shallow-copies the outbound chat body without those top-level keys when the resolved actual_provider lists them, covering routed/fallback/passthrough. Documented with a Mistral `[user]` example for Cursor’s client-only field. Tests cover helper identity/copy-on-change, omit-on vs omit-off upstream JSON, and config loading. Full suite 528 passed; ruff/pyright clean. Deploy note: live mistral provider must set omit_body_keys: [user] after pull/restart.
<!-- SECTION:FINAL_SUMMARY:END -->
