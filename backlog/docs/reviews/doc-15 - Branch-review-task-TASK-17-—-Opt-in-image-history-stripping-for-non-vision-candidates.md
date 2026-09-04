---
id: doc-15
title: >-
  Branch review: task/TASK-17 — Opt-in image-history stripping for non-vision
  candidates
type: other
created_date: '2026-09-04 13:35'
---
# Branch Review: `task/TASK-17` — Opt-in image-history stripping for non-vision candidates

**Reviewer:** Analyst (review-standards-branch)
**Date:** 2026-09-04
**Base:** `main`
**State:** Uncommitted changes on `task/TASK-17` (no commits diverge from `main`).

## Branch Summary

The branch adds an opt-in `smart_proxy.image_history_stripping` policy: a Pydantic config model (disabled by default, validated `image_ttl_turns > 0`), TTL-based softening of the `vision` requirement in capability detection, and a per-candidate sanitizer (`_strip_image_history_for_candidate`) that replaces aged-out `image_url` parts with a deterministic placeholder for candidates that do not declare `vision` in `model_rules`. It also updates README, config.example.yaml, three decision records (doc-12/13/14), and adds 18 tests in `TestImageHistoryStripping`. The implementation faithfully matches the revised doc-13 decision (turn-based TTL, latest-message protection, fail-closed, deterministic placeholder) and doc-14 (cache untouched). Full suite passes (421 tests), ruff/format/pyright clean. Overall assessment: correct, well-tested, well-documented; a few test-robustness and documentation-precision points below, none blocking.

## Findings

### `tests/test_proxy_reload.py`

**[Minor] [High] — Overbroad substring assertions on `str(prepared)`.** Several tests assert `"img-1" in str(prepared)` / `not in`. This also matches the JSON dump of *any* occurrence of that substring and wouldn't catch stripping that moved a URL into a different field. The precise content-equality assertions used in `test_strip_non_vision_candidate_replaces_aged_images` are much stronger. Suggest asserting on the exact reconstructed content lists for the "unchanged" cases too (e.g. `prepared == body` for the disabled/None/vision-capable cases).

**[Info] — Assertion comment mismatch in in-TTL test.** `test_strip_non_vision_candidate_replaces_aged_images` retains `img-2` at turn 2 but the comment says the case "is only relevant for fail-closed misconfigurations." In fact in-TTL retention is the designed guarantee whenever such a body passes through the sanitizer. No behavioral issue; the comment undersells the invariant being tested.

**[Info] — Missing edge-case coverage (non-blocking):** (a) placeholder `"[image omitted]"` when a message's *only* content part is an image and `placeholder=""` produces `content: ""` (string) — behavior is reasonable but untested; (b) `_detect_required_capabilities` TTL comparison uses message-index distance (`latest_user_idx - i`) rather than counting user turns — tests match implementation, but a history with multiple assistant/tool turns between user turns ages images faster than "3 user turns" as documented. If strict user-turn counting was intended (doc-13 says "within `image_ttl_turns` user turns"), this is a real semantic gap; verified: doc-13 line 32 says "(latest_user_idx - image_index) <= image_ttl_turns" — so index distance *is* the sanctioned semantics, but the prose ("user turns") and the formula disagree. Worth clarifying in doc-13/README.

### `src/optiproxai/proxy.py`

No Critical/Major findings. The detection call sites at both `chat_completions` and `route_debug` correctly thread `image_stripping_enabled`/`image_ttl_turns` from config; `_strip_image_history_for_candidate` runs before normalization and cache-control injection as documented; `_get_model_vision_capability` mirrors the `_get_model_content_part_policy` best-score pattern exactly (provider match outranks prefix length), consistent with router behavior for capability filtering (though filtered by a different mechanism — `_get_model_capabilities` unions all matching rules rather than best-score). That asymmetry is worth noting:

**[Minor] [Medium] — Detection/stripping use different capability-resolution semantics than the router's filter.** `Router._get_model_capabilities` (router.py:638-655) *unions* capabilities across all matching rules, while `_get_model_vision_capability` takes the single best-scoring rule. If a model has two matching rules — e.g. generic `prefix="cx/"` with `vision` and more-specific `prefix="cx/vision-pro", provider="dummy"` without `vision` — the router sees the model as vision-capable (union) but the strip helper sees it as non-vision (best-score). In that scenario the request could route to the model as vision-capable, then the sanitizer would strip its history images while detection still required vision. It doesn't break correctness of the actual payload (the model still gets placeholders, and providers that require vision would 400 — same failure mode as the fail-closed design guards). Recommend one of: reuse the union semantics in `_get_model_vision_capability`, or document that stripping intent overrides the union. Confidence Medium: verified code paths are as described; intent from doc-13 is "same best-score pattern as `_get_model_content_part_policy`" so this may be deliberate, but the interaction with router union filtering is unexamined in the docs.

### `src/optiproxai/config.py`, `config.example.yaml`, `README.md`

No findings. Pydantic validation (`gt=0`), defaults, docstrings, README semantics/caveats, and example YAML all match each other and the decision docs.

## Cross-cutting Findings

- **Uncommitted work:** everything for this task lives in the working tree (5 modified, 3 new decision docs); `git log main...HEAD --oneline` is empty. The branch's content exists only as uncommitted changes — committing is required before any push/PR.
- The acceptance criteria in task-17 (TTL detection, disabled bit-identity, per-candidate sanitizer, placeholder, vision-candidate pass-through, ordering, log line, config validation, test coverage, README docs) are all met by the diff.

## Notes (pre-existing, not introduced by this branch)

- `doc-13`'s implementation-plan section in the task file still references the old `keep_recent_images` field and the superseded message-window semantics in places (Implementation Plan section, lines 87-95 and manifest rows), while the Implementation Notes and Final Summary describe the delivered TTL design. Cosmetic doc drift inside the task file only.
- `Router._get_model_capabilities` union-based resolution (router.py:647-655) predates this branch; see the cross-cutting comment above.

## Verification

- `uv run pytest tests/` → 421 passed.
- `uv run pytest tests/test_proxy_reload.py::TestImageHistoryStripping` → 18 passed.
- `uv run ruff check src/` → clean.
- `uv run ruff format --check src/ tests/` → 38 files already formatted.
- `uv run pyright src/` → 0 errors, 0 warnings, 0 informations.

## Merge Recommendation

**Needs changes** — no Critical or Major defects; the code is correct and fully gated. The gating item is process-state, not quality: all task content is uncommitted in the worktree. Commit the 5 modified files + 3 new decision docs, then merge. The two Minor items (broad `str(prepared)` assertions, detection-vs-strip capability-resolution asymmetry) and the doc-13 "user turns" vs index-distance clarification are worth a small follow-up but do not block merge.
