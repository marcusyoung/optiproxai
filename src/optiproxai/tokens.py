"""Shared token estimation helpers for routing decisions."""

from __future__ import annotations

from typing import Any

import tiktoken

_CHARS_PER_TOKEN = 4  # fallback when tiktoken is unavailable

# Module-level cache: model name (or "") → tiktoken Encoding | None
_encoder_cache: dict[str, tiktoken.Encoding | None] = {}


def _get_encoder(model: str | None) -> tiktoken.Encoding | None:
    """Return a cached tiktoken Encoding for model, falling back to cl100k_base."""
    key = model or ""
    if key in _encoder_cache:
        return _encoder_cache[key]
    try:
        enc: tiktoken.Encoding | None = (
            tiktoken.encoding_for_model(model)
            if model
            else tiktoken.get_encoding("cl100k_base")
        )
    except KeyError:
        # Unknown model name — fall back to cl100k_base
        try:
            enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            enc = None
    except Exception:
        enc = None
    _encoder_cache[key] = enc
    return enc


def _count_str_tokens(
    enc: tiktoken.Encoding | None,
    value: Any,
) -> int:
    """Count tokens for an arbitrary value (string or JSON-ish object)."""
    if value is None:
        return 0
    if isinstance(value, str):
        text = value
    else:
        text = str(value)
    if enc is not None:
        return len(enc.encode(text, allowed_special="all"))
    return len(text) // _CHARS_PER_TOKEN


def _estimate_tools_tokens(
    tools: list[dict[str, Any]] | None,
    enc: tiktoken.Encoding | None,
) -> int:
    """Estimate the token cost of a ``tools``/``functions`` schema array.

    Tool definitions are sent verbatim upstream, so counting their serialized
    form is required for an accurate prompt-size estimate (they are *not*
    part of ``messages``).
    """
    if not tools:
        return 0
    total = 0
    for tool in tools:
        total += _count_str_tokens(enc, tool)
        # Serialization overhead per tool (~"type","function","name","parameters").
        total += 4 if enc is not None else 1
    return total


def _estimate_tokens(
    messages: list[dict[str, Any]],
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """Estimate token count for a message list using tiktoken when available.

    Counts every field the provider receives: ``role``, ``name``,
    ``tool_call_id``, ``tool_calls`` arguments, and ``content`` for each
    message, plus the ``tools``/``functions`` schema when supplied. Falls back
    to ``chars/4`` if tiktoken cannot resolve an encoding.

    Args:
        messages: OpenAI-style message list.
        model: Optional model name for tiktoken encoding selection.
        tools: Optional ``tools`` array (OpenAI tool schemas) sent upstream.
    """
    enc = _get_encoder(model)
    total = 0
    for m in messages:
        total += _count_str_tokens(enc, m.get("content", ""))
        total += _count_str_tokens(enc, m.get("role"))
        total += _count_str_tokens(enc, m.get("name"))
        total += _count_str_tokens(enc, m.get("tool_call_id"))
        # tool_calls (assistant messages) carry structured call args.
        for tc in m.get("tool_calls") or []:
            total += _count_str_tokens(enc, tc.get("id"))
            total += _count_str_tokens(enc, tc.get("type"))
            fn = tc.get("function") or {}
            total += _count_str_tokens(enc, fn.get("name"))
            total += _count_str_tokens(enc, fn.get("arguments"))
            total += 2  # structural overhead
    total += _estimate_tools_tokens(tools, enc)
    return max(1, total)
