---
id: TASK-28
title: Route named custom subagents to a pinned model via a router signature hook
status: Done
assignee: []
created_date: '2026-10-03 17:42'
updated_date: '2026-10-03 18:40'
labels: []
dependencies: []
references:
  - src/optiproxai/config.py
  - src/optiproxai/router.py
  - tests/test_subagent_routing.py
  - config.example.yaml
  - README.md
  - >-
    backlog/docs/decisions/doc-19 -
    Decision-Pin-named-custom-subagents-via-a-proxy-layer-signature-hook.md
documentation:
  - >-
    backlog/docs/decisions/doc-19 -
    Decision-Pin-named-custom-subagents-via-a-proxy-layer-signature-hook.md
modified_files:
  - src/optiproxai/config.py
  - src/optiproxai/router.py
  - tests/test_subagent_routing.py
  - config.example.yaml
  - README.md
priority: high
type: feature
ordinal: 28000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Cursor pins subagents to the parent conversation's model under BYOK, and custom/BYOK model IDs are deliberately excluded from subagent model selection, so a subagent can no longer be pinned via its own config for Cursor-hosted agents. A custom subagent only needs a different model than the parent (e.g. a strong-retrieval model for a web-researcher subagent) — that requirement cannot be met Cursor-side. The proxy sits in front of every request and cannot be bypassed by Cursor's pinning, so routing for a specific subagent can be decided there. Requests are distinguishable at the proxy by the message-content line Cursor injects for a named custom subagent (`You are operating as the "<name>" custom subagent.`); `system_prompt` is identical to the parent and cannot be used. The value is a generic, configurable mechanism so any custom subagent can be mapped to a target model/provider from config without code edits, rather than a one-off hardcoded rule.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A pre-classification hook in Router.route() inspects the request message content for the named-custom-subagent signature and identifies the subagent name
- [x] #2 When the subagent matches a configured signature, routing pins the configured target provider and model directly and bypasses classifier tier second-guessing
- [x] #3 The signature-to-target mapping is expressed as config (no code change required to add a subagent rule), with schema and validation in config.py
- [x] #4 A config with no signature rules leaves routing behaviour byte-for-byte unchanged for all requests, including parent requests
- [x] #5 When no signature matches, routing falls through to the existing classifier and tier logic unchanged
- [x] #6 Unit tests cover: signature match pins the target model/provider, non-match is unchanged, an unrecognised subagent name does not pin, and malformed/empty config rules degrade gracefully
- [x] #7 config.example.yaml and README.md document the new config key with a realistic example
- [x] #8 The change is scoped to router.py and config.py (plus tests and docs)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
## Approach

Add an opt-in, config-declared `subagent_routes` list. Each entry binds a subagent identity to a pinned provider+model AND defines the literal request marker ("signature") that identifies that subagent, defaulting to Cursor's injected `You are operating as the "<name>" custom subagent.` line but overridable per entry (template with optional `{name}` placeholder, or a fully literal string) so the mechanism is not tied to one client's wording and works with any harness that injects a marker. At the top of `Router.route()` (before the scorer runs), scan the FULL message list for each configured signature (config order, first match wins); on match, return a `RoutingDecision` pinned to the entry's provider/model/reasoning_effort, bypassing tier classification entirely. No configured routes -> strict no-op.

## Why this shape

- Config-defined signature: the marker text is data, not code — a route can match Cursor's named-subagent line (default) or any other harness's injected marker, so the hook is usable more widely.
- The mechanism only ties a matched subagent session to a model+provider. Parent requests never carry the signature, so parent routing is completely unaffected — the subagent is a separate session and the pin is scoped to it.
- Pinning model+provider (not a tier) is required because a tier pin would still let the classifier/tier candidate lists choose a model from that tier, whereas the requirement is one specific retrieval-strong model.
- Detection + pinning live in `router.py`; schema + validation in `config.py`; docs + tests alongside. Keeps the diff bounded and matches the CLI -> config/router -> scorer architecture.
- Detection scans the raw message list, NOT `classification_input.text`: the classification input keeps only the last 3500 characters, which would lose a marker sitting at the start of a long conversation.

## Pre-start verification (run before writing any code)

Validate the discriminator under real multi-turn conditions, not just the single-request probe:

1. Launch a `web-researcher` subagent (Cursor Task tool) with a task requiring several turns (web search + follow-ups) so its conversation spans multiple model calls.
2. On the VPS, inspect the newest `/var/log/optiproxai/routing-*.jsonl` around the run window: check whether the marker appears in `last_user_message` on every turn, and in `prompt` (the truncated classification tail).
3. Decision rule: marker present across turns (or provably retained in message history) -> proceed as planned. Marker absent entirely from a real multi-turn run -> STOP: the discriminator is invalid and the design must be revisited. If only early turns carry it, full-history scanning still works but conversation compaction could drop it mid-session — record as a monitored residual risk.

Record the evidence in task notes.

## Files to modify

- `src/optiproxai/config.py` - add `SubagentRoute` model `{name, signature?, provider?, model, reasoning_effort?}` with a `resolved_signature` property + `OptiproxaiConfig.subagent_routes` + a load-time validator (provider resolution, duplicate resolved-signature rejection).
- `src/optiproxai/router.py` - add `_detect_subagent_route` (full-message scan), `_build_subagent_decision`; hook in `route()` after `classification_input` is built and before the scorer. Log the pinned decision via `RoutingLogger` so the VPS routing log shows it.
- `tests/test_subagent_routing.py` - new test file (config validation + router pin/fall-through/list-content/empty-config).
- `config.example.yaml` - document `subagent_routes` with a commented example including a custom `signature`.
- `README.md` - document the new key and the BYOK rationale in one short section.
- `deploy/config.vps.yaml` - (deployment step) add the `subagent_routes` entry; Hy3 is already present in the live VPS config (user-confirmed 2026-10-03) — mirror live state into the repo deploy file if the two have drifted.

## Constraints and risks

- Opt-in only: empty/absent `subagent_routes` is a strict no-op (`if not self.config.subagent_routes` guard).
- `provider` validated against `providers` at load time (blank -> `default_provider`); unknown provider is a `ValueError`, not a silent fallback.
- `tier` on a pinned decision is an observability label only (the classifier did not run); `score`/`confidence` = 1.0, `signals = ["subagent_pin"]`.
- Fallbacks are empty on a pin: the point is not to let the router substitute another model.
- Pinned requests bypass input-limit filtering: an over-cap pinned session surfaces the upstream provider's error instead of substituting a model (by design). Follow-up task if it bites in practice.
- Signature matching is literal substring, not regex: message bodies can be megabytes (base64 image parts), and a user-supplied regex over that is a performance/ReDoS hazard. Template + `{name}` substitution covers the cross-harness cases.
- Verify the exact live model id/provider for Hy3 when wiring the route; if the live entry exists only on the VPS, mirror it into `deploy/config.vps.yaml`.
- Tests/docs must include: custom literal signature with no `{name}`; default signature; list-content part; marker in an older message; unrecognised name -> normal routing; competing routes -> first wins; duplicate/empty/invalid config entries -> load error; no routes -> no-op.

## Implementation sub-steps

1. `config.py`: `SubagentRoute` model with `resolved_signature` property (default template; `{name}` substitution); `subagent_routes` field; validator (non-empty name/model, provider resolution, duplicate resolved signatures rejected, `extra="forbid"`).
2. `router.py`: `_detect_subagent_route(messages, routes)` (full-message scan, str + list text parts, config order); `_build_subagent_decision(route, profile, session_key)` returning the pinned decision + `RoutingLogger` entry; hook in `route()` after `classification_input`, before the scorer, guarded by non-empty routes.
3. Tests in `tests/test_subagent_routing.py`.
4. Docs: `config.example.yaml` + `README.md`.
5. Gates: `ruff check`, `ruff format --check`, `pyright`, `pytest` (targeted then full suite).

## Verification

- Unit tests pass; full suite green; lint/format/typecheck clean.
- Local dry-run: `uv run optiproxai route` with a synthetic message carrying the marker (default and custom literal signatures) -> pinned model/provider shown; message without the marker routes normally.
- VPS (user-driven, separate step): add the route to the live config, `systemctl restart optiproxai`, run a `web-researcher` probe, confirm the ROUTE log shows the pinned model/provider for the subagent and unchanged routing for the parent.

## Task Manifest

| # | Title | Files | Depends On | Labels | Acceptance Criterion |
|---|---|---|---|---|---|
| 1 | Add `subagent_routes` config model + validator (config-defined signature) | src/optiproxai/config.py | - | logic | Config load accepts valid routes (default or custom signature), rejects unknown providers and duplicate resolved signatures, and an empty list is a no-op |
| 2 | Add router full-message signature detection + pinned decision | src/optiproxai/router.py | 1 | logic | A request whose message content carries a configured signature returns a decision pinned to the route's provider/model/reasoning_effort with a `subagent_pin` signal |
| 3 | Add subagent routing tests | tests/test_subagent_routing.py | 1, 2 | test | Default/custom signatures, history scan, fall-through, unrecognised name, empty config, and invalid-config cases all pass |
| 4 | Document the config key | config.example.yaml, README.md | 1 | docs | `subagent_routes` (with the `signature` override) is documented with a realistic commented example and the BYOK rationale |

Single-task scope: one cohesive feature, one PR (manifest = implementation sequence, not subtasks). The pre-start verification runs before manifest step 1.

## Amendment (2026-10-03) - compound signature (parent-capture hardening)

Chosen at review: a match now requires BOTH the per-entry named marker AND Cursor's generic subagent reminder present in the same message text.

- `signature` (unchanged): the named marker, default `You are operating as the "{name}" custom subagent.`
- `require` (NEW, optional): a second literal that must ALSO be present; default = Cursor's generic reminder `You are running as a subagent under a parent agent.` Set to empty/null to disable and fall back to single-signature behaviour.
- Match rule: `signature in text and (require == "" or require in text)`. Scanned over the full message list, config order, first match wins (unchanged).

Rationale: the named marker alone is just text a parent can quote (this very session contains it). The generic reminder is present on every real subagent turn and absent from such quotes, so the AND removes the common false positive.

Evidence: in the real log, every named-subagent run (75-81, 126, 187-197) also carries the generic reminder, so compound matching still pins every real subagent turn; the Ask-mode run (85-90) carries the generic reminder but no name -> still correctly not pinned.

Residual (accepted): a parent message pasting a full log line that contains BOTH strings can still false-positive; no first-message anchor is being added (explicitly deferred).

Tests to add for the amendment: (a) named marker present but generic reminder absent -> NO pin; (b) both present -> pin; (c) `require: ""` -> single-signature still pins on the named marker alone.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Pre-start discriminator verification (PASSED 2026-10-03)

Method: 10-turn `web-researcher` probe run, checked against the real routing log (`/var/log/optiproxai/routing-2026-10-03.jsonl`, 200-line tail) with a locally-validated script.

Real evidence:
- `named-in-lum` = [75-81, 126, 187-197]: the marker `You are operating as the "<name>" custom subagent.` appears in `classification_context.last_user_message` on EVERY turn of every named-custom-subagent run. The probe run is records 187-197 (11 consecutive turns, 18:05-18:08 UTC), all `True`.
- Non-named subagent run = records 85-90: `generic-in-lum` True (`You are running as a subagent under a parent agent.`) but `named-in-lum` False -> such runs are correctly NOT pinned. Good discrimination.
- Parent turns = records 198, 199: neither marker in lum -> parent never pinned.
- `named-in-prompt` = False on all of 187-197 (only non-empty on 126/128-132, i.e. text that *quotes* the marker) -> confirms the plan requirement to scan the FULL message list, not the truncated 3500-char `prompt`/`classification_input.text`.

Verdict: discriminator valid under real multi-turn conditions. Phase 3 unblocked.

### Residual risk (monitored)
Literal-substring matching means a PARENT request that literally contains the marker text (e.g. a user message quoting this spec, or pasting routing-log output that includes the marker) would be pinned. Rec 126 is a case where the marker appears in both lum and prompt within a session that was discussing the feature. Accepted tradeoff per plan (no regex), but note it: consider a follow-up guard if false positives are observed in practice after deploy.

### Schema correction (handoff was stale)
Routing-log records have top-level `prompt`/`prompt_preview` plus a `classification_context` dict (`text`, `last_user_message`, `system_prompt`, `selected_turn_count`, `truncated`, `last_user_is_short_followup`). There is no top-level `last_user_message` or `context.*` key as the handoff described.

### Correction to non-named run identification

Records 85-90 are an **Ask-mode subagent** (their `last_user_message` begins `Ask mode is active…`), NOT a bare built-in. They carry the generic subagent reminder but no named-custom-subagent marker, so they are correctly not pinned. Scope note: the mechanism pins *named custom subagents* only; mode-subagents (Ask/Plan) emit no name marker and are out of scope. Only `web-researcher` has been observed as a named custom subagent (runs 75-81, 126, 187-197).

## Implementation notes (2026-10-03)

Branch `task/TASK-28` (from main `823b340`). Commit `a868111`.

Delivered `subagent_routes` exactly per the amended plan: `SubagentRoute` model (extra=forbid, blank name/model rejected, `resolved_signature` with `{name}` substitution, `resolved_require`), `OptiproxaiConfig.subagent_routes`, load-time validator (provider resolution + duplicate resolved-signature rejection), and `router.py` `_iter_message_content` / `_detect_subagent_route` / `_build_subagent_decision` with the hook placed after `build_classification_input` and before the scorer, guarded by a non-empty-routes check.

Deviations from the original plan: the compound `require` marker (the approved amendment) and the added `_iter_message_content` helper (keeps detector readable; concatenates list text parts so signature+require may span parts). No `deploy/config.vps.yaml` change — that remains the separate user-driven deploy step.

Gates (all green): ruff check, ruff format --check (42 files), pyright 0 errors, `pytest` 516 passed, `uv build` ok.

Local dry-run: marker message -> pinned `hy3-retrieval`/`retrieval`/reasoning `high`; plain message -> normal `model-medium`/`openrouter`.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added opt-in `subagent_routes` config that pins a matched named custom subagent to an explicit provider+model, bypassing classifier tier logic. Detection is a literal full-message-list scan (never a regex) requiring both a per-entry `signature` (default: Cursor's named-subagent line, `{name}`-substituted) and a compound `require` marker (default: Cursor's generic subagent reminder; `""` disables). On match the decision is pinned with a `subagent_pin` signal, empty fallbacks, and `score`/`confidence` 1.0; an empty `subagent_routes` is a strict no-op so parent routing is byte-for-byte unchanged. Files: `config.py` (model + validator), `router.py` (detector + pinned decision + hook), `tests/test_subagent_routing.py` (20 tests), `config.example.yaml`, `README.md`. Full CI gate green; 516 tests pass.
<!-- SECTION:FINAL_SUMMARY:END -->
