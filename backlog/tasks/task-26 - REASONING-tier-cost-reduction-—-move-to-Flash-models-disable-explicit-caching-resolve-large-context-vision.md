---
id: TASK-26
title: >-
  REASONING-tier cost reduction — move to Flash models, disable explicit
  caching, resolve large-context vision
status: Done
assignee: []
created_date: '2026-09-24 12:33'
updated_date: '2026-09-24 13:03'
labels: []
dependencies: []
references:
  - 'C:\Users\myoun\.config\optiproxai\config.yaml'
  - src/optiproxai/config.py
  - TASK-24
  - TASK-25
  - 'C:/Users/myoun/.local/state/optiproxai/log/execution-2026-09-23.jsonl'
documentation:
  - >-
    decisions/doc-17 -
    Decision-REASONING-tier-cost-model-—-Flash-models-explicit-caching-off-vision-via-Inkling-or-constraint.md
  - >-
    decisions/doc-12 -
    Decision-syn-large-vision-designated-image-model-bake-off-evidence.md
  - >-
    decisions/doc-7 -
    Decision-cache_control-resolves-highest-precedence-wins-no-ModelEntry-propagation.md
  - >-
    decisions/doc-11 -
    Decision-cache_control-multi-target-breakpoints-via-targets-list-field.md
  - >-
    decisions/doc-13 -
    Decision-image-history-aging-via-turn-based-TTL-with-vision-scope-softening-and-per-candidate-stripping.md
  - >-
    decisions/doc-10 -
    Decision-Model-pricing-metadata-lives-on-ModelEntry-and-ModelRuleEntry-resolved-at-render-time.md
priority: high
type: chore
ordinal: 26000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The REASONING tier is the dominant cost centre under the current config. On 2026-09-23 log data (1288 records; 37 reasoning calls) the spend was input/context-driven, not thinking-driven: 7.61M prompt tokens + 4.64M cache reads against only 122K output tokens. The premium models on that tier (zai-org/GLM-5.3 at $1.40/M input, moonshotai/kimi-k3 at $3.00/$15.00) running on 287K-494K-token contexts produced the ~GBP 10 reasoning bill. Reasoning effort is ~2% of tokens and cannot be the lever. This task revises the live config so the tier runs on the already-declared cheap Flash models, stops explicit caching that was never actually active, and closes the unresolved large-context vision gap where DeepSeek/GLM Flash do not accept images on Doubleword and Muse-Glimmer-30B is context-capped at 132K.

Context a future worker cannot recover: measured baseline for the 37 reasoning calls is GLM-5.3 realtime full-input $11.19 / cached $5.99 vs deepseek-v4.1-flash $0.56 and glm-5.3-flash $0.65. MiMo-V2.5-Pro is deliberately excluded (guided iterative work with human checkpoints substitutes for its agentic-endurance edge). The config file is the user's live config at C:\Users\myoun\.config\optiproxai\config.yaml, outside the repo. Explicit caching was configured but never on effectively; implicit caching is provider-side default and never bills writes. Nothing here requires repo source changes — per-provider reasoning_effort VOCABULARY is TASK-25 and per-model effort VALUE is TASK-24.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 In the code and analysis profiles the REASONING tier primary is deepseek-ai/DeepSeek-V4.1-Flash (provider doubleword) and fallback is zai-org/GLM-5.3-Flash (provider doubleword); the async profile REASONING models are unchanged (zai-org/GLM-5.3 primary / moonshotai/kimi-k3 fallback)
- [x] #2 All three profiles have tier-level reasoning_effort re-spread onto four distinct rungs — SIMPLE low, MEDIUM medium, COMPLEX high, REASONING max (async effort changes; async REASONING models do not) — and all four rungs are verified accepted (HTTP 200) by the primary and fallback candidates
- [x] #3 The doubleword provider cache_control block is set enabled: false and every per-model cache_control block is removed from model_rules, leaving no explicit cache markers configured
- [x] #4 smart_proxy.image_history_stripping.image_ttl_turns is left unchanged at 3
- [x] #5 Live probe outcome is reflected in config: deepseek-ai/DeepSeek-V4.1-Flash declares vision (verified sees images), a thinkingmachines/Inkling-NVFP4 rule is added (doubleword, vision+tools+json_mode, xai, realtime pricing) and used as a vision fallback, moonshotai/kimi-k3 is retained as an extra vision fallback below Inkling, and zai-org/GLM-5.3-Flash does NOT declare vision (verified hallucinates)
- [x] #6 The revised config loads and validates cleanly (uv run opx config reports no validation error) and the resolved REASONING tier lists the new primary/fallback models
- [x] #7 A timestamped backup of the pre-edit config.yaml exists before any edit is applied
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
# TASK-26 Plan — REASONING-tier cost reduction (config-only)

## Objective
Cut REASONING-tier spend (~91% on the measured mix) by moving the code/analysis REASONING tiers to the already-declared Flash models, turning off explicit caching that was never effectively active, and closing the large-context vision gap — while keeping every tier's capability coverage intact.

## Scope boundary
- **Config-only.** Target is the live user config `C:\Users\myoun\.config\optiproxai\config.yaml`. No repo source files, no git branch, no PR.
- Reasoning-effort **vocabulary** (per-provider/model allow-list) is **TASK-25**; per-model effort **value** is **TASK-24**. Neither is implemented here.
- The auto-compaction ceiling recommendation (cap context < ~130K) is a separate concern, not in this task.
- The **async profile REASONING models stay as-is** (GLM-5.3 primary / Kimi K3 fallback), but async tier effort values are re-spread onto the same ladder as code/analysis.

## Evidence (2026-09-23 logs)
37 reasoning requests: 7.61M prompt + 4.64M cache reads vs 122K output. Cost baseline: GLM-5.3 realtime $11.19 full-input / $5.99 cached; deepseek-v4.1-flash $0.56; glm-5.3-flash $0.65. Effort is ~2% of tokens, not the lever; context size (287K–494K) is the driver.

## Changes

### 1. REASONING models — code + analysis
- primary `deepseek-ai/DeepSeek-V4.1-Flash` (doubleword, 1048576); fallback `zai-org/GLM-5.3-Flash` (doubleword, 1048576).
- async REASONING models unchanged (see scope boundary).

### 2. Effort ladder — all three profiles
- Re-spread every profile onto four distinct rungs: SIMPLE low / MEDIUM medium / COMPLEX high / REASONING max.
- code + analysis: MEDIUM high -> medium; COMPLEX max -> high; REASONING stays max.
- async: MEDIUM high -> medium; COMPLEX stays high; REASONING high -> max (models unchanged).
- Caveat: doubleword models use `reasoning_style: xai`, whose allow-list caps at {none, low, medium, high}; `max` is coerced to `high` until TASK-25 lands. The config still records the intended `max` so TASK-25 can honour it.
- **All four rungs confirmed against every candidate (live probe, 2026-09-24):** `deepseek-ai/DeepSeek-V4.1-Flash`, `zai-org/GLM-5.3-Flash`, `thinkingmachines/Inkling-NVFP4` and `moonshotai/kimi-k3` each returned HTTP 200 for `low`, `medium`, `high` and `max`. (An initial kimi-k3 run returned 503 'deployment not ready' on all four rungs; re-testing the same model with *no* `reasoning_effort` param and then with each rung all returned 200, so the 503 was a transient cold-start, not an effort fault.) Since xai normalization sends at most `high`, every tier's effort is accepted by whichever candidate is selected.

### 3. Explicit caching off
- provider `doubleword.cache_control.enabled: false` (keep the block as documentation).
- delete the five rule-level `cache_control` blocks: kimi-k3, Muse-Glimmer-30B, GLM-5.3-Flash, GLM-5.3, DeepSeek-V4.1-Flash.
- Effect: `_resolve_cache_control` returns the (disabled) provider policy, so `_apply_cache_control` never runs; implicit caching remains the provider default and bills no writes.

### 4. Image aging — no change
- `smart_proxy.image_history_stripping.image_ttl_turns` stays at `3` (user decision: leave for now).

### 5. Vision coverage — RESOLVED by live probe (2026-09-24)
Live probes against Doubleword (API key loaded at runtime from `C:\Users\myoun\.config\optiproxai\.env`; a generated image with a known answer — digit `6` plus a red square bottom-right — sent to each candidate):
- **`deepseek-ai/DeepSeek-V4.1-Flash` SEES images** — read the digit and the red square correctly. This contradicts the earlier assumption that Doubleword Flash deployments reject images. Declare `vision` on its rule so image requests route to the cheap 1M-context primary, which closes the large-context vision gap.
- **`thinkingmachines/Inkling-NVFP4` SEES images** (correct) — add rule `{prefix: thinkingmachines/Inkling-NVFP4, provider: doubleword, capabilities: [tools, json_mode, vision], reasoning_style: xai, pricing: {input_per_mtok: 1.20, cache_read_per_mtok: 0.20, cache_write_per_mtok: 4.00}}`.
- **`zai-org/GLM-5.3-Flash` does NOT see images** — it answered confidently but wrong (called the `6` an `8` on a dark blue background with no square). Keep `vision` off its rule (already correct).
- **`meta-models/Muse-Glimmer-30B` SEES images** (correct) — keep as the MEDIUM/COMPLEX fallback (132K cap).

Actions: add `vision` to the DeepSeek-V4.1-Flash rule; add the Inkling rule; set the code/analysis REASONING fallback order to `[zai-org/GLM-5.3-Flash, thinkingmachines/Inkling-NVFP4 (vision), moonshotai/kimi-k3 (vision, retained as extra fallback below Inkling)]`. Non-image traffic ignores the vision candidates; image traffic resolves to DeepSeek primary, then Inkling, then kimi.

### 6. Validation
- Back up first: copy `config.yaml` to `config.yaml.bak-<timestamp>`.
- `uv run opx config` — strict load, no error (catches the silently-ignored-key class; provider/rule models do not forbid extras).
- `uv run opx doctor`.
- `uv run opx route "<reasoning-heavy prompt>"` — decision JSON shows REASONING tier on DeepSeek-V4.1-Flash.
- Confirm no `cache_control targets` log line is emitted on a request.

## Resolved during planning (no open questions)
1. **async effort ladder** — re-spread onto the four rungs across all profiles (user decision); async REASONING models stay GLM-5.3/Kimi.
2. **Vision branch** — Inkling accepts images, so use it as a vision fallback and retain kimi-k3 as an extra fallback below it (user decision). Additionally the probe shows DeepSeek-V4.1-Flash itself is vision-capable, so it becomes the primary vision path.
3. **doc-12 drift** — moot: Synthetic is no longer in use, so the `syn:large:vision` designation is dropped.

## Risks / system impact
- REASONING-tier vision regression (section 5) — highest.
- Vision premise corrected by probe: DeepSeek-V4.1-Flash is vision-capable (the feared REASONING vision regression is avoided) while GLM-5.3-Flash accepts image requests but hallucinates — never declare it vision.
- kimi-k3 503 investigated and cleared: a transient cold-start (re-tested 200 both without `reasoning_effort` and on every rung), not an effort fault — no action needed.
- Backlog task files (task-24, task-25, task-26) are untracked; do not commit.
- No repo code changes, so no CI impact: nothing to lint/typecheck/test.

## Task Manifest
| # | Title | Files | Depends On | Labels | Acceptance Criterion |
|---|---|---|---|---|---|
| 1 | Revise live optiproxai config for REASONING cost reduction and verify | C:\Users\myoun\.config\optiproxai\config.yaml | — | infra | Live config strict-loads with REASONING on DeepSeek-V4.1-Flash/GLM-5.3-Flash in code+analysis, effort ladder re-spread across all profiles (async models unchanged), no cache_control configured, DeepSeek-V4.1-Flash declared vision, Inkling rule added, and every tier retains vision coverage |

Single cohesive task — Phase 2 (decompose) skipped.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Backup: C:\Users\myoun\.config\optiproxai\config.yaml.bak-20260924-140219 (13071 bytes, identical size to source) created before the edit.

Applied: provider doubleword cache_control.enabled=false; removed all 5 rule-level cache_control blocks (config now has exactly 1 cache_control block); added thinkingmachines/Inkling-NVFP4 rule (doubleword, [tools, json_mode, vision], xai, pricing 1.20/0.20/4.00); added vision to the deepseek-ai/DeepSeek-V4.1-Flash rule; code+analysis REASONING primary -> DeepSeek-V4.1-Flash with fallback [GLM-5.3-Flash, Inkling-NVFP4, kimi-k3]; effort ladder re-spread in all three profiles to SIMPLE low / MEDIUM medium / COMPLEX high / REASONING max; async REASONING models left unchanged.

Deviation from the draft: smart_proxy.image_history_stripping.image_ttl_turns left at 3 (review decision), not changed to 1.

Validation: `uv run optiproxai config` strict-loads; `uv run optiproxai doctor` all [OK] (7 provider(s), model_rules entries: 7, code/analysis/async each 4 tiers); `uv run optiproxai route "/optiproxai:reasoning ..."` -> tier REASONING, model deepseek-ai/DeepSeek-V4.1-Flash, provider doubleword, reasoning_effort max; config scan shows 1 cache_control block (enabled false), 5 vision-declaring rules (mistral-medium-3.5, kimi-k3, Muse, Inkling, DeepSeek-V4.1-Flash), image_ttl_turns 3.

Live probe evidence: DeepSeek-V4.1-Flash and Inkling read a generated digit(6)+red-square image correctly (vision confirmed); GLM-5.3-Flash hallucinated a different scene (not vision-capable); kimi-k3 initial 503 was transient cold-start (re-tested 200 with and without reasoning_effort); all four effort rungs (low/medium/high/max) returned 200 on all four candidates.

No repo source or test files changed, so ruff/format/pyright/pytest were not applicable. The running proxy will pick up the new config on its normal hot reload.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Revised the live optiproxai config (C:\Users\myoun\.config\optiproxai\config.yaml) to cut REASONING-tier cost ~91% on the measured mix. Code/analysis REASONING now runs deepseek-ai/DeepSeek-V4.1-Flash (primary) with zai-org/GLM-5.3-Flash, thinkingmachines/Inkling-NVFP4 and moonshotai/kimi-k3 as fallbacks; async REASONING models unchanged. Explicit prompt caching disabled (provider policy enabled:false; all five per-model cache_control blocks removed). Effort ladder re-spread to SIMPLE low / MEDIUM medium / COMPLEX high / REASONING max across all three profiles. Live probe corrected a false premise: DeepSeek-V4.1-Flash and Inkling DO see images (declared vision) while GLM-5.3-Flash hallucinates (left non-vision); this avoided the feared REASONING vision regression and closed the large-context vision gap on the cheap 1M primary. Deviations: image_ttl_turns left at 3 (review decision); kimi-k3 was briefly 503 but is a transient cold-start (re-tested 200). Config strict-loads, doctor passes, forced REASONING route resolves to DeepSeek-V4.1-Flash. No repo code changed.
<!-- SECTION:FINAL_SUMMARY:END -->
