---
id: doc-17
title: >-
  Decision: REASONING-tier cost model — Flash models, explicit caching off,
  vision via Inkling-or-constraint
type: other
created_date: '2026-09-24 12:37'
updated_date: '2026-09-24 13:03'
---
# Decision: REASONING-tier cost model — Flash models, explicit caching off, vision via Inkling-or-constraint

**Date**: 2026-09-24
**Status**: Accepted (implemented in the live config 2026-09-24; TASK-26)
**Related**: TASK-26 (config revision, Done), TASK-24 (per-model effort value), TASK-25 (per-provider/model effort vocabulary), doc-7 (cache_control precedence), doc-10 (pricing metadata), doc-13 (image-history aging)

## Context

The REASONING tier was the dominant spend under the live config. On 2026-09-23 execution logs (1288 records; 37 reasoning requests) the tier pulled 7.61M prompt + 4.64M cached tokens against only 122K output tokens. Premium models on 287K-494K-token contexts drove the ~GBP 10 reasoning bill: GLM-5.3 (zai-org/GLM-5.3, $1.40/M input) and Kimi K3 (moonshotai/kimi-k3, $3.00/$15.00). Reasoning effort accounted for ~2% of tokens, so effort tuning is not a viable lever — the model choice and context size are.

## Decision

1. **Move the code and analysis REASONING tiers off the premium models** to `deepseek-ai/DeepSeek-V4.1-Flash` (primary) / `zai-org/GLM-5.3-Flash` (fallback) — both already declared model rules. Measured effect on the 37-call mix: $5.99 -> $0.56 (~91% cut).
2. **Keep explicit prompt caching off.** Implicit caching is the provider default and never bills writes; explicit caching adds a 1.25x (5m) / 2x (1h) write premium that is not wanted. Provider policy is `enabled: false` and the redundant per-model `cache_control` blocks were removed — they made caching look active when it never was.
3. **Do not chase reasoning effort.** `reasoning_style: xai` caps the allow-list at {none, low, medium, high}, so `max` is coerced to `high`; honouring a wider vocabulary is TASK-25's job, not a cost lever here. The config records a four-rung ladder (SIMPLE low / MEDIUM medium / COMPLEX high / REASONING max) across all three profiles; the async profile keeps its original REASONING models (GLM-5.3/Kimi).
4. **MiMo-V2.5-Pro stays excluded.** The work is guided, iterative with human review checkpoints; MiMo's edge (long-horizon agentic endurance) is what the checkpoints already provide, so its ~2.6x premium is unused.

## Vision capability — resolved by live probe (2026-09-24)

A generated image with a known answer (digit `6` plus a red square bottom-right) was sent to each Doubleword candidate, with the API key loaded at runtime from `~/.config/optiproxai/.env`. Results corrected an earlier assumption that Doubleword Flash deployments reject images:

| Model | Probe result | Config action |
|---|---|---|
| `deepseek-ai/DeepSeek-V4.1-Flash` | SEES — read digit + red square correctly | declared `vision`; is the cheap 1M primary vision path |
| `thinkingmachines/Inkling-NVFP4` | SEES — correct | rule added; vision fallback |
| `zai-org/GLM-5.3-Flash` | DOES NOT SEE — answered `8` on dark blue, no square (confident hallucination) | `vision` left off its rule |
| `meta-models/Muse-Glimmer-30B` | SEES — correct | kept as MEDIUM/COMPLEX fallback (132K cap) |

The feared REASONING vision regression did not materialise: DeepSeek-V4.1-Flash is itself vision-capable at 1M context. Inkling is a belt-and-braces vision fallback, with `moonshotai/kimi-k3` retained as an extra fallback below it. All four effort rungs (low/medium/high/max) return HTTP 200 on all four candidates (an initial kimi-k3 503 was a transient cold-start).

## Open / unresolved

- **Root cause is context size,** not model. A hard auto-compaction ceiling below ~130K would unlock the cheapest context-limited models and is tracked separately from this decision.

## Consequences

- Code/analysis REASONING output quality is now bounded by Flash-class models; per-turn tier override to a premium model remains available if hard turns regress.
- `max` effort is recorded in the config but is sent as `high` until TASK-25 lands.
- GLM-5.3-Flash accepts image requests but does not process them — never declare `vision` on it.
- doc-12's designated image model (`syn:large:vision`, Synthetic) is moot: Synthetic is no longer in use.
