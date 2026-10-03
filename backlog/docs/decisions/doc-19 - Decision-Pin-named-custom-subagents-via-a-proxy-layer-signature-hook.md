---
id: doc-19
title: Decision - Pin named custom subagents via a proxy-layer signature hook
type: other
created_date: '2026-10-03 17:44'
updated_date: '2026-10-03 18:31'
---
# Decision: Pin named custom subagents via a proxy-layer signature hook

- **Date:** 2026-10-03
- **Task:** TASK-28
- **Status:** Proposed (amended 2026-10-03 after Plannotator review; amended again 2026-10-03 for compound signature)

## Context

Under BYOK, Cursor pins a subagent's conversation to the parent conversation's model by design, and custom/BYOK model IDs are deliberately excluded from explicit subagent model selection. A custom subagent (e.g. `web-researcher`) therefore cannot be pinned to a different model than the parent from Cursor's own configuration. Requests from parent and subagent are distinguishable at the proxy only by message content: Cursor injects the line `You are operating as the "<name>" custom subagent.` into the subagent's message content. The `system_prompt` is identical to the parent's and cannot be used. The optiproxai proxy sits in front of every request and cannot be bypassed by Cursor's pinning, so it is the only layer where per-subagent routing can be decided.

## Decision

Add a config-declared `subagent_routes` list. Each entry binds a subagent identity to an explicit **provider + model** pin AND declares the literal request marker ("signature") that identifies that subagent: `signature` defaults to Cursor's injected named-custom-subagent line (a template with an optional `{name}` placeholder) and may be overridden per entry with any literal string, so the mechanism is not tied to one client's wording. At the start of `Router.route()`, before classification, the full request message list is scanned in config order for each configured signature; on the first match the router returns a `RoutingDecision` pinned to that provider/model (with the entry's `reasoning_effort`), bypassing the scorer entirely so no tier classification can second-guess it.

### Amendment: compound signature (2026-10-03)

A match requires **two** literals to be present in the same message text: the per-entry `signature` (named marker) AND a second `require` literal, which defaults to Cursor's generic subagent reminder `You are running as a subagent under a parent agent.` The match rule is `signature in text and (require == "" or require in text)`. Setting `require` to empty/null disables it and falls back to single-signature behaviour.

Why: the named marker alone is plain text, so a parent chat that quotes the spec (this very session does) or pastes routing-log output would be falsely pinned. The generic reminder is injected on every real subagent turn and is absent from such quotes, so the AND removes the common false positive without losing any real subagent turn (verified against the live log: every named-subagent run also carries the generic reminder).

## Rationale

- The signature is data, not code: a route can match Cursor's line (default) or any other harness's injected marker, so one configurable mechanism covers named custom subagents generally and is usable more widely than a single client's wording.
- The mechanism only ties a matched subagent session to a model+provider. Parent requests never carry the signature, so parent routing is completely unaffected — the subagent is a separate session and the pin is scoped to it.
- Pinning model/provider (not merely a tier) is required because a tier pin would still let the classifier and tier candidate lists choose a model from that tier, whereas the requirement is one specific retrieval-strong model.
- Detection scans the raw message list rather than `classification_input.text`: the classification text keeps only the last 3500 characters, which would lose a marker sitting at the start of a long conversation (confirmed empirically: the named marker is present in `classification_context.last_user_message` on every subagent turn but absent from the truncated `prompt`).
- Keeping detection and pinning in `router.py` plus schema/validation in `config.py` matches the existing CLI -> config/router -> scorer architecture and keeps the change bounded.
- A config with no `subagent_routes` entries must leave routing byte-for-byte unchanged, so the feature is strictly opt-in.

## Consequences

- New top-level config key `subagent_routes` (list of `{name, signature?, require?, provider?, model, reasoning_effort?}`); `provider` is validated against the configured `providers` map at load time (blank resolves to `default_provider`); duplicate resolved signatures are rejected.
- The matching decision reports a `subagent_pin` signal; `tier` is an observability label (default `MEDIUM`) since the classifier did not run, and fallbacks are empty because substitution is exactly what the pin prevents.
- Signature matching is literal substring matching (no user-supplied regex): message bodies can be megabytes (base64 image parts), so a config-supplied regex would be a performance/ReDoS hazard.
- The compound `require` guard removes the common parent-quote false positive. Residual risk (accepted, explicitly deferred): a parent message pasting a full log line containing BOTH strings can still false-positive; a first-message anchor was considered but not adopted (compound-only chosen at review).
- Scope: the mechanism pins named custom subagents only. Cursor mode-subagents (Ask/Plan) emit the generic reminder but no name marker, so they are neither pinned nor matched — verified in the live log (Ask-mode run 85-90).
- The pinned target must exist in the active deploy config. Hy3 is already present in the live VPS config (user-confirmed 2026-10-03); the repo copy of `deploy/config.vps.yaml` must be checked for drift when wiring the route.
- Discriminator persistence: the pre-start multi-turn probe against the live routing log PASSED (named marker present on all 11 turns of a `web-researcher` run, absent on parent turns, absent from the truncated `prompt`); only then is the design committed to.
