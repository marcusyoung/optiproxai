---
id: doc-12
title: 'Decision: syn:large:vision designated image model (bake-off evidence)'
type: other
created_date: '2026-09-04 12:36'
---
# Decision: syn:large:vision is the designated image model (evidence-based bake-off)

**Date**: 2026-09-04
**Status**: Accepted
**Related**: TASK-17 (image-history stripping), TASK-21 (cache_control breakpoints), doc-11 (multi-target breakpoints)

## Context

Image-bearing sessions pin all subsequent turns to vision-capable models (vision capability is detected from the whole message history). The 2026-09-03 kimi-k3@doubleword cache incident showed this pinning is only cheap and cache-friendly if the vision model it pins to handles both image extraction and caching well. A bake-off was run against a representative workload image (QGIS-style map screenshot with gridded raster overlay, `~/Downloads/2026-09-03_12-37-39.jpg`) across all 5 vision candidates in production config.

## Decision

**`syn:large:vision` (Kimi K3 on Synthetic) is the designated image model.** Image-bearing sessions route there first (existing capability routing already does this after vision was removed from the moonshotai/kimi-k3 doubleword rule); no dedicated image tier. TASK-17 (opt-in image-history stripping for non-vision candidates) is the complementary second half: once images age out of the strip window, sessions route back to cheaper/larger non-vision models with byte-stable prefixes.

## Evidence (2026-09-04 bake-off)

### Extraction quality

| Model | Provider | Quality |
|---|---|---|
| syn:large:vision | Synthetic | Best — correctly identified GIS/map + gridded heatmap overlay, precise color/structure description |
| mistral-medium-3.5 | Mistral | Good — identified GIS software, read concrete elevations (Monte Solaro 589m) |
| mistral-small-2603 | Mistral | Fair — labels only, weak on data overlay |
| glm-5.3-flash | ollamacloud | Verbose but generic |
| zai-org/glm-5.3-flash | Novita | Verbose but generic |

### Cache behavior on image content (repeat calls, image in prefix)

| Provider | cached_tokens | Mechanism |
|---|---|---|
| Synthetic | **2,176 (incl. image_tokens: 2,058)** — image itself served from cache, stable across calls | implicit content-hash auto-cache |
| Mistral (small + medium) | 0 | none on image content |
| Novita | 0 | none on image content |
| ollamacloud | not reported | n/a |

Synthetic's result doubles as a control for the Doubleword escalation: identical image and multi-turn shape with a ~2K-token served read proves image-bearing prefixes can cache — the kimi-k3 read-pinning is Doubleword/kimi-specific, not inherent to images.

## Operational caveats (syn:large:vision)

1. Reasoning model: budget `max_tokens >= 1500` for extraction tasks or content returns empty (finish_reason=length consumed by reasoning tokens).
2. Most verbose of the tested set.
3. 524K context ceiling (vs 1M on some non-vision candidates) — acceptable for screenshot workloads.

## Consequences

- No config change required now: capability routing already lands image sessions on syn:large:vision for the REASONING tier and mistral-medium-3.5 elsewhere; this decision documents the intended destination.
- TASK-17 planning should treat image stripping as the hand-back mechanism: image turns -> syn:large:vision; post-image turns with stripped history -> cheaper non-vision models (e.g. DeepSeek-V4-Pro 1M).
- If a future provider adds marker-based caching that byte-matches image blocks, re-run the bake-off before switching.
