from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from optiproxai import dashboard
from optiproxai.proxy import app, configure


@pytest.fixture
def configured_dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIPROXAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPTIPROXAI_LOG_DIR", str(tmp_path / "log"))
    config = tmp_path / "config.yaml"
    config.write_text(
        """
host: "0.0.0.0"
port: 18420
default_provider: dummy
default_profile: auto
providers:
  dummy:
    name: dummy
    base_url: "http://localhost:9999"
    api_key: "fake"
profiles:
  auto:
    tiers:
      SIMPLE: {primary: "auto-simple", fallback: [], provider: default}
      MEDIUM: {primary: "auto-medium", fallback: [], provider: default}
      COMPLEX: {primary: "auto-complex", fallback: [], provider: default}
      REASONING: {primary: "auto-reason", fallback: [], provider: default}
  eco:
    tiers:
      SIMPLE: {primary: "eco-simple", fallback: [], provider: default}
      MEDIUM: {primary: "eco-medium", fallback: [], provider: default}
      COMPLEX: {primary: "eco-complex", fallback: [], provider: default}
      REASONING: {primary: "eco-reason", fallback: [], provider: default}
  premium:
    tiers:
      SIMPLE: {primary: "premium-simple", fallback: [], provider: default}
      MEDIUM: {primary: "premium-medium", fallback: [], provider: default}
      COMPLEX: {primary: "premium-complex", fallback: [], provider: default}
      REASONING: {primary: "premium-reason", fallback: [], provider: default}
  compress:
    tiers:
      SIMPLE: {primary: "compress-simple", fallback: [], provider: default}
      MEDIUM: {primary: "compress-medium", fallback: [], provider: default}
      COMPLEX: {primary: "compress-complex", fallback: [], provider: default}
      REASONING: {primary: "compress-reason", fallback: [], provider: default}
"""
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dashboard, "_DASHBOARD_DB_PATH", tmp_path / "dashboard.db")
    configure(str(config))
    dashboard._init_dashboard_db()
    return tmp_path / "dashboard.db"


@pytest.fixture
def seeded_dashboard(configured_dashboard):
    now = datetime.now(timezone.utc)
    routing_rows = [
        {
            "timestamp": (now - timedelta(minutes=5)).isoformat(),
            "tier": "SIMPLE",
            "score": 0.2,
            "confidence": 0.93,
            "agentic_score": 0.1,
            "model": "auto-simple",
            "provider": "dummy",
            "profile": "auto",
            "signals": {"length": 1},
        },
        {
            "timestamp": (now - timedelta(minutes=4)).isoformat(),
            "tier": "MEDIUM",
            "score": 0.55,
            "confidence": 0.82,
            "agentic_score": 0.4,
            "model": "eco-medium",
            "provider": "dummy",
            "profile": "eco",
            "signals": {"tools": 1},
        },
        {
            "timestamp": (now - timedelta(minutes=3)).isoformat(),
            "tier": "COMPLEX",
            "score": 0.88,
            "confidence": 0.74,
            "agentic_score": 0.8,
            "model": "premium-complex",
            "provider": "dummy",
            "profile": "premium",
            "signals": {"reasoning": 1},
        },
    ]
    execution_rows = [
        {
            "timestamp": (now - timedelta(minutes=5)).isoformat(),
            "request_id": "req-auto",
            "tier": "SIMPLE",
            "score": 0.2,
            "confidence": 0.93,
            "agentic_score": 0.1,
            "model": "auto-simple",
            "provider": "dummy",
            "profile": "auto",
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "elapsed_ms": 150,
        },
        {
            "timestamp": (now - timedelta(minutes=4)).isoformat(),
            "request_id": "req-eco",
            "tier": "MEDIUM",
            "score": 0.55,
            "confidence": 0.82,
            "agentic_score": 0.4,
            "model": "eco-medium",
            "provider": "dummy",
            "profile": "eco",
            "prompt_tokens": 200,
            "completion_tokens": 50,
            "total_tokens": 250,
            "elapsed_ms": 250,
        },
        {
            "timestamp": (now - timedelta(minutes=3)).isoformat(),
            "request_id": "req-premium",
            "tier": "COMPLEX",
            "score": 0.88,
            "confidence": 0.74,
            "agentic_score": 0.8,
            "model": "premium-complex",
            "provider": "dummy",
            "profile": "premium",
            "prompt_tokens": 500,
            "completion_tokens": 120,
            "total_tokens": 620,
            "elapsed_ms": 850,
        },
    ]
    with sqlite3.connect(configured_dashboard) as conn:
        conn.row_factory = sqlite3.Row
        for row in routing_rows:
            dashboard._insert_routing_record(conn, row)
        for row in execution_rows:
            dashboard._insert_execution_record(conn, row)
        conn.commit()
    return configured_dashboard


def test_get_dashboard_stats_filters_multiple_profiles(seeded_dashboard):
    stats = dashboard.get_dashboard_stats(hours=24, profiles=["auto", "eco"])

    assert stats["selected_profiles"] == ["auto", "eco"]
    assert stats["available_profiles"] == ["auto", "compress", "eco", "premium"]
    assert stats["total_requests"] == 2
    assert stats["tier_distribution"] == {"MEDIUM": 1, "SIMPLE": 1}
    assert stats["windows"]["24h"]["total_tokens"] == 370
    assert [row["model"] for row in stats["model_usage"]["24h"]] == [
        "eco-medium",
        "auto-simple",
    ]
    assert stats["model_usage"]["24h"][0]["avg_tps"] == 1000.0
    assert stats["model_usage"]["24h"][1]["avg_tps"] == 800.0


def test_model_usage_rows_average_tps_ignores_non_positive_elapsed(
    configured_dashboard,
):
    now = datetime.now(timezone.utc)
    rows = [
        {
            "timestamp": (now - timedelta(minutes=2)).isoformat(),
            "request_id": "req-1",
            "tier": "SIMPLE",
            "score": 0.2,
            "confidence": 0.9,
            "agentic_score": 0.1,
            "model": "m1",
            "provider": "p1",
            "profile": "auto",
            "prompt_tokens": 80,
            "completion_tokens": 20,
            "total_tokens": 100,
            "elapsed_ms": 100.0,
        },
        {
            "timestamp": (now - timedelta(minutes=1)).isoformat(),
            "request_id": "req-2",
            "tier": "SIMPLE",
            "score": 0.2,
            "confidence": 0.9,
            "agentic_score": 0.1,
            "model": "m1",
            "provider": "p1",
            "profile": "auto",
            "prompt_tokens": 160,
            "completion_tokens": 40,
            "total_tokens": 200,
            "elapsed_ms": 400.0,
        },
        {
            "timestamp": now.isoformat(),
            "request_id": "req-3",
            "tier": "SIMPLE",
            "score": 0.2,
            "confidence": 0.9,
            "agentic_score": 0.1,
            "model": "m1",
            "provider": "p1",
            "profile": "auto",
            "prompt_tokens": 40,
            "completion_tokens": 10,
            "total_tokens": 50,
            "elapsed_ms": 0.0,
        },
    ]
    with sqlite3.connect(configured_dashboard) as conn:
        conn.row_factory = sqlite3.Row
        for row in rows:
            dashboard._insert_execution_record(conn, row)
        conn.commit()

    with sqlite3.connect(configured_dashboard) as conn:
        conn.row_factory = sqlite3.Row
        usage_rows = dashboard._model_usage_rows(conn, 24)

    assert usage_rows == [
        {
            "model": "m1",
            "provider": "p1",
            "count": 3,
            "prompt_tokens": 280,
            "completion_tokens": 70,
            "total_tokens": 350,
            "avg_elapsed_ms": 166.7,
            "avg_tps": 750.0,
        }
    ]


def test_render_dashboard_html_shows_profile_filter_controls():
    html = dashboard.render_dashboard_html(
        {
            "period_hours": 24,
            "total_requests": 2,
            "tier_distribution": {"SIMPLE": 1, "MEDIUM": 1},
            "avg_scores_by_tier": [],
            "confidence_distribution": {"90-100%": 1},
            "windows": {
                "24h": {
                    "routing_requests": 2,
                    "execution_requests": 2,
                    "prompt_tokens": 300,
                    "completion_tokens": 70,
                    "total_tokens": 370,
                    "avg_elapsed_ms": 200.0,
                    "usage_coverage": 1.0,
                },
                "7d": {
                    "routing_requests": 2,
                    "execution_requests": 2,
                    "prompt_tokens": 300,
                    "completion_tokens": 70,
                    "total_tokens": 370,
                    "avg_elapsed_ms": 200.0,
                    "usage_coverage": 1.0,
                },
                "30d": {
                    "routing_requests": 2,
                    "execution_requests": 2,
                    "prompt_tokens": 300,
                    "completion_tokens": 70,
                    "total_tokens": 370,
                    "avg_elapsed_ms": 200.0,
                    "usage_coverage": 1.0,
                },
            },
            "model_usage": {"24h": [], "7d": [], "30d": []},
            "daily_trends": [],
            "last_updated_at": None,
            "available_profiles": ["auto", "compress", "eco", "premium"],
            "selected_profiles": ["auto", "premium"],
        }
    )

    assert 'name="profiles"' in html
    assert 'value="auto" checked' in html
    assert 'value="premium" checked' in html
    assert 'value="eco"' in html
    assert "Apply" in html
    assert "Clear" in html
    assert 'rel="icon"' in html
    assert "OPX" in html
    assert "Latest:" in html
    assert 'id="combined-trend-chart"' in html
    assert "renderCombinedTrendChart" in html
    assert "requests-chart" not in html
    assert "tokens-chart" not in html
    assert "input-tokens-chart" not in html
    assert "output-tokens-chart" not in html


def test_ingest_stderr_proxy_logs_enriches_legacy_routing_profile(
    configured_dashboard, tmp_path
):
    now = datetime.now(timezone.utc)
    route_ts = (now - timedelta(minutes=2)).replace(microsecond=0)
    route_iso = route_ts.isoformat()

    with sqlite3.connect(configured_dashboard) as conn:
        dashboard._insert_routing_record(
            conn,
            {
                "timestamp": route_iso,
                "tier": "SIMPLE",
                "score": 0.2,
                "confidence": 0.93,
                "agentic_score": 0.1,
                "model": None,
                "provider": None,
                "profile": None,
                "signals": {},
            },
        )
        conn.commit()

    stderr_log = tmp_path / "log" / "launchd-stderr.log"
    stderr_log.parent.mkdir(parents=True, exist_ok=True)
    local_ts = route_ts.astimezone().strftime("%Y-%m-%d %H:%M:%S,%f")
    stderr_log.write_text(
        f"{local_ts} [INFO] optiproxai.proxy: ROUTE request_id=req1 model=auto-simple provider=dummy tier=SIMPLE score=0.2000 confidence=0.9300 agentic=0.1000 profile=auto\n",
        encoding="utf-8",
    )

    inserted = dashboard.ingest_stderr_proxy_logs()

    assert inserted == 1
    with sqlite3.connect(configured_dashboard) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT model, provider, profile FROM routing_logs WHERE timestamp = ?",
            (route_iso,),
        ).fetchone()

    assert row["model"] == "auto-simple"
    assert row["provider"] == "dummy"
    assert row["profile"] == "auto"


def test_dashboard_stats_endpoint_accepts_repeated_profile_query_params(
    seeded_dashboard,
):
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/dashboard/stats?hours=24&profiles=auto&profiles=eco")

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_profiles"] == ["auto", "eco"]
    assert payload["total_requests"] == 2
    assert payload["windows"]["24h"]["prompt_tokens"] == 300


def test_dashboard_endpoint_tolerates_invalid_utf8_in_routing_logs(
    configured_dashboard, tmp_path
):
    now = datetime.now(timezone.utc)
    log_file = tmp_path / "log" / f"routing-{now.strftime('%Y-%m-%d')}.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    valid = (
        '{"timestamp":"%s","tier":"SIMPLE","score":0.2,'
        '"confidence":0.93,"agentic_score":0.1,"signals":{}}\n'
    ) % now.isoformat()
    log_file.write_bytes(valid.encode("utf-8") + b"\xe3broken\n")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/dashboard")

    assert response.status_code == 200


def test_render_model_usage_table_shows_avg_tps_column():
    rows = [
        {
            "model": "m1",
            "provider": "p1",
            "count": 2,
            "prompt_tokens": 280,
            "completion_tokens": 70,
            "total_tokens": 350,
            "avg_elapsed_ms": 250.0,
            "avg_tps": 750.0,
        },
        {
            "model": "m2",
            "provider": "p2",
            "count": 1,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "avg_elapsed_ms": None,
            "avg_tps": None,
        },
    ]

    html = dashboard._render_model_usage_table(rows)

    assert "AVG TPS" in html
    assert ">750.0<" in html
    assert ">-<" in html


class TestCacheUsageExtraction:
    """Provider field-name tolerance of proxy._extract_cache_usage."""

    def test_anthropic_shape(self):
        from optiproxai.proxy import _extract_cache_usage

        usage = {
            "prompt_tokens": 1000,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 200,
        }
        assert _extract_cache_usage(usage) == (0, 800, 200)

    def test_openai_nested_shape(self):
        from optiproxai.proxy import _extract_cache_usage

        usage = {
            "prompt_tokens": 4000,
            "prompt_tokens_details": {"cached_tokens": 2048},
        }
        assert _extract_cache_usage(usage) == (2048, 0, 0)

    def test_synthetic_top_level_shape(self):
        from optiproxai.proxy import _extract_cache_usage

        assert _extract_cache_usage({"cached_tokens": 512}) == (512, 0, 0)

    def test_ollamacloud_no_cache_data(self):
        from optiproxai.proxy import _extract_cache_usage

        assert _extract_cache_usage({"prompt_tokens": 100}) == (0, 0, 0)

    def test_none_and_junk_coerce_to_zero(self):
        from optiproxai.proxy import _extract_cache_usage

        assert _extract_cache_usage(None) == (0, 0, 0)
        assert _extract_cache_usage({}) == (0, 0, 0)
        assert _extract_cache_usage({"cached_tokens": "oops"}) == (0, 0, 0)
        assert _extract_cache_usage({"prompt_tokens_details": None}) == (0, 0, 0)

    def test_doubleword_double_report_kept_raw(self):
        from optiproxai.proxy import _extract_cache_usage

        usage = {
            "cached_tokens": 2048,
            "cache_read_input_tokens": 2048,
            "cache_creation_input_tokens": 0,
        }
        assert _extract_cache_usage(usage) == (2048, 2048, 0)


class TestCachePersistence:
    """execution_logs cache columns, insert, and JSONL round-trip."""

    def test_insert_and_read_cache_columns(self, configured_dashboard):
        now = datetime.now(timezone.utc)
        with sqlite3.connect(configured_dashboard) as conn:
            dashboard._insert_execution_record(
                conn,
                {
                    "timestamp": now.isoformat(),
                    "model": "m1",
                    "provider": "p1",
                    "profile": "auto",
                    "prompt_tokens": 1000,
                    "completion_tokens": 10,
                    "total_tokens": 1010,
                    "cached_tokens": 0,
                    "cache_read_input_tokens": 800,
                    "cache_creation_input_tokens": 200,
                },
            )
            conn.commit()
            row = conn.execute(
                "SELECT cached_tokens, cache_read_input_tokens, "
                "cache_creation_input_tokens FROM execution_logs"
            ).fetchone()

        assert row == (0, 800, 200)

    def test_old_jsonl_without_cache_fields_ingests_as_zero(
        self, configured_dashboard, tmp_path
    ):
        now = datetime.now(timezone.utc)
        log_file = tmp_path / "log" / f"execution-{now.strftime('%Y-%m-%d')}.jsonl"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(
            json.dumps(
                {
                    "timestamp": now.isoformat(),
                    "model": "legacy",
                    "provider": "p1",
                    "profile": "auto",
                    "prompt_tokens": 50,
                    "completion_tokens": 5,
                    "total_tokens": 55,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        inserted = dashboard.ingest_execution_logs(days=1)

        assert inserted == 1
        with sqlite3.connect(configured_dashboard) as conn:
            row = conn.execute(
                "SELECT cached_tokens, cache_read_input_tokens, "
                "cache_creation_input_tokens FROM execution_logs"
            ).fetchone()

        assert row == (0, 0, 0)

    def test_log_execution_event_persists_cache_fields(self, configured_dashboard):
        dashboard.log_execution_event(
            request_id="req-cache",
            model="m1",
            provider="p1",
            profile="auto",
            prompt_tokens=1000,
            completion_tokens=10,
            total_tokens=1010,
            cached_tokens=0,
            cache_read_input_tokens=700,
            cache_creation_input_tokens=300,
        )

        with sqlite3.connect(configured_dashboard) as conn:
            row = conn.execute(
                "SELECT cached_tokens, cache_read_input_tokens, "
                "cache_creation_input_tokens FROM execution_logs"
            ).fetchone()

        assert row == (0, 700, 300)


class TestCacheQueries:
    """_cache_summary and _cache_model_rows math incl. double-count guard."""

    def _seed(self, db_path, rows):
        now = datetime.now(timezone.utc)
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            for offset, row in enumerate(rows):
                record = {
                    "timestamp": (now - timedelta(minutes=offset + 1)).isoformat(),
                    "model": row.get("model", "m1"),
                    "provider": row.get("provider", "p1"),
                    "profile": row.get("profile", "auto"),
                    "prompt_tokens": row.get("prompt_tokens", 0),
                    "completion_tokens": 0,
                    "total_tokens": row.get("prompt_tokens", 0),
                    "cached_tokens": row.get("cached_tokens", 0),
                    "cache_read_input_tokens": row.get("cache_read_input_tokens", 0),
                    "cache_creation_input_tokens": row.get(
                        "cache_creation_input_tokens", 0
                    ),
                }
                dashboard._insert_execution_record(conn, record)
            conn.commit()

    def test_summary_hit_rate_and_double_report_guard(self, configured_dashboard):
        self._seed(
            configured_dashboard,
            [
                # Anthropic shape
                {
                    "model": "claude",
                    "prompt_tokens": 1000,
                    "cache_read_input_tokens": 800,
                    "cache_creation_input_tokens": 200,
                },
                # Doubleword double-report: counted once, not twice
                {
                    "model": "deepseek",
                    "prompt_tokens": 4000,
                    "cached_tokens": 2048,
                    "cache_read_input_tokens": 2048,
                },
                # No cache activity
                {"model": "llama", "prompt_tokens": 100},
            ],
        )

        with sqlite3.connect(configured_dashboard) as conn:
            conn.row_factory = sqlite3.Row
            summary = dashboard._cache_summary(conn, 24)

        assert summary["requests"] == 3
        assert summary["cache_hits"] == 2
        # 800 + 2048 (guard counts the double-report row once)
        assert summary["cache_read_tokens"] == 2848
        assert summary["cache_creation_tokens"] == 200
        assert summary["prompt_tokens"] == 5100
        assert summary["hit_rate"] == round(2848 / 5100, 4)

    def test_summary_zero_prompt_tokens_no_division_error(self, configured_dashboard):
        self._seed(configured_dashboard, [{"model": "m1", "prompt_tokens": 0}])

        with sqlite3.connect(configured_dashboard) as conn:
            conn.row_factory = sqlite3.Row
            summary = dashboard._cache_summary(conn, 24)

        assert summary["hit_rate"] == 0.0

    def test_model_rows_grouping_and_profile_filter(self, configured_dashboard):
        self._seed(
            configured_dashboard,
            [
                {
                    "model": "m1",
                    "provider": "p1",
                    "profile": "auto",
                    "prompt_tokens": 1000,
                    "cache_read_input_tokens": 800,
                },
                {
                    "model": "m2",
                    "provider": "p2",
                    "profile": "eco",
                    "prompt_tokens": 100,
                    "cached_tokens": 50,
                },
            ],
        )

        with sqlite3.connect(configured_dashboard) as conn:
            conn.row_factory = sqlite3.Row
            all_rows = dashboard._cache_model_rows(conn, 24)
            auto_rows = dashboard._cache_model_rows(conn, 24, profiles=["auto"])

        assert {row["model"] for row in all_rows} == {"m1", "m2"}
        assert [row["model"] for row in auto_rows] == ["m1"]
        assert auto_rows[0]["hit_rate"] == 0.8
        assert auto_rows[0]["cache_read_tokens"] == 800

    def test_stats_expose_cache_keys(self, seeded_dashboard):
        stats = dashboard.get_dashboard_stats(hours=24)

        assert set(stats["cache_summary"].keys()) == {"24h", "7d", "30d"}
        assert set(stats["cache_model_usage"].keys()) == {"24h", "7d", "30d"}
        assert stats["cache_summary"]["24h"]["requests"] == 3


class TestCachePricingAndRender:
    """Pricing resolution, savings estimate, and rendered HTML output."""

    @pytest.fixture
    def priced_dashboard(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPTIPROXAI_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("OPTIPROXAI_LOG_DIR", str(tmp_path / "log"))
        config = tmp_path / "config.yaml"
        config.write_text(
            """
host: "0.0.0.0"
port: 18420
default_provider: dummy
default_profile: auto
providers:
  dummy:
    name: dummy
    base_url: "http://localhost:9999"
    api_key: "fake"
model_rules:
  - prefix: "rule-model"
    pricing:
      input_per_mtok: 1.00
      cache_read_per_mtok: 0.10
      cache_write_per_mtok: 2.00
profiles:
  auto:
    tiers:
      MEDIUM:
        primary:
          - model: "entry-model"
            pricing:
              input_per_mtok: 3.00
              cache_read_per_mtok: 0.30
"""
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(dashboard, "_DASHBOARD_DB_PATH", tmp_path / "dashboard.db")
        configure(str(config))
        dashboard._init_dashboard_db()
        return tmp_path / "dashboard.db"

    def test_pricing_resolution_entry_and_rule(self, priced_dashboard):
        entry_pricing = dashboard._resolve_model_pricing("entry-model")
        rule_pricing = dashboard._resolve_model_pricing("rule-model-x")

        assert entry_pricing is not None
        assert entry_pricing.input_per_mtok == 3.00
        assert rule_pricing is not None
        assert rule_pricing.cache_read_per_mtok == 0.10

    def test_pricing_resolution_unknown_model(self, priced_dashboard):
        assert dashboard._resolve_model_pricing("unknown-model") is None
        assert dashboard._resolve_model_pricing(None) is None

    def test_savings_estimate_math(self):
        from optiproxai.config import ModelPricingConfig

        pricing = ModelPricingConfig(
            input_per_mtok=3.0,
            cache_read_per_mtok=0.3,
            cache_write_per_mtok=3.75,
        )
        # read: (3.0 - 0.3) * 1_000_000 / 1e6 = 2.70
        # write: (3.75 - 3.0) * 400_000 / 1e6 = 0.30
        savings = dashboard._estimate_cache_savings(pricing, 1_000_000, 400_000)
        assert savings == pytest.approx(2.40)

    def test_savings_estimate_unpriced_returns_none(self):
        assert dashboard._estimate_cache_savings(None, 1000, 0) is None

        from optiproxai.config import ModelPricingConfig

        assert (
            dashboard._estimate_cache_savings(ModelPricingConfig(), 1000, 100) is None
        )

    def test_rendered_html_contains_cache_section(self, priced_dashboard):
        now = datetime.now(timezone.utc)
        with sqlite3.connect(priced_dashboard) as conn:
            conn.row_factory = sqlite3.Row
            dashboard._insert_execution_record(
                conn,
                {
                    "timestamp": now.isoformat(),
                    "model": "entry-model",
                    "provider": "dummy",
                    "profile": "auto",
                    "prompt_tokens": 1000,
                    "completion_tokens": 10,
                    "total_tokens": 1010,
                    "cached_tokens": 0,
                    "cache_read_input_tokens": 800,
                    "cache_creation_input_tokens": 200,
                },
            )
            conn.commit()

        stats = dashboard.get_dashboard_stats(hours=24)
        html = dashboard.render_dashboard_html(stats)

        assert "Cache Metrics" in html
        assert "Cache hit rate" in html
        assert "Cache by Model (7d)" in html
        assert "Est. savings" in html
        # 800 / 1000 = 80%
        assert "80%" in html
        # Savings: (3.0 - 0.3) * 800/1e6 - 0 = 0.00216 -> $0.0022
        assert "$0.0022" in html

    def test_rendered_html_savings_dash_without_pricing(self, configured_dashboard):
        now = datetime.now(timezone.utc)
        with sqlite3.connect(configured_dashboard) as conn:
            conn.row_factory = sqlite3.Row
            dashboard._insert_execution_record(
                conn,
                {
                    "timestamp": now.isoformat(),
                    "model": "auto-simple",
                    "provider": "dummy",
                    "profile": "auto",
                    "prompt_tokens": 1000,
                    "completion_tokens": 10,
                    "total_tokens": 1010,
                    "cache_read_input_tokens": 500,
                },
            )
            conn.commit()

        stats = dashboard.get_dashboard_stats(hours=24)
        html = dashboard.render_dashboard_html(stats)

        assert "Cache Metrics" in html
        # Unpriced model renders em-dash savings
        assert "$" not in html.split("Est. savings")[1].split("</table>")[0]
