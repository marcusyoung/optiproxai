---
id: doc-5
title: >-
  Decision: Candidate preparation returns body plus upstream headers (tuple
  return)
type: other
created_date: '2026-08-18 18:58'
---
# Decision: Candidate preparation returns body plus upstream headers (tuple return)

**Date:** 2026-08-18
**Status:** Decided
**Task:** TASK-6
**Author:** dev (optiproxai/code)

## Context
TASK-6 introduces three async delivery modes. `body` mode mutates the request JSON, which fits the current `_prepare_body_for_candidate(body, model, provider, runtime) -> dict` contract. `header` mode must add an HTTP header to the upstream request, and `model_suffix` mode must change the model name sent upstream. Neither is expressible through a body-only return: `_proxy_upstream` builds its own `headers` dict internally (Content-Type + Authorization) and receives only the prepared body, and the upstream model name is `body["model"]`, set by the caller before `_prepare_body_for_candidate` runs.

The existing `_prepare_body_for_candidate` call sites are `_try_with_fallbacks` (primary at proxy.py:900, fallback loop at proxy.py:973). Tests in `tests/test_proxy_reload.py::TestModelExtraBody` call `_prepare_body_for_candidate` directly and assert on the returned body dict.

## Options Considered

| Option | Pros | Cons |
--------|------|------|
| `_prepare_body_for_candidate` returns `(body, headers)`; `_apply_async_mode` mutates body["model"] for model_suffix; `_proxy_upstream` gains an `extra_headers` parameter | Single preparation step returns everything the upstream call needs; model name, body, and headers stay consistent because they are computed together; `_proxy_upstream` change is one additive parameter with merge-after-defaults so async headers win over nothing (no overlap with Content-Type/Authorization expected) | Signature change to a private function with direct test callers (tests updated to unpack tuple); `_proxy_upstream` grows a parameter |
| Keep body-only return; add a separate `_resolve_async_headers(model, provider, runtime)` called by `_try_with_fallbacks` | `_prepare_body_for_candidate` signature unchanged | Async logic split across two call sites; easy for them to drift; model_suffix still needs body mutation, so the body-only contract is broken anyway |
| Return a small result object (e.g. `PreparedRequest(body, headers, model)`) | Self-documenting; room to grow | New class for a two-field tuple; heavier than the codebase's existing tuple conventions (`as_tuple`, `(model, provider)` pairs) |

## Decision
Change `_prepare_body_for_candidate` to return `tuple[dict[str, Any], dict[str, str]]` — `(prepared_body, extra_upstream_headers)`. Internally it calls `_resolve_async_mode(...)`; when a config resolves, `_apply_async_mode(body, headers, async_mode)` applies the delivery mechanism: `body` injects `{field: value}` into the JSON (merged last, after `extra_body`, so async config wins on conflict), `header` adds `{field: value}` to the extra headers dict, and `model_suffix` appends `:{suffix}` to `body["model"]` in place (no return of a separate model value — the body is the single source of truth for the model name). `_proxy_upstream` gains an optional `extra_headers: dict[str, str] | None = None` parameter and merges it into its headers dict after the defaults. Both `_try_with_fallbacks` call sites unpack the tuple and pass the headers through.

## Rationale
The tuple keeps all async application inside the existing preparation step, matching the task's intent that `_prepare_body_for_candidate` uses `_resolve_async_mode` then `_apply_async_mode`. Keeping the model name in `body["model"]` avoids a third channel (the task description's `(body, headers, model)` triple) since the body already carries it and `_proxy_upstream` reads the model from the body for logging.

## Consequences
`_prepare_body_for_candidate` is private; its direct test callers in `tests/test_proxy_reload.py` are updated to unpack `(prepared, _)`. New tests for each delivery mode assert on the tuple elements. `_proxy_upstream`'s new parameter is optional so the pass-through path (no routing decision) is untouched. `extra_body` remains a general escape hatch, applied before async body injection.
