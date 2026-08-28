---
id: TASK-17
title: Opt-in image-history stripping for non-vision candidates (config policy)
status: To Do
assignee: []
created_date: '2026-08-28 15:24'
labels:
  - enhancement
  - routing
  - config
  - vision
  - content-policy
dependencies: []
priority: medium
ordinal: 21000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Vision capability is detected from the WHOLE message history, so a single image anywhere in a session pins all subsequent turns to vision-capable models. In the analysis COMPLEX tier this excluded the 1M DeepSeek-V4-Pro fallback and promoted syn:large:vision (524K cap) for 154-198K prompt turns (observed 2026-08-28, TASK-016 follow-up) — correct behavior, but long mixed image+text conversations pay synthetic prices and face a 512K ceiling.

Feature: opt-in config policy to strip image parts from older conversation turns for non-vision candidates, so post-image turns can route back to cheaper/larger non-vision models (e.g. DeepSeek-V4-Pro 1M).

Design points to settle during planning:
1. Trigger: only for candidates lacking vision (start narrow; do not also strip for vision-capable models to cut cost).
2. Window: keep the most recent N image-bearing user messages (N>=1, default 1); "leave for x turns then remove" maps onto counting image-bearing turns.
3. Placeholder: replace with text like [image omitted] vs silently drop the part — provider prompt-shape tolerance may differ; decide per provider if needed.
4. NEVER touch the latest user message — only degrade history.
5. KEY DECISION - last-context cache interaction: provider prompt_tokens includes image tokens; after stripping, the real prompt shrinks but max(cached, live) keeps the inflated estimate and forces escalation, partly defeating the savings. Choose: keep inflated value (consistent with TASK-016 err-toward-escalation bias) vs reset cache entry on stripped turns (risks underestimation). Needs an explicit decision record.
6. Quality caveat to document: later turns referencing earlier images ("compare with the chart above") degrade to placeholder-based reasoning — silent quality loss. Opt-in containment is the point.

Fits existing machinery: proxy already rewrites message content per candidate (_sanitize_reasoning_content_for_candidate, _normalize_message_content_for_candidate, content_part_policy) — a new image-history sanitizer mirrors that pattern. Also mirrors tools_capability_detection's declared-vs-required philosophy.
<!-- SECTION:DESCRIPTION:END -->
