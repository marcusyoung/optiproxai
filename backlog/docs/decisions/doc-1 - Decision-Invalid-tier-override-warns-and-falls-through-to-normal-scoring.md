---
id: doc-1
title: 'Decision: Invalid tier override warns and falls through to normal scoring'
type: other
created_date: '2026-08-17 12:25'
---
# Decision: Invalid tier override warns and falls through to normal scoring

**Date:** 2026-08-17
**Status:** Decided
**Task:** TASK-1
**Author:** dev (optiproxai/code)

## Context
When a user writes `/optiproxai:foo` (an unrecognized tier), the proxy must decide whether to reject the request or continue. The token is already parsed from the latest user message; the question is what to do with an unrecognized tier name.

## Options Considered

| Option | Pros | Cons |
--------|------|------|
| Return HTTP 400 error | Explicit failure signal; user knows immediately | Breaks chat UIs that don't surface HTTP errors well; harsh for a typo |
| Warn + normal scoring (token still stripped) | Graceful degradation; no crash; user can retry with correct tier; token doesn't leak upstream | User may not notice the warning if logs aren't visible |

## Decision
Warn + normal scoring. The `/optiproxai:foo` token is still stripped from the message content (so it doesn't leak to the upstream model), but `tier_override` is set to `None` and the scorer runs normally. A `log.warning` is emitted.

## Rationale
The feature is a convenience override, not a security boundary. A typo should not break the user's turn. Stripping the token regardless prevents leakage. The warning provides visibility for users who check logs (`optiproxai-server.log` via `optiproxai.proxy` stderr logger).

## Consequences
Invalid tier tokens are silently consumed (stripped) but do not affect routing. Users who misremember tier names get normal routing instead of an error. No HTTP error path needs to be added to the proxy or CLI.
