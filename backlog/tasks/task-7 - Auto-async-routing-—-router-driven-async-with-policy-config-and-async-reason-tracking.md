---
id: TASK-7
title: >-
  Auto-async routing — router-driven async with policy config and async reason
  tracking
status: To Do
assignee: []
created_date: '2026-08-18 13:13'
updated_date: '2026-08-18 13:13'
labels: []
dependencies:
  - TASK-2
  - TASK-5
  - TASK-6
priority: low
type: feature
ordinal: 11000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Router-driven async routing — the scorer/router automatically decides whether to send a request in async mode based on classification signals (tier, agentic_score, confidence), governed by a policy knob in config.

## Concept
Async/flex trades latency for cost. The router already classifies every prompt with tier, score, confidence, and agentic_score. These signals map naturally to async eligibility:

| Signal | Sync (latency matters) | Async (cost/throughput) |
|---|---|---|
| Tier | REASONING, COMPLEX | SIMPLE, MEDIUM |
| agentic_score | High (>0.6) — agentic loops compound latency | Low (<0.3) |
| Confidence | Low — needs careful routing | High — routine |

## Config
```yaml
routing:
  auto_async:
    enabled: true
    strategy: cost_optimized   # cost_optimized | latency_optimized | never
    tiers: [SIMPLE, MEDIUM]     # only apply to these tiers
    max_agentic_score: 0.3      # never auto-async above this
```

- `cost_optimized`: router auto-sends eligible prompts async
- `latency_optimized`: router never auto-decides async (async only via config default or per-turn override)
- `never`: alias for latency_optimized, explicit opt-out

## Precedence
```
Per-turn :async override    -> forces async (wins everything)
Per-turn :sync override      -> forces sync (overrides auto-async)
Router auto-async           -> applies when client is silent
Config default              -> baseline, lowest priority
```

The client always wins. The router only auto-decides when the client didn't express a preference. The config default is the floor.

## Async reason tracking
Persist `async_reason` alongside `async_mode` in execution_logs:
- `config` — async from provider/model config default
- `client` — async from per-turn :async modifier
- `router` — async from auto-async policy

Dashboard async card can then show a split: config vs client vs router.

## Dependencies
- TASK-6 (async_mode config model) — needs the delivery mechanism infrastructure
- TASK-5 (dashboard routing section) — needs async tracking columns
- TASK-2 (per-turn :async modifier) — needs the per-turn override to complete the precedence chain

## Deferred
This task is intentionally deferred. It builds on the async_mode config model (TASK-6), per-turn async modifier (TASK-2), and dashboard async tracking (TASK-5). It should be started only after those three are complete.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 routing.auto_async config section exists with enabled, strategy, tiers, and max_agentic_score fields
- [ ] #2 strategy accepts cost_optimized, latency_optimized, and never
- [ ] #3 When strategy is cost_optimized and request is eligible (tier in configured tiers, agentic_score below threshold, client did not express preference), router sets async_mode for the request
- [ ] #4 When strategy is latency_optimized or never, router never auto-decides async
- [ ] #5 Per-turn :async override takes precedence over auto-async (client always wins)
- [ ] #6 Per-turn :sync override (if implemented) takes precedence over auto-async, forcing sync
- [ ] #7 Config default async_mode applies when router does not auto-decide and client is silent
- [ ] #8 async_reason persisted in execution_logs: config, client, or router
- [ ] #9 Dashboard async card can split async requests by reason (config vs client vs router)
- [ ] #10 uv run ruff check src/ passes
- [ ] #11 uv run ruff format --check src/ tests/ passes
- [ ] #12 uv run pyright src/ passes
- [ ] #13 uv run pytest tests/ -q passes
- [ ] #14 Tests cover: cost_optimized strategy auto-async, latency_optimized/never opt-out, precedence (client override wins), agentic_score threshold filtering, tier filtering, async_reason persistence
<!-- AC:END -->
