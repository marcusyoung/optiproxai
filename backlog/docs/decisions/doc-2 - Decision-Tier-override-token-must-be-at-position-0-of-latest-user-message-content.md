---
id: doc-2
title: >-
  Decision: Tier override token must be at position 0 of latest user message
  content
type: other
created_date: '2026-08-17 12:25'
---
# Decision: Tier override token must be at position 0 of latest user message content

**Date:** 2026-08-17
**Status:** Decided
**Task:** TASK-1
**Author:** dev (optiproxai/code)

## Context
The `/optiproxai:<tier>` token could theoretically appear anywhere in the message. The question is whether to scan the full content or only the start.

## Options Considered

| Option | Pros | Cons |
--------|------|------|
| Scan entire message content | Flexible; token can appear anywhere | Ambiguous if user types `/optiproxai:reasoning` mid-sentence; false positives; more complex parsing |
| Position 0 only (regex `^/optiproxai:(\w+)\s*`) | Simple; unambiguous; matches slash-command convention; fast | Token must be first thing in the message |

## Decision
The token must be at position 0 of the latest user message content. For string content, the regex `^/optiproxai:(\w+)\s*` anchors to the start. For list content, the token must be at the start of the first `{"type": "text", "text": ...}` part. No scanning of later parts.

## Rationale
Slash commands conventionally start a message. Scanning the full content would risk false positives (e.g., a user discussing the feature in prose). Position 0 is unambiguous and matches user expectations for command syntax. The first-text-part rule for list content keeps the logic simple and predictable.

## Consequences
A `/optiproxai:reasoning` token not at the very start of the message is treated as normal text (no override, no stripping). Users must place the token first. List-content messages with the token in a non-first text part do not trigger an override.
