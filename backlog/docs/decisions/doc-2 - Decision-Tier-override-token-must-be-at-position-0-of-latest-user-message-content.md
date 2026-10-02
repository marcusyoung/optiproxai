---
id: doc-2
title: >-
  Decision: Tier override token must be at position 0 of latest user message
  content
type: other
created_date: '2026-08-17 12:25'
updated_date: '2026-10-02 13:29'
---
# Decision: Tier override token must be at position 0 of latest user message content

**Date:** 2026-08-17
**Status:** Decided (superseded by amendment 2026-10-02)
**Task:** TASK-1
**Author:** dev (optiproxai/code)

## Context
The `/optiproxai:<tier>` token could theoretically appear anywhere in the message. The question is whether to scan the full content or only the start.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Scan entire message content | Flexible; token can appear anywhere | Ambiguous if user types `/optiproxai:reasoning` mid-sentence; false positives; more complex parsing |
| Position 0 only (regex `^/optiproxai:(\w+)\s*`) | Simple; unambiguous; matches slash-command convention; fast | Token must be first thing in the message |

## Decision
The token must be at position 0 of the latest user message content. For string content, the regex `^/optiproxai:(\w+)\s*` anchors to the start. For list content, the token must be at the start of the first `{"type": "text", "text": ...}` part. No scanning of later parts.

## Rationale
Slash commands conventionally start a message. Scanning the full content would risk false positives (e.g., a user discussing the feature in prose). Position 0 is unambiguous and matches user expectations for command syntax. The first-text-part rule for list content keeps the logic simple and predictable.

## Consequences
A `/optiproxai:reasoning` token not at the very start of the message is treated as normal text (no override, no stripping). Users must place the token first. List-content messages with the token in a non-first text part do not trigger an override.

---

# Amendment (2026-10-02): any-position `::<tier>` token supersedes position-0 `/optiproxai:<tier>`

**Date:** 2026-10-02
**Status:** Decided
**Context:** Production regression — the override silently never fired through Cursor.

## Why the position-0 decision broke
Cursor does not send the raw user text as the latest user turn. It wraps the message in a `<user_query>...</user_query>` envelope (corroborated by the leaked Cursor prompt, community request-shape analyses, and this project's own agent context). The position-0 regex `^/optiproxai:(\w+)` therefore never matched: the first character is `<`, not `/`. Symptom: the tier override was ignored for Cursor while the CLI/opencode path (raw text) kept working, so the existing test suite stayed green and hid the regression.

## New decision
- **Syntax:** `::<tier>` (e.g. `::reasoning`), matched **anywhere in the latest user message only** — never in assistant, system, or earlier user turns.
- **Regex:** `(?<![:\w])::(\w+)` (case-insensitive). The negative lookbehind keeps it a distinct token: it does not match mid-identifier (`std::reasoning`) or inside a longer colon run (`:::reasoning`), which preserves the false-positive protection this document originally sought.
- **Only the first match is honoured.** For list content, parts are scanned in order; the first text part containing a token wins.
- **The legacy `/optiproxai:<tier>` form is dropped.** The token is slash-free to avoid chat-client slash-command autocomplete and is position-independent to survive client-added wrappers. Migration: replace `/optiproxai:reasoning ` with `::reasoning` (anywhere in the message).

## Rationale for reversing "position 0 only"
The original rationale — avoid false positives from a token discussed in prose — is better served by token distinctiveness than by position. `::<tier>` with a left-boundary guard is unlikely to occur in prose/code and cannot collide with identifiers, so scanning the whole latest turn is safe and makes the feature client-agnostic. Position 0 was never the real source of unambiguity once a distinctive token exists.

## Consequences
- The token is stripped wherever it appears in the latest user message, so it never leaks upstream.
- Clients that wrap user text (Cursor `<user_query>`, and future wrappers) work unchanged.
- Per-turn override toggling (including via `/` command UIs) remains unsupported for the same reason as before: only the latest user message is scanned, so a marker routinely present in an earlier turn is not repeatedly enforced.
