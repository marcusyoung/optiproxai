---
id: TASK-22
title: >-
  Reconcile router capability resolution to best-score semantics (union vs
  best-score divergence)
status: To Do
assignee: []
created_date: '2026-09-04 13:45'
labels:
  - enhancement
  - routing
  - consistency
dependencies: []
priority: low
type: enhancement
ordinal: 21500
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Capability resolution in the codebase uses two different semantics: Router._get_model_capabilities (router.py ~638) UNIONS capabilities across ALL matching model_rules, while every proxy-side resolver (_get_model_content_part_policy, _get_model_reasoning_content_support, _get_model_extra_body, _get_model_cache_control, _get_model_vision_capability) uses best-score (provider-specific outranks provider-agnostic, then prefix length). Divergence: a model with overlapping rules of differing specificity can be seen as vision-capable by the router's filter but non-vision by the TASK-17 strip sanitizer (or vice versa). No live exposure in production config as of 2026-09-04 (no overlapping rules with disagreeing vision flags), but latent inconsistency.

Recommended direction: reconcile the router to best-score semantics (matching the proxy-side pattern and the doc-7 presence-based philosophy) rather than union — but this changes capability-filter routing behavior and needs its own plan + tests. Discovered in doc-15 branch review of TASK-17.
<!-- SECTION:DESCRIPTION:END -->
