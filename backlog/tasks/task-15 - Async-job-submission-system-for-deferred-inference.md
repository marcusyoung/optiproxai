---
id: TASK-15
title: Async job submission system for deferred inference
status: To Do
assignee: []
created_date: '2026-08-19 23:07'
updated_date: '2026-08-19 23:16'
labels:
  - async
  - jobs
  - sference
  - architecture
dependencies: []
references:
  - 'https://github.com/gszecsenyi/LLMeQueue'
  - 'https://github.com/Kurat0r/ollama-queue'
  - 'https://github.com/Attakay78/fastapi-taskflow'
  - 'https://github.com/ddreamboy/liteq'
  - 'https://github.com/jayuzferro/hivemind'
  - 'https://github.com/SeemSeam/llmgateway'
  - 'https://github.com/vleeuwenmenno/llmapiproxy'
priority: low
type: feature
ordinal: 19000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add a one-shot async job layer to optiproxai: submit an LLM request now, get a job ID, poll for the result later. Designed for latency-tolerant work on providers with long queue times (e.g. sference flex ~10 min).

## Background

Research across six established systems (Bifrost, qlaud, fal.ai, Future AGI, T-Systems LLM Hub, Ray Serve) confirms the industry-standard pattern: POST to an async endpoint, get job_id + 202, poll GET /jobs/{id} (202 while pending/running, 200 when terminal), result TTL + cleanup, optional webhooks. All six pass the sync body through unchanged — none inject an unattended-request contract, strip tools structurally, or classify the response.

## Core design

**Mailbox shape (adopt industry standard):**
- `POST /v1/jobs` — accepts a bare request (model/tier + prompt + minimal knobs), returns `{job_id, status: "queued"}` + 202
- `GET /v1/jobs/{id}` — returns status + result when terminal (200), or 202 while pending/running
- `GET /v1/jobs` — list jobs (optional status filter)
- `DELETE /v1/jobs/{id}` — cancel a pending job
- CLI: `opx job submit/status/list/cancel`
- SQLite storage (same pattern as dashboard), job lifecycle: `queued -> running -> completed | failed | cancelled`
- Result TTL (default 1h from completion), expired jobs cleaned periodically
- Background worker in serve process holds the long-poll connection to the provider

**Unattended-job contract (our differentiator):**
- Job layer injects a fixed system instruction: "This is an unattended request. You have no tools available and cannot ask questions. If you would normally ask for clarification, state your assumption explicitly in your answer, then proceed. Produce only your final response."
- Tools are structurally stripped from the request body — the model cannot return tool calls even if it tries
- No conversation history, no prior turns, no streaming — the job is strictly one-shot
- Response classification: job record flags `completed` vs `completed_with_tool_calls` (if the model returned tool calls despite the contract) vs `needs_clarification` (heuristic detection of question-like responses)

**Trigger: per-request opt-in, never routing.**
- A job is submitted explicitly by the caller; the router/tier never auto-routes to flex
- Interactive tiers stay on realtime; jobs are for callers who know their work is one-shot and latency-tolerant

**Two-tier timeout (from fal.ai pattern):**
- `queue_timeout`: wall-clock deadline for provider queue wait (abandon if not picked up in time)
- `execution_timeout`: separate cap on processing time once the provider starts

**Not in scope (explicitly excluded):**
- No job chaining / multi-turn continuations / tool-result posting — a job is one-shot, period
- No auto-continue / proxy-held conversation sessions
- No scheduling (Future AGI's `scheduled_for` is a possible future addition, not v1)
- No webhooks (poll-only for v1; webhooks are a possible future addition)
- No batch file upload (OpenAI Batch API is a different category)

## References

- Bifrost async: https://docs.getbifrost.ai/features/async-inference (job lifecycle, TTL, webhook signing)
- qlaud: https://docs.qlaud.ai/api-reference/jobs (Cloudflare Queue consumer, cost_micros in response)
- fal.ai: https://fal.ai/docs/documentation/model-apis/inference/queue (two-tier timeout, queue_position, status streaming)
- Future AGI: https://docs.futureagi.com/docs/command-center/api/async-batch/ (scheduled completions, batch)
- T-Systems: https://docs.llmhub.t-systems.net/guides/asynchronous-requests/ (queue twin of every endpoint)
- Ray Serve: https://docs.ray.io/en/latest/serve/asynchronous-inference.html (Celery/Redis queue infra, DLQ, autoscaling)
- Session handoff: 7dc44b64-6bc4-43b3-8b08-46fd5979fc70 (sference flex evaluation, detached execution pattern, design discussion)
<!-- SECTION:DESCRIPTION:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## MIT-licensed building blocks research (2026-08-19)

No drop-in solution found. Every candidate needs adaptation. Two practical paths identified:

**Path 2 recommended: build from scratch, study LLMeQueue.** optiproxai already has FastAPI, SQLite (dashboard pattern), Click CLI, config, router — a `jobs.py` module reusing those is more natural than adapting an external framework. Queue machinery ~200 lines (SQLite + background worker); the differentiators (unattended contract, tool stripping, response classification) are ~50 lines each.

**Path 1 alternative: fastapi-taskflow library.** Pip-installable, adds job persistence/retries/priority/queues to an existing FastAPI app. Lifecycle states (PENDING/RUNNING/SUCCESS/FAILED/INTERRUPTED), pending requeue on restart, live SSE dashboard. Would still need the /v1/jobs endpoint, contract injection, tool stripping, and provider routing added. Lowest effort, but external abstraction to adapt.

### Candidates found

| Project | Stack | Job pattern | Notes |
|---|---|---|---|
| LLMeQueue (gszecsenyi) | FastAPI + SQLite + worker | POST /v1/chat/completions -> GET /tasks/{id} + /tasks/{id}/result | Closest architecture match: same submit/poll/retrieve mailbox with SQLite. ~4 files, cleanest reference. |
| ollama-queue (Kurat0r) | FastAPI + SQLite WAL + Click CLI | Priority queue, DLQ, retry backoff, health gating, web dashboard | Most mature job machinery (1,951 tests, 100% coverage) but Ollama-specific (VRAM/RAM gating) and deeply entwined with Ollama proxy. Heavy to extract. |
| fastapi-taskflow (Attakay78, 54 stars) | FastAPI library over BackgroundTasks | Retries, persistence (SQLite/Redis/PG), priority + named queues, scheduling, SSE dashboard | Easiest integration (library, not service). No LLM awareness — contract layer is ours. |
| LiteQ (ddreamboy) | Pure Python + SQLite, zero deps | @task decorator + .delay(), priorities, retries, FastAPI integration | Simplest; good reference for queue mechanics, too minimal for production API. |
| hivemind (jayluxferro) | Python HTTP proxy + SQLite | Transparent proxy with admission control, AIMD backpressure, token budgets, MCP tools (hm.submit/status/priority) | Architecturally closest sibling (proxy with SQLite jobs + MCP surface) but a full proxy replacement, not extractable. |

Key insight: none of the found projects inject an unattended-job contract, strip tools structurally, or classify responses — the differentiators remain ours.

## sference flex verified working (2026-08-19)

Flex tier is now confirmed active on the user's account. Detached curl job (pid 31516, dispatched 22:54:25Z) completed after ~10 min in the flex queue and echoed `service_tier: "flex"` in the response.

```json
{"object":"chat.completion","model":"Qwen/Qwen3.6-35B-A3B","finish_reason":"length","usage":{"prompt_tokens":16,"completion_tokens":8,"total_tokens":24},"service_tier":"flex"}
```

Observations:
- Flex eligibility is endpoint-wide, not per-model — the model catalog exposes no flex/tier/queue field; quickstart says "three modes share the same endpoint and models".
- The 8-token test cap produced finish_reason "length" with empty content because Qwen3.6-35B consumes tokens on reasoning_content first ("thinking" enabled) — a test-parameter artifact, not a flex failure.
- Response carries reasoning_content + reasoning_format "provider_field" on chat completions — matches the xai-style reasoning already compatible with optiproxai's relay.
- Flex cost ~30% below realtime per sference support. With K3 realtime at $2.25/$11.25, flex K3 ≈ $1.58/$7.88 on 1M context — cheapest 1M-context option vs Doubleword K3 (256K, $2.15/$11.25 async).

Operational lessons for the job worker design:
- Node fetch abandons flex connections at its default 5-min header timeout (observed twice). curl --max-time 900 (no header timeout) is required to ride the full queue.
- A transient 502 Bad Gateway HTML page was observed at the gateway for one queued attempt; the request was eventually processed server-side anyway. The worker must tolerate gateway 502s and keep its own deadline, not abort on them.

## Phase 2: Batch mode (separate task, same ledger spine)

Batch is a different concept (many requests vs one) but composes on the same job layer. Out of scope for v1 (TASK-15); planned as a follow-up phase 2 task.

| | Async job (v1) | Batch (phase 2) |
|---|---|---|
| Unit | One request | Many requests (file / inline array) |
| ID | job_id | batch_id + per-item custom_id |
| Window | ~10 min (sference flex queue) | 24h |
| Discount | ~30% (sference flex) | ~48.5% (measured sference batch) |
| Result | Single response | Array of per-item responses |
| Fit | One-shot latency-tolerant work | Bulk N-for-N work, no interactive use |

Shared spine with v1: SQLite job table with a batch_id column grouping job rows; background worker submits to the provider's batch endpoint, polls batch status, fans results out per custom_id. No file upload needed for sference — measured POST /v1/batches with inline requests array (5 requests, 930 tokens, cost_micros=85 vs realtime-expected 165, ~48.5% discount).

Phase 2 shape:
- POST /v1/batches — accept a list of job requests (or a reference to an uploaded file for OpenAI-style batch), return batch_id + 202
- GET /v1/batches/{id} — status + per-item results when terminal
- Same unattended-job contract applies per item (each item is a one-shot unattended request)
- Response classification per item
- CLI: opx batch submit/status/list
- sference: inline requests array; Doubleword/OpenAI style: file upload first, then batch create

Not in phase 2: webhooks (still poll-only), chunked streaming of batch results.
<!-- SECTION:NOTES:END -->
