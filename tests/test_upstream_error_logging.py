"""Tests for upstream error event logging (TASK-20.01).

Non-200 upstream responses must be persisted to the execution JSONL stream
(and dashboard DB) with status, provider, and a bounded body excerpt, plus a
server-log WARNING line — with zero change to routing/cooldown behavior.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.responses import JSONResponse

from optiproxai import dashboard, proxy
from optiproxai.dirs import log_dir


def _read_execution_error_events(log_directory: Path) -> list[dict]:
    """Read upstream_error events from all execution JSONL files in log_dir."""
    events = []
    for log_file in sorted(log_directory.glob("execution-*.jsonl")):
        for line in log_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("event_type") == "upstream_error":
                events.append(record)
    return events


def _upstream_response(
    status_code: int = 429,
    body: str = '{"error":"You\'ve exceeded your subscription rate limits."}',
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Build an httpx.Response like an upstream provider error."""
    return httpx.Response(
        status_code,
        headers=headers or {},
        content=body.encode("utf-8"),
        request=httpx.Request("POST", "https://upstream.test/v1/chat/completions"),
    )


class TestLogUpstreamErrorHelper:
    def test_writes_jsonl_event_with_all_fields(self, tmp_path) -> None:
        proxy._log_upstream_error(
            model_name="syn:large:vision",
            actual_provider="synthetic",
            status_code=429,
            raw_body='{"error":"You\'ve exceeded your subscription rate limits."}',
            headers=httpx.Headers({"retry-after": "30"}),
            request_id="abc123",
            profile="analysis",
        )

        events = _read_execution_error_events(log_dir())
        assert len(events) == 1
        event = events[0]
        assert event["event_type"] == "upstream_error"
        assert event["request_id"] == "abc123"
        assert event["model"] == "syn:large:vision"
        assert event["provider"] == "synthetic"
        assert event["profile"] == "analysis"
        assert event["status_code"] == 429
        assert event["error_type"] == "upstream_error"
        assert "subscription rate limits" in event["body_excerpt"]
        assert event["retry_after"] == "30"

    def test_body_excerpt_truncated_to_500_chars(self) -> None:
        proxy._log_upstream_error(
            model_name="m",
            actual_provider="p",
            status_code=500,
            raw_body="x" * 2000,
            headers=httpx.Headers(),
        )

        events = _read_execution_error_events(log_dir())
        assert len(events) == 1
        assert len(events[0]["body_excerpt"]) == 500

    def test_missing_retry_after_is_none(self) -> None:
        proxy._log_upstream_error(
            model_name="m",
            actual_provider="p",
            status_code=503,
            raw_body="service unavailable",
            headers=httpx.Headers(),
        )

        events = _read_execution_error_events(log_dir())
        assert events[0]["retry_after"] is None

    def test_empty_body_yields_empty_excerpt(self) -> None:
        proxy._log_upstream_error(
            model_name="m",
            actual_provider="p",
            status_code=500,
            raw_body="",
            headers=httpx.Headers(),
        )

        events = _read_execution_error_events(log_dir())
        assert events[0]["body_excerpt"] == ""

    def test_non_utf8_body_replaced_safely(self) -> None:
        proxy._log_upstream_error(
            model_name="m",
            actual_provider="p",
            status_code=500,
            raw_body=b"\xff\xfe binary".decode("utf-8", errors="replace"),
            headers=httpx.Headers(),
        )

        events = _read_execution_error_events(log_dir())
        assert "\ufffd" in events[0]["body_excerpt"]

    def test_missing_headers_does_not_raise(self) -> None:
        proxy._log_upstream_error(
            model_name="m",
            actual_provider="p",
            status_code=500,
            raw_body="oops",
            headers=None,
        )

        events = _read_execution_error_events(log_dir())
        assert events[0]["retry_after"] is None

    def test_emits_server_log_warning(self, caplog) -> None:
        import logging

        with caplog.at_level(logging.WARNING, logger="optiproxai.proxy"):
            proxy._log_upstream_error(
                model_name="syn:large:vision",
                actual_provider="synthetic",
                status_code=429,
                raw_body='{"error":"exceeded"}',
                headers=httpx.Headers(),
                request_id="req-1",
            )

        warnings = [
            r for r in caplog.records if r.getMessage().startswith("UPSTREAM_ERROR")
        ]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "status=429" in message
        assert "provider=synthetic" in message
        assert "model=syn:large:vision" in message

    def test_persists_to_dashboard_db(self, tmp_path) -> None:
        proxy._log_upstream_error(
            model_name="m",
            actual_provider="p",
            status_code=429,
            raw_body="quota",
            headers=httpx.Headers(),
        )

        import sqlite3

        with sqlite3.connect(dashboard._DASHBOARD_DB_PATH) as conn:
            rows = conn.execute(
                "SELECT event_type, status_code, error_type, body_excerpt "
                "FROM execution_logs WHERE event_type = 'upstream_error'"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "upstream_error"
        assert rows[0][1] == 429


class TestProxyUpstreamWiring:
    @pytest.fixture
    def proxy_http(self, monkeypatch):
        """Patch the module-level httpx client used by _proxy_upstream."""
        calls: dict = {}

        class _FakeAsyncClient:
            def __init__(self) -> None:
                pass

            def build_request(self, *args, **kwargs):
                return httpx.Request(
                    "POST", "https://upstream.test/v1/chat/completions"
                )

            async def send(self, request, stream=False):
                calls["stream"] = stream
                return _upstream_response(status_code=429)

            async def post(self, *args, **kwargs):
                calls["stream"] = False
                return _upstream_response(status_code=429)

        monkeypatch.setattr(proxy, "_http", _FakeAsyncClient())
        return calls

    @pytest.mark.anyio
    async def test_non_streaming_non_200_emits_error_event(
        self, proxy_http, tmp_path
    ) -> None:
        result = await proxy._proxy_upstream(
            "https://upstream.test/v1",
            "key",
            {"model": "syn:large:vision"},
            decision=None,
            profile="analysis",
            actual_provider="synthetic",
            request_id="req-42",
        )

        assert isinstance(result, JSONResponse)
        assert result.status_code == 429
        events = _read_execution_error_events(log_dir())
        assert len(events) == 1
        assert events[0]["provider"] == "synthetic"
        assert events[0]["status_code"] == 429
        assert "subscription rate limits" in events[0]["body_excerpt"]

    @pytest.mark.anyio
    async def test_streaming_non_200_emits_error_event(
        self, proxy_http, tmp_path
    ) -> None:
        body = {"model": "syn:large:vision", "stream": True}
        result = await proxy._proxy_upstream(
            "https://upstream.test/v1",
            "key",
            body,
            decision=None,
            profile="analysis",
            actual_provider="synthetic",
            request_id="req-42",
        )

        assert isinstance(result, JSONResponse)
        events = _read_execution_error_events(log_dir())
        assert len(events) == 1
        assert events[0]["provider"] == "synthetic"


class TestNoBehaviorChange:
    def test_error_event_does_not_apply_fallback_cooldown(self) -> None:
        """Logging an upstream error must not touch the backoff state (AC #6)."""
        from optiproxai.config import FallbackBackoffConfig
        from optiproxai.fallback_backoff import FallbackBackoffState

        state = FallbackBackoffState(FallbackBackoffConfig())
        proxy._log_upstream_error(
            model_name="m",
            actual_provider="p",
            status_code=429,
            raw_body="body",
            headers=httpx.Headers(),
        )
        assert state.get_entry("m", "p") is None

    def test_log_execution_error_shape(self) -> None:
        dashboard.log_execution_error(
            request_id="r1",
            model="m",
            provider="p",
            profile="auto",
            status_code=429,
            error_type="upstream_error",
            body_excerpt="b" * 600,
            retry_after="15",
        )
        events = _read_execution_error_events(log_dir())
        assert len(events) == 1
        assert events[0]["body_excerpt"] == "b" * 500
        assert events[0]["event_type"] == "upstream_error"
