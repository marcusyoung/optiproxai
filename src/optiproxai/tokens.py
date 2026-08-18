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


def _estimate_tokens(messages: list[dict[str, Any]], model: str | None = None) -> int:
    """Estimate token count for a message list using tiktoken when available.

    Falls back to chars/4 if tiktoken cannot resolve an encoding.
    """
    enc = _get_encoder(model)
    if enc is not None:
        total = sum(
            len(enc.encode(str(m.get("content", "")), allowed_special="all"))
            for m in messages
        )
    else:
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        total = total_chars // _CHARS_PER_TOKEN
    return max(1, total)
