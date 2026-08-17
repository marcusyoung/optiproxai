---
id: TASK-1.3
title: 'Integrate tier override into CLI: route command + tests'
status: Done
assignee: []
created_date: '2026-08-17 12:25'
updated_date: '2026-08-17 18:31'
labels:
  - logic
  - test
dependencies:
  - TASK-1.1
references:
  - src/optiproxai/cli.py
  - tests/test_tier_override.py
parent_task_id: TASK-1
priority: medium
type: feature
ordinal: 4000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Wire the tier override helper into the `optiproxai route` CLI command so the override is honored when routing prompts from the command line.

## What to implement

### route_cmd() (cli.py, ~line 296)
- Call `parse_tier_override(messages)` on the constructed messages list.
- Pass `tier_override` to `router.route()`.
- No stripping needed — the CLI does not forward upstream, so the token staying in the prompt text is harmless. The override value is what matters.

## Files affected
- `src/optiproxai/cli.py` — modify `route_cmd()`
- `tests/test_tier_override.py` — add `TestCliTierOverride` class

## Risks and constraints
- The CLI constructs a messages list from the prompt argument. parse_tier_override must be called after the messages list is built but before `router.route()` is called.
- The CLI output should reflect the overridden tier in the routing decision display.
- Use Click's CliRunner or unittest.mock for testing the CLI command.

## Dependencies
- TASK-1.1 must be complete: `parse_tier_override` and `Router.route(tier_override=...)` must be importable from `optiproxai.router`.

## Output contract
After this task, `optiproxai route "/optiproxai:reasoning explain quantum computing"` forces REASONING tier and displays it in the routing output.

## Decision records
- `decisions/invalid-tier-warn-vs-error` — invalid tier behavior in CLI context
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 route_cmd() calls parse_tier_override on the constructed messages list
- [x] #2 route_cmd() passes tier_override to router.route()
- [x] #3 A /optiproxai:reasoning prompt via `optiproxai route` shows REASONING as the selected tier in output
- [x] #4 A /optiproxai:simple prompt via `optiproxai route` shows SIMPLE as the selected tier
- [x] #5 An invalid /optiproxai:foo prompt via `optiproxai route` falls through to normal scoring (no crash)
- [x] #6 A prompt without /optiproxai: prefix via `optiproxai route` routes normally (unchanged behavior)
- [x] #7 All tests in TestCliTierOverride pass
- [x] #8 uv run pytest tests/test_tier_override.py::TestCliTierOverride -q passes
- [x] #9 uv run ruff check src/optiproxai/cli.py passes
- [x] #10 uv run pyright src/optiproxai/cli.py passes
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Implementation Notes (2026-08-17)

### Change
- `src/optiproxai/cli.py` `route_cmd()`: imported `parse_tier_override` alongside `Router`, called it on the constructed `messages` list, and passed `tier_override` to `router.route()`. No message stripping — the CLI does not forward upstream, so the token staying in the prompt text is harmless.

### Tests (TestCliTierOverride, 6 tests)
- `test_route_calls_parse_tier_override` — spies on `optiproxai.router.parse_tier_override` to confirm it is called (AC #1)
- `test_route_passes_tier_override_to_router` — patches `Router` constructor to return a real instance, then spies on the instance bound `route` method to verify `tier_override=REASONING` is passed (AC #2)
- `test_override_shows_tier_in_output[REASONING]` — JSON output has `"tier": "REASONING"` (AC #3)
- `test_override_shows_tier_in_output[SIMPLE]` — JSON output has `"tier": "SIMPLE"` (AC #4)
- `test_invalid_tier_falls_through_to_normal_scoring` — `/optiproxai:foo` exits 0 and tier is one of the four valid tiers (AC #5)
- `test_no_prefix_routes_normally` — plain prompt exits 0 and tier is valid (AC #6)

### Verification
- `uv run pytest tests/test_tier_override.py::TestCliTierOverride -q` -> 6 passed
- `uv run pytest tests/test_tier_override.py -q` -> 43 passed (all tier-override tests)
- `uv run ruff check src/optiproxai/cli.py` -> clean
- `uv run ruff format --check src/optiproxai/cli.py tests/test_tier_override.py` -> already formatted
- `uv run pyright src/optiproxai/cli.py` -> 0 errors, 0 warnings
- `uv run pytest tests/ -q` -> 388 passed, 1 pre-existing failure (test_agentic_training_script, unrelated)

### Blocker
- git commit denied by permission rule; changes are staged but uncommitted on `task/TASK-1.3`.

## Parity Fix (Copilot PR #4 review)

Copilot flagged that `route_cmd()` discarded the stripped messages list, so invalid `/optiproxai:foo` tokens would pollute scorer input — same issue fixed for `route_debug` in TASK-1.2.

### Fix
- `route_cmd()` now passes `stripped_messages` (not the original `messages`) to `router.route()`, matching the proxy endpoints.
- Added `test_route_routes_with_stripped_messages` — spies on the router instance to verify `/optiproxai:foo hello world` routes with content `"hello world"`.

### Updated verification
- `uv run pytest tests/test_tier_override.py -q` -> 44 passed (was 43)
- ruff + format + pyright clean
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Wired parse_tier_override into the `optiproxai route` CLI command. `route_cmd()` now imports `parse_tier_override` from `optiproxai.router`, calls it on the constructed messages list, and passes `tier_override` to `router.route()`. No message stripping (CLI does not forward upstream). Added `TestCliTierOverride` class with 6 tests covering: parse call verification, tier_override kwarg passing, REASONING/SIMPLE override in JSON output, invalid tier fallthrough to normal scoring, and unchanged behavior for plain prompts. All 10 ACs verified. 43/43 tier-override tests pass, ruff+format+pyright clean, full suite 388 passed (1 pre-existing unrelated failure). Changes staged but uncommitted — git commit permission denied by rule.
<!-- SECTION:FINAL_SUMMARY:END -->
