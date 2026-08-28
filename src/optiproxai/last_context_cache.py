"""Process-local cache of the last provider-reported prompt size per session."""

from __future__ import annotations

from threading import Lock


class LastContextCache:
    """Track the last provider-reported prompt token count keyed by session.

    Provider ``usage.prompt_tokens`` is ground truth for what a provider
    actually received on a prior turn (including ``reasoning_content`` and
    provider-side template overhead that live estimation cannot see). Caching
    it per session lets the router gate ``max_input_tokens`` against the
    largest prompt the conversation has produced so far.
    """

    def __init__(self) -> None:
        self._entries: dict[str, int] = {}
        self._lock = Lock()

    def record(self, session_key: str, prompt_tokens: int) -> None:
        """Store the provider-reported prompt size for a session."""
        with self._lock:
            self._entries[session_key] = prompt_tokens

    def get(self, session_key: str) -> int | None:
        """Return the last recorded prompt size for a session, or None."""
        with self._lock:
            return self._entries.get(session_key)

    def clear(self) -> None:
        """Drop all cached entries (test helper)."""
        with self._lock:
            self._entries.clear()


# Process-local singleton, mirroring the lifetime of FallbackBackoffState.
last_context_cache = LastContextCache()
