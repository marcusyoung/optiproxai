"""Tests for the session-keyed last-context token cache."""

from __future__ import annotations

from optiproxai.last_context_cache import LastContextCache


class TestLastContextCache:
    def test_miss_returns_none(self) -> None:
        cache = LastContextCache()

        assert cache.get("missing-session") is None

    def test_record_then_get(self) -> None:
        cache = LastContextCache()
        cache.record("session-a", 170000)

        assert cache.get("session-a") == 170000

    def test_record_overwrites_with_latest(self) -> None:
        cache = LastContextCache()
        cache.record("session-a", 150000)
        cache.record("session-a", 185000)

        assert cache.get("session-a") == 185000

    def test_sessions_are_isolated(self) -> None:
        cache = LastContextCache()
        cache.record("session-a", 1000)
        cache.record("session-b", 2000)

        assert cache.get("session-a") == 1000
        assert cache.get("session-b") == 2000

    def test_clear_drops_all_entries(self) -> None:
        cache = LastContextCache()
        cache.record("session-a", 1000)
        cache.record("session-b", 2000)

        cache.clear()

        assert cache.get("session-a") is None
        assert cache.get("session-b") is None

    def test_zero_prompt_tokens_is_recorded(self) -> None:
        cache = LastContextCache()
        cache.record("session-a", 0)

        assert cache.get("session-a") == 0
