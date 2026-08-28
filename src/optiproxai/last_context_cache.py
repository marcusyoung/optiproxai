"""Process-local cache of the last provider-reported prompt size per session."""

from __future__ import annotations

from threading import Lock

_DEFAULT_MAX_SESSIONS = 10_000


class LastContextCache:
    """Track the last provider-reported prompt token count keyed by session.

    Provider ``usage.prompt_tokens`` is ground truth for what a provider
    actually received on a prior turn (including ``reasoning_content`` and
    provider-side template overhead that live estimation cannot see). Caching
    it per session lets the router gate ``max_input_tokens`` against the
    largest prompt the conversation has produced so far.

    Entries are bounded: when ``max_sessions`` is reached, the least recently
    used session is evicted, so a high-cardinality deployment cannot grow
    memory without limit.
    """

    def __init__(self, max_sessions: int = _DEFAULT_MAX_SESSIONS) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be >= 1")
        self._entries: dict[str, int] = {}
        self._lock = Lock()
        self._max_sessions = max_sessions

    def record(self, session_key: str, prompt_tokens: int) -> None:
        """Store the provider-reported prompt size for a session."""
        with self._lock:
            if session_key in self._entries:
                del self._entries[session_key]
            elif len(self._entries) >= self._max_sessions:
                self._entries.pop(next(iter(self._entries)))
            self._entries[session_key] = prompt_tokens

    def get(self, session_key: str) -> int | None:
        """Return the last recorded prompt size for a session, or None.

        Refreshes recency so recently-read sessions are evicted last.
        """
        with self._lock:
            value = self._entries.get(session_key)
            if value is not None:
                del self._entries[session_key]
                self._entries[session_key] = value
            return value

    def clear(self) -> None:
        """Drop all cached entries (test helper)."""
        with self._lock:
            self._entries.clear()


# Process-local singleton, mirroring the lifetime of FallbackBackoffState.
last_context_cache = LastContextCache()
