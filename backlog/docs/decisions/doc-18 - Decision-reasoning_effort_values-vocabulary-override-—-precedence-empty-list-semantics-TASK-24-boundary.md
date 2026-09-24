---
id: doc-18
title: >-
  Decision: reasoning_effort_values vocabulary override — precedence, empty-list
  semantics, TASK-24 boundary
type: other
created_date: '2026-09-24 13:07'
---
# Decision: reasoning_effort_values vocabulary override

**Date**: 2026-09-24
**Status**: Proposed (pending TASK-25 plan review)
**Task**: TASK-25

## Context
`_normalize_reasoning_effort` maps an effort label against a hard-coded per-`reasoning_style` allow-list. Because the style is shared across providers, Doubleword's broader vocabulary (`none, minimal, low, medium, high, xhigh, max`) is unreachable: the `xai` style caps at `{none, low, medium, high}` and silently coerces `max`/`xhigh` to `high`. The vocabulary is provider-specific (and can differ per model), so a style-fixed allow-list cannot express it.

## Decision
1. **Two new optional fields**, both `list[str] | None = None`:
   - `ProviderConfig.reasoning_effort_values` (config.py)
   - `ModelRuleEntry.reasoning_effort_values` (config.py)
2. **Resolution precedence**: best-matching `ModelRuleEntry` > `ProviderConfig` > style default. Model-rule matching reuses the existing prefix/provider scoring used by `_get_model_reasoning_style` (provider-specific beats prefix specificity).
3. **`None` vs `[]` is a strict three-state distinction**:
   - `None` (unset) -> fall back to the style default allow-list (current behaviour unchanged).
   - non-empty list -> that list is the allow-list; a value present passes through unchanged (so `max`/`xhigh` reach Doubleword); a value absent keeps the existing coercion (`xhigh`/`max` -> `high`, else `medium`).
   - `[]` (empty list) -> suppress reasoning-control injection entirely, documented as equivalent to a `none` style.
4. **Allow-list comparison is case/whitespace-normalised** (`strip().lower()` per entry) so config casing never causes a spurious miss; the emitted value is already lower-cased by `_normalize_reasoning_effort`.
5. **TASK-24 boundary**: TASK-25 owns the per-candidate *vocabulary* (allowed set); TASK-24 owns the per-candidate effort *value* (`ModelEntry.reasoning_effort`). They are separate code paths at different layers — TASK-25 adds no `ModelEntry` field and TASK-24 adds no vocabulary. They compose: TASK-24's future value would be normalised against TASK-25's resolved vocabulary. No folding of one into the other.

## Rationale
- A `[]` sentinel is the only way to express "this provider/model must never receive a reasoning control" without overloading `None` (whose job is "inherit").
- Doing resolution at injection time (model + provider + config all in hand) avoids threading a new field through `RoutingDecision` and keeps the change inside `config.py` + `proxy.py`.
- Keeping the vocabulary per provider/rule (not per style) is what actually unblocks Doubleword while leaving every other provider regression-free.

## Alternatives rejected
- **Widen the `xai` style allow-list** — wrong: would send `max`/`xhigh` to genuine xAI endpoints that reject them.
- **Per-`ModelEntry` vocabulary** — not needed for TASK-25; provider/rule granularity covers the case and a candidate's provider determines the vocabulary.
- **Overload `None` for suppression** — removes the ability to inherit the style default.
