---
id: TASK-3
title: 'Spike: input-limit escalation to higher tiers'
status: To Do
assignee: []
created_date: '2026-08-18 00:42'
labels:
  - router
  - routing
  - research
  - config
dependencies: []
references:
  - src/optiproxai/router.py
  - src/optiproxai/proxy.py
  - src/optiproxai/config.py
priority: medium
type: spike
ordinal: 7000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Context

Currently, `Router.route()` escalates to higher tiers only when required *capabilities* (vision, tools, json_mode) are not satisfied by any candidate in the selected tier (router.py lines 346-378). There is no equivalent escalation for *input-limit* failures — when every candidate in the tier has `max_input_tokens` configured and the estimated prompt tokens exceed all of them, the router raises `InputLimitNotSatisfiedError` → 400 `input_limit_not_satisfied` (proxy.py lines 1942-1951).

All models in the current production config declare `max_input_tokens`, so this path is reachable in practice, not just theoretical.

## Spike question

Should the router escalate to higher tiers on input-limit failure before returning a 400, mirroring the existing capability-escalation pattern?

## Considerations

- **Consistency with capability escalation**: The capability path already walks `_escalation_path` (MEDIUM → COMPLEX → REASONING for a SIMPLE tier) and picks the first tier with an eligible candidate. Input-limit escalation could reuse the same loop with a different eligibility predicate.
- **Higher tiers tend to have larger context windows**: In practice, COMPLEX and REASONING models often accept more input tokens than SIMPLE/MEDIUM models, making escalation a natural fit.
- **Cost implications**: Escalating to a higher tier means a more expensive model. The user (or override) asked for a lower tier; silently upgrading changes the cost profile. Should this be opt-in via config?
- **Tier override interaction**: If a user pins `/optiproxai:simple` and the prompt is too long for all SIMPLE models, should the router escalate (ignoring the pin) or respect the pin and fail? The capability path escalates even under override — should input-limit match that?
- **Graceful degradation vs loud failure**: The current 400 is loud and clear. Escalation would be silent unless logged/headers expose it. Is that trade-off worth it?
- **Alternative approaches**: 
  1. Escalate automatically (mirror capability path)
  2. Config flag: `input_limit_escalation: true/false` (default false to preserve current behavior)
  3. Return the over-limit prompt to the lowest-capable tier anyway and let the upstream provider reject it (pushes the error upstream)
  4. Hybrid: escalate but add a response header `X-Optiproxai-Input-Limit-Escalated: true`

## Deliverable

A recommendation with rationale, posted as a decision record under `docs/decisions/`. If the recommendation is to implement, follow up with a feature task referencing this spike.

## References

- `src/optiproxai/router.py` — `_escalation_path` (line 801), input-limit filtering (line 733), `InputLimitNotSatisfiedError` raise (line 431)
- `src/optiproxai/proxy.py` — 400 error handling (lines 1942-1951)
- `src/optiproxai/config.py` — `ModelEntry.max_input_tokens` (line 98)
- Existing capability escalation pattern (router.py lines 346-378)
- Decision records: `decisions/invalid-tier-warn-vs-error`, `decisions/tier-override-token-position`
<!-- SECTION:DESCRIPTION:END -->
