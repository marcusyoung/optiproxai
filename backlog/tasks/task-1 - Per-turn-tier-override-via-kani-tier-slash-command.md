---
id: TASK-1
title: 'Per-turn tier override via /optiproxai:<tier> slash command'
status: Done
assignee:
  - dev
created_date: '2026-08-17 12:00'
updated_date: '2026-08-17 20:06'
labels: []
dependencies: []
priority: medium
type: feature
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
OptiProxAI LLM routing proxy: allow a user to force the routing tier for a single turn (e.g. force REASONING for a prompt the scorer would classify lower) by starting the latest user message with /optiproxai:<tier> (e.g. /optiproxai:reasoning, /optiproxai:simple, /optiproxai:medium, /optiproxai:complex).

Context: optiproxai routes prompts through a scorer that classifies complexity into tiers (SIMPLE/MEDIUM/COMPLEX/REASONING) and picks a model per profile tier config. There is currently no way for a client to override the tier for a specific turn. A per-message command is the right mechanism because the override lives in the message itself, works from any client (chat UIs, CLI), and is naturally per-turn — no sticky state to forget to reset.

Decisions already made with the user:
- Syntax: /optiproxai:<tier> (colon-delimited), case-insensitive tier matching.
- The token must be stripped from the latest user message before forwarding upstream so the model never sees it.
- Parse/override must live in the Router (route() gains an override that skips the scorer and pins the tier) so the chat completions proxy, `optiproxai route` CLI command, and /v1/route debug endpoint all honor it.
- Invalid tier values must not crash routing: warn and fall through to normal scorer behavior (or reject with a clear error at the HTTP boundary — plan phase decides).
- Only the latest user message is scanned (a /optiproxai:... token in assistant/history content must NOT trigger an override).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A /optiproxai:<tier> token at the start of the latest user message forces that tier for the request in the chat completions proxy
- [x] #2 The /optiproxai:<tier> token is stripped from the message text before forwarding upstream
- [x] #3 The override is honored by `optiproxai route` CLI and the /v1/route debug endpoint
- [x] #4 An invalid tier value does not crash routing and degrades gracefully (warn + normal scoring, or clear error at HTTP boundary)
- [x] #5 A /optiproxai:... token in assistant/history messages does not trigger an override
- [x] #6 Tests cover: valid overrides per tier, case-insensitivity, stripping, invalid tier handling, history-token non-triggering
- [x] #7 README documents the /optiproxai:<tier> feature
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
# Implementation Plan: Per-turn tier override via /optiproxai:<tier>

## Approach

A shared helper `parse_tier_override(messages)` extracts `(tier_override, stripped_messages)` from the latest user message. The helper lives in `router.py` (already imported by both proxy and CLI). `Router.route()` gains a `tier_override: str | None = None` parameter — when set and valid, it skips the scorer and pins the tier. The proxy calls the helper before routing, passes `tier_override` to `route()`, and replaces `body["messages"]` with the stripped messages before compaction and upstream forwarding. The CLI and debug endpoint call the helper and pass the override to `route()` but do not strip (no upstream forwarding).

## Token matching rule

- Regex: `^/optiproxai:(\w+)\s*` at the start of the latest user message content.
- `<word>` is matched case-insensitively against `_TIER_ORDER` (SIMPLE, MEDIUM, COMPLEX, REASONING).
- Valid tier: `tier_override` set to uppercase tier name, token stripped from content.
- Invalid tier (`/optiproxai:foo ...`): `tier_override = None`, token still stripped, warning logged. Falls through to normal scoring.
- No `/optiproxai:` prefix: `tier_override = None`, no stripping.
- Only the latest user message is scanned. Tokens in assistant/history/system messages are ignored.

## Content handling

- **String content**: strip the token and leading whitespace from the string.
- **List content**: find the first `{"type": "text", "text": ...}` part. If it starts with the token, strip from that part. If the first text part doesn't start with the token, no override (does not scan later parts).
- Empty/stripped content: if stripping leaves an empty string, keep it as `""` (do not remove the message — it's still a valid user turn).

## Invalid tier behavior

Warn + normal scoring (acceptance criterion #4, option chosen over HTTP error). The token is still stripped so it doesn't leak to the upstream model. The warning is logged at `log.warning` level.

## Files to modify

### 1. `src/optiproxai/router.py`

- Add `parse_tier_override(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]`:
  - Finds the latest user message (iterate messages in reverse, first with `role == "user"`).
  - Normalizes content (handles string and list of parts).
  - Applies the regex match.
  - Returns `(tier_override, stripped_messages)`. `stripped_messages` is the original list if no match, or a shallow copy with the latest user message content replaced if stripped. The messages list is shallow-copied; only the latest user message dict is deep-copied (content replaced).
  - Invalid tier: returns `(None, stripped_messages)` — token stripped, override None.
- Modify `Router.route()` signature: add `tier_override: str | None = None` keyword parameter.
  - If `tier_override` is not None and `tier_override.upper() in _TIER_ORDER`: set `tier = tier_override.upper()`, skip `_classify()` call (set score=1.0, confidence=1.0, signals=["tier_override"], agentic_score=0.0). Skip the agentic-profile tier bump logic.
  - If `tier_override` is not None but invalid: log warning, fall through to normal scoring.
  - If `tier_override` is None: normal scoring path (unchanged).

### 2. `src/optiproxai/proxy.py`

- In `chat_completions()` (after messages validation, before `router.route()` call ~line 1902):
  - Call `parse_tier_override(messages)` → `(tier_override, stripped_messages)`.
  - If `tier_override is not None` or `stripped_messages is not messages`: set `body = dict(body)` and `body["messages"] = stripped_messages` (strip before compaction so token doesn't leak into summaries).
  - Pass `tier_override=tier_override` to `state.router.route()`.
  - Log the override: `logger.info("TIER_OVERRIDE request_id=%s tier_override=%s", request_id, tier_override)`.
- In `route_debug()` (~line 2257): call `parse_tier_override(messages)`, pass `tier_override` to `route()`. Include `tier_override` in the debug payload.

### 3. `src/optiproxai/cli.py`

- In `route_cmd()` (~line 296): call `parse_tier_override(messages)` on the constructed messages list, pass `tier_override` to `router.route()`. No stripping needed (CLI doesn't forward upstream).

### 4. `tests/test_tier_override.py` (new)

Test classes:

- `TestParseTierOverride`: unit tests for the helper.
  - Valid override per tier (SIMPLE, MEDIUM, COMPLEX, REASONING).
  - Case-insensitivity (`/optiproxai:reasoning`, `/optiproxai:REASONING`, `/optiproxai:Reasoning`).
  - Token stripped from string content.
  - Token stripped from list content (first text part).
  - Invalid tier: `tier_override = None`, token still stripped.
  - No `/optiproxai:` prefix: `(None, original_messages)`.
  - Token in assistant message: no override, no stripping.
  - Token in earlier user message (not latest): no override, no stripping.
  - Empty content after stripping: `""` preserved.

- `TestRouterTierOverride`: integration tests for `Router.route()` with `tier_override`.
  - Override forces the tier (verify `decision.tier == override`).
  - Override skips scorer (mock `_classify`, assert it's not called when override is valid).
  - Invalid override falls through to scorer (mock `_classify`, assert it IS called).
  - Override with `required_capabilities` still works (escalation path respected).

- `TestProxyTierOverride`: tests for the proxy endpoint using `TestClient`.
  - Valid override in chat completions: verify routing decision uses overridden tier, verify token stripped from upstream body (mock `_proxy_upstream` or `_try_with_fallbacks`).
  - Invalid override: normal routing, token stripped.
  - Token in history: no override, no stripping.
  - Debug endpoint: override reflected in response payload.

- `TestCliTierOverride`: test for `route` CLI command.
  - `/optiproxai:reasoning` in prompt: routing decision shows REASONING tier.

### 5. `README.md`

Add a section after the "Usage" section titled "### Per-turn tier override" documenting:
- Syntax: `/optiproxai:<tier>` at the start of the latest user message.
- Valid tiers: `simple`, `medium`, `complex`, `reasoning` (case-insensitive).
- The token is stripped before forwarding upstream.
- Example curl command.
- Example Python client usage.

## Constraints and risks

- **Compaction interaction**: the token must be stripped before `_resolve_compaction` runs, otherwise the token could leak into compaction summaries. The plan handles this by stripping before the compaction call.
- **Content part edge cases**: list content where the first text part is empty or the token spans parts. The helper only scans the first text part — if it doesn't start with the token, no override. This is a deliberate simplification.
- **RoutingDecision fields**: when the scorer is skipped, `score`, `confidence`, and `signals` are synthetic. `score=1.0`, `confidence=1.0`, `signals=["tier_override"]` makes the override visible in logs and headers without misleading downstream consumers.
- **`resolve_model()` is not affected**: compaction's internal model resolution does not use `route()` and does not need override support.

## Open questions

None remaining. All design decisions were made during Phase 0 and confirmed above.

## Task Manifest

| # | Title | Files | Depends On | Labels | Acceptance Criterion |
|---|---|---|---|---|---|
| 1 | Implement /optiproxai:<tier> override: helper + router + proxy + CLI + tests + docs | src/optiproxai/router.py, src/optiproxai/proxy.py, src/optiproxai/cli.py, tests/test_tier_override.py, README.md | — | logic, test, docs | All 7 acceptance criteria pass: override forces tier in proxy, token stripped, CLI + debug endpoint honor it, invalid tier degrades gracefully, history tokens ignored, tests pass, README documents feature |
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
TASK-1 complete: per-turn tier override via /optiproxai:<tier> slash command. All 4 subtasks done (TASK-1.1 router helper + route() override, TASK-1.2 proxy integration, TASK-1.3 CLI integration, TASK-1.4 README + OpenSpec docs). Merged to main via PR #5 (marcusyoung/optiproxai, merge commit 76cab60). 44 tier-override tests pass; full suite 388 passed with 1 pre-existing unrelated failure; ruff/pyright clean. Manually verified: proxy request pinned COMPLEX (was SIMPLE), CLI route shows tier: COMPLEX. Copilot review clean after addressing deepcopy + mock-style comments. OpenSpec change proposal 2026-08-17-tier-override created with routing + proxy-api spec deltas. Also added 4 opencode commands (.opencode/command/optiproxai-{simple,medium,complex,reasoning}.md) for tier switching from chat UI.
<!-- SECTION:FINAL_SUMMARY:END -->
