---
id: TASK-1.4
title: 'Document /optiproxai:<tier> feature in README'
status: Done
assignee: []
created_date: '2026-08-17 12:25'
updated_date: '2026-08-17 18:45'
labels:
  - docs
dependencies:
  - TASK-1.1
references:
  - README.md
modified_files:
  - README.md
  - openspec/changes/2026-08-17-tier-override/proposal.md
  - openspec/changes/2026-08-17-tier-override/specs/routing/spec.md
  - openspec/changes/2026-08-17-tier-override/specs/proxy-api/spec.md
  - openspec/changes/2026-08-17-tier-override/tasks.md
parent_task_id: TASK-1
priority: low
type: docs
ordinal: 5000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Document the `/optiproxai:<tier>` per-turn tier override feature in the README.

## What to implement

### README.md
Add a new section after the "Usage" section titled "### Per-turn tier override" documenting:
- Syntax: `/optiproxai:<tier>` at the start of the latest user message.
- Valid tiers: `simple`, `medium`, `complex`, `reasoning` (case-insensitive).
- The token is stripped before forwarding upstream.
- Example curl command showing a `/optiproxai:reasoning` request.
- Example Python client usage with the override token.
- Note that invalid tier values fall through to normal scoring (the token is still stripped).

## Files affected
- `README.md` — add a new section

## Dependencies
- TASK-1.1 must be complete so the feature behavior is finalized and documentation is accurate.

## Output contract
README documents the feature end-to-end. Users can discover the override syntax and valid tier names from the README alone.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 README has a new section titled "Per-turn tier override" documenting the /optiproxai:<tier> feature
- [x] #2 Syntax is documented: /optiproxai:<tier> at the start of the latest user message
- [x] #3 All four valid tiers are listed: simple, medium, complex, reasoning (case-insensitive)
- [x] #4 Documentation states the token is stripped before forwarding upstream
- [x] #5 At least one example curl command is included
- [x] #6 At least one example Python client usage is included
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
TASK-1.4 complete. Added README section 'Per-turn tier override' (lines 135-193) documenting syntax, valid tiers, stripping behavior, invalid-tier handling, and 3 examples (curl, Python client, /v1/route debug). Created OpenSpec change proposal 2026-08-17-tier-override with: (1) proposal.md covering problem, solution, syntax, routing behavior, backward compatibility, affected specs, and test plan; (2) specs/routing/spec.md delta with 12 scenarios covering valid override pinning, all four tiers, case-insensitivity, invalid tier warning+fallback, position-0 requirement, history/assistant non-triggering, upstream stripping, compaction stripping, multimodal list content (first text part only), empty content preservation, capability filtering respect, and tier fallback respect; (3) specs/proxy-api/spec.md delta with 5 scenarios covering chat completions proxy, debug endpoint, CLI route command, invalid override graceful fallback, and compaction stripping; (4) tasks.md linking all 4 subtasks. Quality gates: ruff check pass, ruff format pass, pyright clean, 44/44 tier_override tests pass. Note: git commit blocked by permission rule — user needs to commit manually.
<!-- SECTION:FINAL_SUMMARY:END -->
