---
id: doc-8
title: Session-keyed last-context token cache for input-limit gating
type: other
created_date: '2026-08-28 11:40'
---
# DR: Session-keyed last-context token cache for input-limit gating

- **Date:** 2026-08-28
- **Task:** TASK-016 (Hy3 input-limit cap misfire)
- **Status:** Accepted

## Context
`_estimate_tokens()` (even after 3472b91 counting tools schema and per-message fields) undercounts real prompts because it cannot see `reasoning_content` payloads reliably and cannot account for provider-side prompt-template overhead. Real 168–190K-token prompts estimated under the 160K cap and routed to Hy3, stalling 300s before failing over.

## Decision
Estimate prompt size in `route()` as `max(prior-turn provider-reported prompt_tokens, live tiktoken estimate)`.

- Provider `usage.prompt_tokens` is ground truth for what the provider received — cache it per session after each successful upstream call (fed from the `_log_usage` path in proxy.py).
- Cache is process-local `dict[str, int]` + `Lock` keyed by `X-Session-Id` (`config.routing.session_header`), mirroring `FallbackBackoffState` (module `src/optiproxai/last_context_cache.py`).
- The session key travels on `RoutingDecision.session_key` so the usage-logging site can feed the cache without changing `_proxy_upstream`/`_log_usage` signatures.
- First turn / missing session header → live estimate only (unchanged behaviour).
- Additionally count `reasoning_content` in `_estimate_tokens` for first-turn accuracy (belt-and-braces; the cache is the primary mechanism).

## Rationale
- Err toward the 1M fallback rather than a 300s stall; cached provider prompt includes all real overhead so it never underestimates a continuation of the same conversation.
- Overestimate risk is bounded: worst case is early escalation to the 1M model, never a misfire stall.
- Rejected alternative: only fix `_estimate_tokens` — proven insufficient (3472b91 did not stop the misfire).
- Rejected alternative: count reasoning_content only — reasoning_content is often stripped/sanitized per candidate model (`_sanitize_reasoning_content_for_candidate`), so the live estimate still cannot see what a prior turn actually sent.

## Consequences
- One int of memory per session for process lifetime; cleared only on process restart (same lifetime model as FallbackBackoffState).
- Clients not sending `X-Session-Id` get no cache benefit.
- Held decisions preserved: analysis stays Hy3 @ 160000 cap; REASONING stays K3; MEDIUM+COMPLEX on glm-5.3-flash.
