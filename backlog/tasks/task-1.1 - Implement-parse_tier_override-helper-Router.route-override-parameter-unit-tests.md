---
id: TASK-1.1
title: >-
  Implement parse_tier_override helper + Router.route() override parameter +
  unit tests
status: Done
assignee: []
created_date: '2026-08-17 12:25'
updated_date: '2026-08-17 13:01'
labels:
  - logic
  - test
dependencies: []
references:
  - src/optiproxai/router.py
  - tests/test_tier_override.py
parent_task_id: TASK-1
priority: high
type: feature
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add the core tier override parsing logic to router.py and the Router.route() override parameter that skips the scorer when a valid tier is forced.

## What to implement

### parse_tier_override helper (router.py)
Add `parse_tier_override(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]`:
- Finds the latest user message (iterate messages in reverse, first with `role == "user"`).
- Normalizes content (handles string and list of parts).
- Applies regex `^/optiproxai:(\w+)\s*` at the start of the content.
- Valid tier (case-insensitive match against `_TIER_ORDER`: SIMPLE, MEDIUM, COMPLEX, REASONING): returns `(tier_override_upper, stripped_messages)`. Token and leading whitespace stripped from content.
- Invalid tier: returns `(None, stripped_messages)` — token still stripped, override None. `log.warning` emitted.
- No `/optiproxai:` prefix: returns `(None, original_messages)` — no stripping.
- Only the latest user message is scanned. Tokens in assistant/history/system messages are ignored.
- For list content: find the first `{"type": "text", "text": ...}` part. If it starts with the token, strip from that part. If the first text part doesn't start with the token, no override. Does not scan later parts.
- Empty content after stripping: keep as `""` (do not remove the message).
- `stripped_messages` is a shallow copy of the list; only the latest user message dict is deep-copied (content replaced). If no match, return the original list unchanged.

### Router.route() override parameter (router.py)
Modify `Router.route()` signature: add `tier_override: str | None = None` keyword parameter.
- If `tier_override` is not None and `tier_override.upper() in _TIER_ORDER`: set `tier = tier_override.upper()`, skip `_classify()` call. Set synthetic fields: `score=1.0`, `confidence=1.0`, `signals=["tier_override"]`, `agentic_score=0.0`. Skip the agentic-profile tier bump logic.
- If `tier_override` is not None but invalid (not in `_TIER_ORDER`): log warning, fall through to normal scoring.
- If `tier_override` is None: normal scoring path (unchanged).

## Files affected
- `src/optiproxai/router.py` — add `parse_tier_override` function, modify `Router.route()` signature and logic
- `tests/test_tier_override.py` — create new test file with `TestParseTierOverride` and `TestRouterTierOverride` classes

## Risks and constraints
- Do not deep-copy the entire messages list — only shallow-copy the list and deep-copy the latest user message dict. Performance matters for large conversation histories.
- The `_TIER_ORDER` constant already exists in router.py — use it for validation, do not duplicate tier names.
- The regex must use `\w+` for the tier name capture group — this matches alphanumeric and underscore, which is broader than the valid tiers, but validation against `_TIER_ORDER` handles the filtering.
- When the scorer is skipped, the synthetic RoutingDecision fields must still be valid for downstream consumers (response headers, logs).

## Output contract
This task provides the `parse_tier_override` function and the `Router.route(tier_override=...)` parameter that subsequent tasks (proxy integration, CLI integration) will call. The function and parameter must be importable and usable without modification by downstream tasks.

## Decision records
- `decisions/invalid-tier-warn-vs-error` — invalid tier behavior: warn + normal scoring, token still stripped
- `decisions/tier-override-token-position` — token must be at position 0 of latest user message content
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 parse_tier_override extracts a valid tier (SIMPLE, MEDIUM, COMPLEX, REASONING) from the start of the latest user message with string content
- [x] #2 parse_tier_override handles list content: token must be at the start of the first {"type": "text", "text": ...} part
- [x] #3 The /optiproxai:<tier> token and leading whitespace are stripped from the returned messages
- [x] #4 Tier matching is case-insensitive (/optiproxai:reasoning, /optiproxai:REASONING, /optiproxai:Reasoning all work)
- [x] #5 Invalid tier (/optiproxai:foo) returns tier_override=None with the token still stripped from messages
- [x] #6 No /optiproxai: prefix returns (None, original_messages) with no modification
- [x] #7 A /optiproxai:... token in assistant or history messages does not trigger an override or stripping
- [x] #8 A /optiproxai:... token in an earlier (non-latest) user message does not trigger an override
- [x] #9 Empty content after stripping preserves an empty string "" (message is not removed)
- [x] #10 Router.route() with a valid tier_override skips the scorer and pins the tier (decision.tier == override)
- [x] #11 Router.route() with an invalid tier_override logs a warning and falls through to normal scoring
- [x] #12 All tests in TestParseTierOverride and TestRouterTierOverride pass
- [x] #13 uv run pytest tests/test_tier_override.py -q passes
- [x] #14 uv run ruff check src/optiproxai/router.py passes
- [x] #15 uv run pyright src/optiproxai/router.py passes
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
### Implementation notes (2026-08-17)

- Added `parse_tier_override()` to `src/optiproxai/router.py`: scans latest user message only, regex `^/optiproxai:(\w+)\s*`, case-insensitive tier match vs `_TIER_ORDER`, strips token on valid AND invalid tiers (invalid = warn + override None per doc-1), position-0 only per doc-2, shallow-copies list + deep-copies only the latest user message dict.

- Added `tier_override` kwarg to `Router.route()`: valid override pins tier and skips scorer with synthetic fields (score=1.0, confidence=1.0, signals=['tier_override'], agentic_score=0.0); skips agentic tier bump; invalid override warns and falls through to scorer.

- Created `tests/test_tier_override.py` with TestParseTierOverride (14 tests: valid tiers, list content, case-insensitivity, stripping, invalid-tier, no-prefix, assistant/history non-triggering, empty-content) and TestRouterTierOverride (11 tests: pinning, scorer skip via mock, invalid fallthrough, capability escalation).

- Verification: 25/25 new tests pass; ruff check + format pass on both files; pyright clean. Full suite: 370 passed, 1 pre-existing failure in test_agentic_training_script (reproduces on base commit without changes — unrelated).

- STAGED (not committed) on branch task/TASK-1.1: permission rule denies git commit/push. Pending: user commit, merge into task/TASK-1, then finalSummary + Done.

- Iterations: 1
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Implemented the core tier override logic in src/optiproxai/router.py:

- parse_tier_override(messages) -> (tier_override, stripped_messages): scans only the latest user message, matches ^/optiproxai:(\w+)\s* at position 0 (string or first text part of list content), validates case-insensitively against _TIER_ORDER. Valid tiers return the upper-cased override with the token stripped; invalid tiers warn and strip but return None (decision doc-1); no token returns the original list unchanged. The list is shallow-copied and only the latest user message dict is deep-copied.
- Router.route() gains tier_override kwarg: valid overrides pin the tier and skip the scorer with synthetic fields (score=1.0, confidence=1.0, signals=['tier_override'], agentic_score=0.0) and skip the agentic tier bump; invalid overrides warn and fall through to normal scoring.

Tests: tests/test_tier_override.py with TestParseTierOverride (14 tests) and TestRouterTierOverride (11 tests) — 25/25 pass. ruff check + format pass on both files; pyright clean. Full suite: 370 passed, 1 pre-existing failure in test_agentic_training_script (reproduces on base commit, unrelated).

Committed as 20c4f95 on task/TASK-1.1, fast-forwarded into task/TASK-1. No deviations from the plan.
<!-- SECTION:FINAL_SUMMARY:END -->
