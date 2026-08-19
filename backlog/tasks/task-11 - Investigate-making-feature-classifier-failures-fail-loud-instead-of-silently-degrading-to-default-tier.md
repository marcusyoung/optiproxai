---
id: TASK-11
title: >-
  Investigate making feature-classifier failures fail-loud instead of silently
  degrading to default tier
status: To Do
assignee: []
created_date: '2026-08-19 12:42'
labels: []
dependencies: []
type: task
ordinal: 15000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
When the distilled feature classifier cannot run correctly at request time, the proxy currently falls back silently to the default (medium) tier. This was triggered in production (2026-08-19): a transient embedding-config resolution failure got pinned by the embedding-settings memoization, so the runtime used OpenAI text-embedding-3-small (1536-dim) against a bundle trained on voyage-4 (1024-dim). The result was `ValueError: embedding dimension mismatch: expected 1024, got (1536,)` inside `Scorer._embed_text`, caught by `Scorer.classify`'s broad except, and every request routed to medium with no operator-visible signal.

Root-cause fix already landed separately (env-fallback must not write to the embedding-settings cache). This task is a follow-up to decide and implement the desired fail-loud behavior so this class of failure is visible.

Context:
- `Scorer.classify` (src/optiproxai/scorer.py) wraps classification in broad except -> `_default_result()` (fallback tier).
- `DistilledFeatureClassifier.load` computes `embedding_model_mismatch` (bundle model vs runtime model) and only logs a WARNING.
- `Scorer._embed_text` raises `ValueError` on dimension mismatch; it is swallowed into the default result.
- `inspect_feature_classifier_runtime_status` (doctor) already surfaces mismatch/mismatch warning but is not fail-loud.

Decide which of the following (or a combination) is the right behavior, and implement accordingly:
1. Refuse classification and let routing fail closed (proxy returns an OpenAI-style error, request not routed).
2. Keep routing on the default tier but log an ERROR and mark the routing log/decision as classifier-failed so it is visible instead of a silent medium.
3. Only make doctor/CLI diagnostics hard-error on mismatch.

Also decide whether to detect by dimension mismatch, model-name mismatch, or both (note: the incident was a dimension mismatch, which the current model-name check would NOT have caught). Consider a mechanism to make a transient/cached failure self-heal on config reload or process restart.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Investigate and document the current failure paths for the feature classifier (model-name mismatch, dimension mismatch, embedding failure, disabled mode) and where each is silently swallowed.
- [ ] #2 Recommend and decide on the fail-loud behavior (one of: hard-stop routing, error-log+default-tier, or diagnostics-only), including how dimension mismatch vs model-name mismatch are each handled.
- [ ] #3 Recommend whether to add a per-request or startup check that compares the runtime embedding model/dim against the bundle and surfaces a clear, actionable error message.
- [ ] #4 If a code change is chosen, implement it with tests covering the failure paths and the chosen behavior.
- [ ] #5 Update doctor/CLI diagnostics to reflect the chosen behavior.
- [ ] #6 Do not expand scope into unrelated classifier or routing changes.
- [ ] #7 References: PR fix branch `fix/embedding-env-fallback-cache` (env-fallback cache fix), and the incident root cause is documented in the session handoff.
<!-- AC:END -->
