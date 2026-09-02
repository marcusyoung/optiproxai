from __future__ import annotations

import secrets
from pathlib import Path
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import optiproxai.proxy as proxy_mod
from optiproxai.config import (
    CacheControlConfig,
    ContentPartPolicy,
    OptiproxaiConfig,
    ModelRuleEntry,
    ProviderConfig,
)
from optiproxai.last_context_cache import LastContextCache
from optiproxai.proxy import RuntimeState, app, configure
from optiproxai.router import Router, RoutingDecision
from optiproxai.scorer import ClassificationResult, Tier


def _simple_classification(_: object, text: str) -> ClassificationResult:
    return ClassificationResult(
        score=0.0,
        tier=Tier.SIMPLE,
        confidence=0.9,
        signals={"method": {"raw": "test"}, "tokenCount": len(text.split())},
    )


def _config_text(
    *,
    host: str = "0.0.0.0",
    port: int = 18420,
    default_profile: str = "auto",
    include_alt_profile: bool = False,
    fallback_backoff_enabled: bool = False,
    tools_capability_detection: str | None = None,
    decorative_tool_schema_handling: str | None = None,
    input_limit_too_small: bool = False,
    primary_model: str = "auto-simple",
    fallback_models: str = "[]",
    provider_body: str = "",
    model_rules: str = "",
) -> str:
    alt_profile = ""
    if include_alt_profile:
        alt_profile = """
  premium:
    tiers:
      SIMPLE: {primary: \"premium-simple\", fallback: [], provider: default}
      MEDIUM: {primary: \"premium-medium\", fallback: [], provider: default}
      COMPLEX: {primary: \"premium-complex\", fallback: [], provider: default}
      REASONING: {primary: \"premium-reason\", fallback: [], provider: default}
"""

    smart_proxy_sections: list[str] = []
    smart_proxy_sections.append(
        f"""
  fallback_backoff:
    enabled: {str(fallback_backoff_enabled).lower()}
    initial_delay_seconds: 5
    multiplier: 2
    max_delay_seconds: 60
"""
    )
    if tools_capability_detection is not None:
        smart_proxy_sections.append(
            f"  tools_capability_detection: {tools_capability_detection}\n"
        )
    if decorative_tool_schema_handling is not None:
        smart_proxy_sections.append(
            f"  decorative_tool_schema_handling: {decorative_tool_schema_handling}\n"
        )

    smart_proxy = ""
    if smart_proxy_sections:
        smart_proxy = "smart_proxy:\n" + "".join(smart_proxy_sections)

    auto_simple = (
        f'primary: "{primary_model}", fallback: {fallback_models}, provider: default'
    )
    auto_medium = 'primary: "auto-medium", fallback: [], provider: default'
    auto_complex = 'primary: "auto-complex", fallback: [], provider: default'
    auto_reason = 'primary: "auto-reason", fallback: [], provider: default'
    if input_limit_too_small:
        auto_simple = (
            'primary: [{model: "tiny-simple", max_input_tokens: 1}], '
            "fallback: [], provider: default"
        )
        auto_medium = (
            'primary: [{model: "tiny-medium", max_input_tokens: 1}], '
            "fallback: [], provider: default"
        )
        auto_complex = (
            'primary: [{model: "tiny-complex", max_input_tokens: 1}], '
            "fallback: [], provider: default"
        )
        auto_reason = (
            'primary: [{model: "tiny-reason", max_input_tokens: 1}], '
            "fallback: [], provider: default"
        )

    provider_extra = provider_body or ""
    if provider_extra and not provider_extra.endswith("\n"):
        provider_extra += "\n"
    model_rules_extra = model_rules or ""
    if model_rules_extra and not model_rules_extra.endswith("\n"):
        model_rules_extra += "\n"
    return f"""
host: "{host}"
port: {port}
default_provider: dummy
default_profile: {default_profile}
providers:
  dummy:
    name: dummy
    base_url: "http://localhost:9999"
    api_key: "fake"
{provider_extra}profiles:
  auto:
    tiers:
      SIMPLE: {{{auto_simple}}}
      MEDIUM: {{{auto_medium}}}
      COMPLEX: {{{auto_complex}}}
      REASONING: {{{auto_reason}}}
{alt_profile}
{model_rules_extra}{smart_proxy}
"""


@pytest.fixture(autouse=True)
def isolate_data_dir(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPTIPROXAI_DATA_DIR", str(data_dir))


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(_config_text())
    return path


@pytest.fixture
def configured_proxy(config_path: Path):
    configure(str(config_path))
    return config_path


@pytest.fixture
def admin_token(monkeypatch):
    monkeypatch.setenv("OPTIPROXAI_ADMIN_TOKEN", "secret-admin-token")
    return "secret-admin-token"


def _admin_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestReasoningContentCompatibility:
    def test_config_metadata_explicitly_controls_reasoning_content_support(self):
        cfg = OptiproxaiConfig(
            providers={
                "plain": ProviderConfig(
                    name="plain",
                    base_url="http://plain.example",
                ),
                "supported": ProviderConfig(
                    name="supported",
                    base_url="http://supported.example",
                    supports_reasoning_content=True,
                ),
            },
            model_rules=[
                ModelRuleEntry(
                    prefix="model-supported",
                    provider="plain",
                    supports_reasoning_content=True,
                ),
                ModelRuleEntry(
                    prefix="model-disabled",
                    provider="supported",
                    supports_reasoning_content=False,
                ),
            ],
        )
        state = RuntimeState(
            config_path=None,
            config=cfg,
            router=Router(cfg),
            fallback_backoff_state=Router(cfg).fallback_backoff_state,
            config_loaded_at="test",
            version=1,
        )

        assert proxy_mod._supports_reasoning_content("any", "plain", state) is False
        assert proxy_mod._supports_reasoning_content("any", "supported", state) is True
        assert (
            proxy_mod._supports_reasoning_content("model-supported-v1", "plain", state)
            is True
        )
        assert (
            proxy_mod._supports_reasoning_content(
                "model-disabled-v1", "supported", state
            )
            is False
        )

    def test_provider_specific_wildcard_beats_specific_prefix(self):
        cfg = OptiproxaiConfig(
            providers={
                "dummy": ProviderConfig(
                    name="dummy",
                    base_url="http://dummy.example",
                ),
                "other": ProviderConfig(
                    name="other",
                    base_url="http://other.example",
                ),
            },
            model_rules=[
                ModelRuleEntry(
                    prefix="sonnet-",
                    supports_reasoning_content=True,
                ),
                ModelRuleEntry(
                    prefix="*",
                    provider="dummy",
                    supports_reasoning_content=False,
                ),
            ],
        )
        state = RuntimeState(
            config_path=None,
            config=cfg,
            router=Router(cfg),
            fallback_backoff_state=Router(cfg).fallback_backoff_state,
            config_loaded_at="test",
            version=1,
        )

        assert (
            proxy_mod._get_model_reasoning_content_support(
                "sonnet-4-20250514", "dummy", state
            )
            is False
        )
        assert (
            proxy_mod._get_model_reasoning_content_support(
                "sonnet-4-20250514", "other", state
            )
            is True
        )

    def test_unknown_provider_logs_warning(self, caplog: pytest.LogCaptureFixture):
        cfg = OptiproxaiConfig(
            providers={},
            model_rules=[],
        )
        state = RuntimeState(
            config_path=None,
            config=cfg,
            router=Router(cfg),
            fallback_backoff_state=Router(cfg).fallback_backoff_state,
            config_loaded_at="test",
            version=1,
        )

        with caplog.at_level("WARNING", logger="optiproxai.proxy"):
            supported = proxy_mod._supports_reasoning_content(
                "unknown-model", "missing-provider", state
            )

        assert supported is False
        warning_records = [
            record
            for record in caplog.records
            if record.name == "optiproxai.proxy" and record.levelname == "WARNING"
        ]
        assert len(warning_records) == 1
        assert warning_records[0].name == "optiproxai.proxy"
        assert warning_records[0].getMessage() == (
            "Unknown provider for reasoning_content support fallback "
            "model=unknown-model provider=missing-provider"
        )
        assert "Unknown provider for reasoning_content support fallback" in caplog.text
        assert "missing-provider" in caplog.text
        assert "unknown-model" in caplog.text

    def test_sanitizer_returns_original_body_when_no_reasoning_content(self):
        cfg = OptiproxaiConfig(
            providers={
                "dummy": ProviderConfig(
                    name="dummy",
                    base_url="http://dummy.example",
                ),
            }
        )
        state = RuntimeState(
            config_path=None,
            config=cfg,
            router=Router(cfg),
            fallback_backoff_state=Router(cfg).fallback_backoff_state,
            config_loaded_at="test",
            version=1,
        )
        body: dict[str, Any] = {
            "model": "plain-model",
            "messages": [
                {"role": "assistant", "content": {"parts": ["answer"]}},
                {"role": "user", "content": {"parts": ["hello"]}},
            ],
        }

        with pytest.MonkeyPatch.context() as mp:
            deepcopy = MagicMock()
            mp.setattr(proxy_mod.copy, "deepcopy", deepcopy)
            sanitized = proxy_mod._sanitize_reasoning_content_for_candidate(
                body, "plain-model", "dummy", state
            )

        assert sanitized is body
        assert deepcopy.call_count == 0

    def test_sanitizer_shallow_copies_only_messages_with_reasoning_content(self):
        cfg = OptiproxaiConfig(
            providers={
                "dummy": ProviderConfig(
                    name="dummy",
                    base_url="http://dummy.example",
                ),
            }
        )
        state = RuntimeState(
            config_path=None,
            config=cfg,
            router=Router(cfg),
            fallback_backoff_state=Router(cfg).fallback_backoff_state,
            config_loaded_at="test",
            version=1,
        )
        body: dict[str, Any] = {
            "model": "plain-model",
            "messages": [
                {
                    "role": "assistant",
                    "content": {"parts": ["answer"]},
                    "reasoning_content": "private chain",
                },
                {"role": "user", "content": {"parts": ["hello"]}},
            ],
        }

        with pytest.MonkeyPatch.context() as mp:
            deepcopy = MagicMock()
            mp.setattr(proxy_mod.copy, "deepcopy", deepcopy)
            sanitized = proxy_mod._sanitize_reasoning_content_for_candidate(
                body, "plain-model", "dummy", state
            )

        assert sanitized is not body
        assert sanitized["messages"] is not body["messages"]
        assert sanitized["messages"][0] is not body["messages"][0]
        assert sanitized["messages"][1] is body["messages"][1]
        assert "reasoning_content" not in sanitized["messages"][0]
        assert body["messages"][0]["reasoning_content"] == "private chain"
        assert deepcopy.call_count == 0

    def test_model_rule_content_part_policy_normalizes_by_candidate_metadata(self):
        cfg = OptiproxaiConfig(
            providers={
                "dummy": ProviderConfig(
                    name="dummy",
                    base_url="http://dummy.example",
                ),
            },
            model_rules=[
                ModelRuleEntry(
                    prefix="cx/gpt-5.5",
                    provider="dummy",
                    content_part_policy=ContentPartPolicy(
                        mode="normalize",
                        allowed_types=["text", "image_url"],
                        text_types=["input_text", "output_text", "reasoning"],
                        image_types=["input_image"],
                        unknown="text",
                    ),
                )
            ],
        )
        state = RuntimeState(
            config_path=None,
            config=cfg,
            router=Router(cfg),
            fallback_backoff_state=Router(cfg).fallback_backoff_state,
            config_loaded_at="test",
            version=1,
        )
        body: dict[str, Any] = {
            "model": "cx/gpt-5.5",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "answer"},
                        {"type": "reasoning", "summary": [{"text": "thought"}]},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "hello"},
                        {"type": "input_image", "image_url": "file-123"},
                    ],
                },
            ],
        }

        normalized = proxy_mod._normalize_message_content_for_candidate(
            body, "cx/gpt-5.5", "dummy", state
        )

        assert normalized is not body
        assert normalized["messages"][0]["content"] == [
            {"type": "text", "text": "answer"},
            {
                "type": "text",
                "text": '{"type":"reasoning","summary":[{"text":"thought"}]}',
            },
        ]
        assert normalized["messages"][1]["content"] == [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "file-123", "detail": "auto"}},
        ]
        assert body["messages"][0]["content"][0]["type"] == "output_text"

    def test_model_rule_content_part_policy_uses_provider_specific_precedence(self):
        cfg = OptiproxaiConfig(
            providers={
                "dummy": ProviderConfig(
                    name="dummy",
                    base_url="http://dummy.example",
                ),
                "other": ProviderConfig(
                    name="other",
                    base_url="http://other.example",
                ),
            },
            model_rules=[
                ModelRuleEntry(
                    prefix="cx/gpt-5.5",
                    content_part_policy=ContentPartPolicy(
                        mode="normalize", unknown="text"
                    ),
                ),
                ModelRuleEntry(
                    prefix="*",
                    provider="dummy",
                    content_part_policy=ContentPartPolicy(mode="preserve"),
                ),
            ],
        )
        state = RuntimeState(
            config_path=None,
            config=cfg,
            router=Router(cfg),
            fallback_backoff_state=Router(cfg).fallback_backoff_state,
            config_loaded_at="test",
            version=1,
        )

        assert proxy_mod._get_model_content_part_policy(
            "cx/gpt-5.5", "dummy", state
        ) == ContentPartPolicy(mode="preserve")
        assert proxy_mod._get_model_content_part_policy(
            "cx/gpt-5.5", "other", state
        ) == ContentPartPolicy(mode="normalize", unknown="text")

    def test_config_text_accepts_optional_fragments_without_trailing_newline(
        self, tmp_path: Path
    ):
        path = tmp_path / "config.yaml"
        provider_body = '''  fallback:
    name: fallback
    base_url: "http://fallback.example"
    api_key: "fake-fallback"'''
        model_rules = """model_rules:
  - prefix: "auto-simple"
    provider: "dummy"
    supports_reasoning_content: true"""
        config_text = _config_text(
            provider_body=provider_body,
            model_rules=model_rules,
        )
        path.write_text(config_text)

        configure(str(path))
        state = proxy_mod._require_runtime_state()

        assert re.search(r'api_key: "fake-fallback"\nprofiles:', config_text)
        assert re.search(r"supports_reasoning_content: true\nsmart_proxy:", config_text)
        assert "fallback" in state.config.providers
        assert state.config.model_rules[0].supports_reasoning_content is True

    def test_routed_primary_strips_unsupported_reasoning_content(self, tmp_path: Path):
        path = tmp_path / "config.yaml"
        path.write_text(_config_text())
        configure(str(path))
        captured: list[dict[str, Any]] = []

        async def fake_proxy_upstream(*args, **kwargs):
            captured.append(args[2])
            return JSONResponse(content={"ok": True})

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(proxy_mod, "_proxy_upstream", fake_proxy_upstream)
            mp.setattr("optiproxai.scorer.Scorer.classify", _simple_classification)
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "optiproxai/auto",
                        "messages": [
                            {
                                "role": "assistant",
                                "content": "answer",
                                "reasoning_content": "keep me",
                            },
                            {"role": "user", "content": "hello"},
                        ],
                    },
                )

        assert resp.status_code == 200
        assert len(captured) == 1
        assert captured[0]["model"] == "auto-simple"
        assert "reasoning_content" not in captured[0]["messages"][0]

    @pytest.mark.parametrize(
        ("provider_body", "fallback_preserves"),
        [
            (
                """  fallback:
    name: fallback
    base_url: "http://fallback.example"
    api_key: "fake-fallback"
""",
                False,
            ),
            (
                """  fallback:
    name: fallback
    base_url: "http://fallback.example"
    api_key: "fake-fallback"
    supports_reasoning_content: true
""",
                True,
            ),
        ],
    )
    def test_fallback_sanitizes_with_fallback_candidate_compatibility(
        self, tmp_path: Path, provider_body: str, fallback_preserves: bool
    ):
        path = tmp_path / "config.yaml"
        path.write_text(
            _config_text(
                fallback_models='[{model: "fallback-model", provider: "fallback"}]',
                provider_body=provider_body,
            )
        )
        configure(str(path))
        captured: list[dict[str, Any]] = []

        async def fake_proxy_upstream(*args, **kwargs):
            captured.append(args[2])
            if len(captured) == 1:
                return JSONResponse(status_code=500, content={"error": "boom"})
            return JSONResponse(content={"ok": True})

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(proxy_mod, "_proxy_upstream", fake_proxy_upstream)
            mp.setattr("optiproxai.scorer.Scorer.classify", _simple_classification)
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "optiproxai/auto",
                        "messages": [
                            {
                                "role": "assistant",
                                "content": "answer",
                                "reasoning_content": "private chain",
                            },
                            {"role": "user", "content": "hello"},
                        ],
                    },
                )

        assert resp.status_code == 200
        assert len(captured) == 2
        assert captured[0]["model"] == "auto-simple"
        assert captured[1]["model"] == "fallback-model"
        assert "reasoning_content" not in captured[0]["messages"][0]
        if fallback_preserves:
            assert captured[1]["messages"][0]["reasoning_content"] == "private chain"
        else:
            assert "reasoning_content" not in captured[1]["messages"][0]

    def test_supported_model_preserves_reasoning_content(self, tmp_path: Path):
        path = tmp_path / "config.yaml"
        model_rules = """model_rules:
  - prefix: "auto-simple"
    provider: "dummy"
    supports_reasoning_content: true
"""
        path.write_text(_config_text(model_rules=model_rules))
        configure(str(path))
        captured: list[dict[str, Any]] = []

        async def fake_proxy_upstream(*args, **kwargs):
            captured.append(args[2])
            return JSONResponse(content={"ok": True})

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(proxy_mod, "_proxy_upstream", fake_proxy_upstream)
            mp.setattr("optiproxai.scorer.Scorer.classify", _simple_classification)
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "optiproxai/auto",
                        "messages": [
                            {
                                "role": "assistant",
                                "content": "answer",
                                "reasoning_content": "keep me",
                            },
                            {"role": "user", "content": "hello"},
                        ],
                    },
                )

        assert resp.status_code == 200
        assert len(captured) == 1
        assert captured[0]["messages"][0]["reasoning_content"] == "keep me"

    def test_passthrough_keeps_reasoning_content_unchanged(self, configured_proxy):
        captured: list[dict[str, Any]] = []

        async def fake_proxy_upstream(*args, **kwargs):
            captured.append(args[2])
            return JSONResponse(content={"ok": True})

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(proxy_mod, "_proxy_upstream", fake_proxy_upstream)
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "direct-model",
                        "messages": [
                            {
                                "role": "assistant",
                                "content": "answer",
                                "reasoning_content": "pass through",
                            },
                        ],
                    },
                )

        assert resp.status_code == 200
        assert len(captured) == 1
        assert captured[0]["model"] == "direct-model"
        assert captured[0]["messages"][0]["reasoning_content"] == "pass through"

    def test_reasoning_content_does_not_force_routing_tier(self, configured_proxy):
        state = proxy_mod._require_runtime_state()
        without_metadata = state.router.route(
            [{"role": "user", "content": "hello"}], profile="auto"
        )
        with_metadata = state.router.route(
            [
                {
                    "role": "assistant",
                    "content": "previous answer",
                    "reasoning_content": "hidden reasoning metadata",
                },
                {"role": "user", "content": "hello"},
            ],
            profile="auto",
        )

        assert with_metadata.tier == without_metadata.tier


class TestUsageLogging:
    def test_log_usage_records_empty_session_key(self, monkeypatch: pytest.MonkeyPatch):
        cache = LastContextCache()
        monkeypatch.setattr(proxy_mod, "last_context_cache", cache)

        proxy_mod._log_usage(
            model="test-model",
            provider="dummy",
            usage={"prompt_tokens": 123, "completion_tokens": 4, "total_tokens": 127},
            decision=RoutingDecision(
                model="test-model",
                provider="dummy",
                base_url="http://dummy.example",
                tier="MEDIUM",
                score=0.5,
                confidence=1.0,
                session_key="",
            ),
        )

        assert cache.get("") == 123


class TestModelExtraBody:
    """Per-model extra_body injection via model_rules."""

    def _state(self, model_rules: list[ModelRuleEntry]) -> RuntimeState:
        cfg = OptiproxaiConfig(
            providers={
                "doubleword": ProviderConfig(
                    name="doubleword",
                    base_url="https://api.doubleword.ai/v1",
                ),
            },
            model_rules=model_rules,
        )
        return RuntimeState(
            config_path=None,
            config=cfg,
            router=Router(cfg),
            fallback_backoff_state=Router(cfg).fallback_backoff_state,
            config_loaded_at="test",
            version=1,
        )

    def test_extra_body_merged_into_prepared_body(self):
        state = self._state(
            [
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    provider="doubleword",
                    extra_body={"service_tier": "flex"},
                ),
            ]
        )
        body: dict[str, Any] = {
            "model": "moonshotai/kimi-k3",
            "messages": [{"role": "user", "content": "hello"}],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "moonshotai/kimi-k3", "doubleword", state
        )

        assert prepared["service_tier"] == "flex"
        assert prepared["model"] == "moonshotai/kimi-k3"

    def test_extra_body_wins_over_client_field(self):
        state = self._state(
            [
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    provider="doubleword",
                    extra_body={"service_tier": "flex"},
                ),
            ]
        )
        body: dict[str, Any] = {
            "model": "moonshotai/kimi-k3",
            "service_tier": "realtime",
            "messages": [{"role": "user", "content": "hello"}],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "moonshotai/kimi-k3", "doubleword", state
        )

        assert prepared["service_tier"] == "flex"

    def test_no_extra_body_when_rule_has_none(self):
        state = self._state(
            [
                ModelRuleEntry(
                    prefix="zai-org/GLM-5.2-FP8",
                    provider="doubleword",
                ),
            ]
        )
        body: dict[str, Any] = {
            "model": "zai-org/GLM-5.2-FP8",
            "messages": [{"role": "user", "content": "hello"}],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "zai-org/GLM-5.2-FP8", "doubleword", state
        )

        assert "service_tier" not in prepared

    def test_extra_body_not_applied_to_other_provider(self):
        state = self._state(
            [
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    provider="doubleword",
                    extra_body={"service_tier": "flex"},
                ),
            ]
        )
        body: dict[str, Any] = {
            "model": "moonshotai/kimi-k3",
            "messages": [{"role": "user", "content": "hello"}],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "moonshotai/kimi-k3", "sference", state
        )

        assert "service_tier" not in prepared

    def test_provider_specific_rule_outranks_provider_agnostic(self):
        state = self._state(
            [
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    extra_body={"service_tier": "realtime"},
                ),
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    provider="doubleword",
                    extra_body={"service_tier": "flex"},
                ),
            ]
        )
        body: dict[str, Any] = {
            "model": "moonshotai/kimi-k3",
            "messages": [{"role": "user", "content": "hello"}],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "moonshotai/kimi-k3", "doubleword", state
        )

        assert prepared["service_tier"] == "flex"


class TestCacheControlInjection:
    """cache_control marker injection into stable prefix (TASK-14)."""

    def _state(
        self,
        *,
        provider_cache_control: CacheControlConfig | None = None,
        model_rules: list[ModelRuleEntry] | None = None,
    ) -> RuntimeState:
        cfg = OptiproxaiConfig(
            providers={
                "doubleword": ProviderConfig(
                    name="doubleword",
                    base_url="https://api.doubleword.ai/v1",
                    cache_control=provider_cache_control,
                ),
            },
            model_rules=model_rules or [],
        )
        return RuntimeState(
            config_path=None,
            config=cfg,
            router=Router(cfg),
            fallback_backoff_state=Router(cfg).fallback_backoff_state,
            config_loaded_at="test",
            version=1,
        )

    def _marker(self, ttl: str = "5m") -> dict[str, str]:
        return {"type": "ephemeral", "ttl": ttl}

    def test_string_system_content_converted_to_array(self):
        state = self._state(provider_cache_control=CacheControlConfig(enabled=True))
        body: dict[str, Any] = {
            "model": "moonshotai/kimi-k3",
            "messages": [
                {"role": "system", "content": "you are helpful"},
                {"role": "user", "content": "hello"},
            ],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "moonshotai/kimi-k3", "doubleword", state
        )

        assert prepared["messages"][0]["content"] == [
            {
                "type": "text",
                "text": "you are helpful",
                "cache_control": self._marker(),
            }
        ]

    def test_array_system_content_marker_on_last_block(self):
        state = self._state(provider_cache_control=CacheControlConfig(enabled=True))
        body: dict[str, Any] = {
            "model": "moonshotai/kimi-k3",
            "messages": [
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": "part one"},
                        {"type": "text", "text": "part two"},
                    ],
                },
                {"role": "user", "content": "hello"},
            ],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "moonshotai/kimi-k3", "doubleword", state
        )

        content = prepared["messages"][0]["content"]
        assert content[0] == {"type": "text", "text": "part one"}
        assert content[1] == {
            "type": "text",
            "text": "part two",
            "cache_control": self._marker(),
        }

    def test_tools_target_marker_on_last_tool_object(self):
        state = self._state(
            provider_cache_control=CacheControlConfig(enabled=True, target="tools")
        )
        body: dict[str, Any] = {
            "model": "moonshotai/kimi-k3",
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [
                {"type": "function", "function": {"name": "first"}},
                {"type": "function", "function": {"name": "second"}},
            ],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "moonshotai/kimi-k3", "doubleword", state
        )

        assert prepared["tools"][0] == {
            "type": "function",
            "function": {"name": "first"},
        }
        assert prepared["tools"][1]["cache_control"] == self._marker()

    def test_no_system_message_skips_injection(self):
        state = self._state(provider_cache_control=CacheControlConfig(enabled=True))
        body: dict[str, Any] = {
            "model": "moonshotai/kimi-k3",
            "messages": [{"role": "user", "content": "hello"}],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "moonshotai/kimi-k3", "doubleword", state
        )

        assert prepared == body
        assert "cache_control" not in str(prepared)

    def test_no_tools_array_skips_injection_for_tools_target(self):
        state = self._state(
            provider_cache_control=CacheControlConfig(enabled=True, target="tools")
        )
        body: dict[str, Any] = {
            "model": "moonshotai/kimi-k3",
            "messages": [{"role": "user", "content": "hello"}],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "moonshotai/kimi-k3", "doubleword", state
        )

        assert prepared == body

    def test_client_provided_markers_preserved_no_double_injection(self):
        state = self._state(provider_cache_control=CacheControlConfig(enabled=True))
        client_marker = {"type": "ephemeral", "ttl": "1h"}
        body: dict[str, Any] = {
            "model": "moonshotai/kimi-k3",
            "messages": [
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": "help", "cache_control": client_marker}
                    ],
                },
                {"role": "user", "content": "hello"},
            ],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "moonshotai/kimi-k3", "doubleword", state
        )

        assert prepared == body
        system_content = prepared["messages"][0]["content"]
        assert len(system_content) == 1
        assert system_content[0]["cache_control"] == client_marker

    def test_client_marker_elsewhere_skips_injection(self):
        state = self._state(provider_cache_control=CacheControlConfig(enabled=True))
        client_marker = {"type": "ephemeral", "ttl": "1h"}
        body: dict[str, Any] = {
            "model": "moonshotai/kimi-k3",
            "messages": [
                {"role": "system", "content": "you are helpful"},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "context",
                            "cache_control": client_marker,
                        }
                    ],
                },
            ],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "moonshotai/kimi-k3", "doubleword", state
        )

        assert prepared == body

    def test_breakpoint_limit_respected(self):
        state = self._state(
            provider_cache_control=CacheControlConfig(enabled=True, max_breakpoints=4)
        )
        marker = self._marker()
        body: dict[str, Any] = {
            "model": "moonshotai/kimi-k3",
            "messages": [
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": "a", "cache_control": marker},
                        {"type": "text", "text": "b", "cache_control": marker},
                        {"type": "text", "text": "c", "cache_control": marker},
                        {"type": "text", "text": "d", "cache_control": marker},
                    ],
                },
                {"role": "user", "content": "hello"},
            ],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "moonshotai/kimi-k3", "doubleword", state
        )

        assert prepared == body

    def test_injection_survives_content_part_policy_normalization(self):
        state = self._state(
            provider_cache_control=CacheControlConfig(enabled=True),
            model_rules=[
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    provider="doubleword",
                    content_part_policy=ContentPartPolicy(
                        mode="normalize",
                        text_types=["text"],
                        unknown="drop",
                    ),
                ),
            ],
        )
        body: dict[str, Any] = {
            "model": "moonshotai/kimi-k3",
            "messages": [
                {"role": "system", "content": "you are helpful"},
                {"role": "user", "content": "hello"},
            ],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "moonshotai/kimi-k3", "doubleword", state
        )

        assert prepared["messages"][0]["content"] == [
            {
                "type": "text",
                "text": "you are helpful",
                "cache_control": self._marker(),
            }
        ]

    def test_disabled_config_no_injection(self):
        state = self._state(provider_cache_control=CacheControlConfig(enabled=False))
        body: dict[str, Any] = {
            "model": "moonshotai/kimi-k3",
            "messages": [{"role": "system", "content": "you are helpful"}],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "moonshotai/kimi-k3", "doubleword", state
        )

        assert prepared == body

    def test_rule_cache_control_outranks_provider(self):
        state = self._state(
            provider_cache_control=CacheControlConfig(enabled=True, ttl="1h"),
            model_rules=[
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    provider="doubleword",
                    cache_control=CacheControlConfig(
                        enabled=True, ttl="5m", target="tools"
                    ),
                ),
            ],
        )
        body: dict[str, Any] = {
            "model": "moonshotai/kimi-k3",
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [
                {"type": "function", "function": {"name": "alpha"}},
            ],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "moonshotai/kimi-k3", "doubleword", state
        )

        assert prepared["tools"][0]["cache_control"] == self._marker("5m")
        assert prepared["tools"][0]["cache_control"]["ttl"] == "5m"

    def test_rule_disabled_explicitly_opts_out_of_provider(self):
        state = self._state(
            provider_cache_control=CacheControlConfig(enabled=True),
            model_rules=[
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    provider="doubleword",
                    cache_control=CacheControlConfig(enabled=False),
                ),
            ],
        )
        body: dict[str, Any] = {
            "model": "moonshotai/kimi-k3",
            "messages": [{"role": "system", "content": "you are helpful"}],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "moonshotai/kimi-k3", "doubleword", state
        )

        assert prepared == body

    def test_original_body_not_mutated_in_place(self):
        state = self._state(provider_cache_control=CacheControlConfig(enabled=True))
        body: dict[str, Any] = {
            "model": "moonshotai/kimi-k3",
            "messages": [{"role": "system", "content": "you are helpful"}],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "moonshotai/kimi-k3", "doubleword", state
        )

        assert prepared is not body
        assert body["messages"][0]["content"] == "you are helpful"
        assert "cache_control" not in str(body)

    # --- TASK-21: conversation (last_message) target and multi-target breakpoints ---

    def test_last_message_string_content_converted_to_array(self):
        state = self._state(
            provider_cache_control=CacheControlConfig(
                enabled=True, target="last_message"
            )
        )
        body: dict[str, Any] = {
            "model": "deepseek-ai/DeepSeek-V4-Pro",
            "messages": [
                {"role": "system", "content": "you are helpful"},
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
                {"role": "user", "content": "continue"},
            ],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "deepseek-ai/DeepSeek-V4-Pro", "doubleword", state
        )

        last = prepared["messages"][-1]
        assert last["role"] == "user"
        assert last["content"] == [
            {"type": "text", "text": "continue", "cache_control": self._marker()}
        ]
        # System message untouched by the last_message target.
        assert prepared["messages"][0]["content"] == "you are helpful"

    def test_last_message_array_content_marker_on_last_block(self):
        state = self._state(
            provider_cache_control=CacheControlConfig(
                enabled=True, target="last_message"
            )
        )
        body: dict[str, Any] = {
            "model": "deepseek-ai/DeepSeek-V4-Pro",
            "messages": [
                {"role": "user", "content": "hello"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "part one"},
                        {"type": "text", "text": "part two"},
                    ],
                },
            ],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "deepseek-ai/DeepSeek-V4-Pro", "doubleword", state
        )

        content = prepared["messages"][-1]["content"]
        assert content[0] == {"type": "text", "text": "part one"}
        assert content[1] == {
            "type": "text",
            "text": "part two",
            "cache_control": self._marker(),
        }

    def test_multi_target_tools_system_last_message_three_markers(self):
        state = self._state(
            provider_cache_control=CacheControlConfig(enabled=True),
            model_rules=[
                ModelRuleEntry(
                    prefix="deepseek-ai/DeepSeek-V4-Pro",
                    provider="doubleword",
                    cache_control=CacheControlConfig(
                        enabled=True,
                        ttl="1h",
                        targets=["tools", "system", "last_message"],
                    ),
                ),
            ],
        )
        body: dict[str, Any] = {
            "model": "deepseek-ai/DeepSeek-V4-Pro",
            "messages": [
                {"role": "system", "content": "you are helpful"},
                {"role": "user", "content": "hello"},
            ],
            "tools": [
                {"type": "function", "function": {"name": "alpha"}},
                {"type": "function", "function": {"name": "beta"}},
            ],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "deepseek-ai/DeepSeek-V4-Pro", "doubleword", state
        )

        # tools target: marker on last tool object
        assert prepared["tools"][-1]["cache_control"] == self._marker("1h")
        # system target: marker on last block of first system message
        assert prepared["messages"][0]["content"][-1]["cache_control"] == self._marker(
            "1h"
        )
        # last_message target: marker on last content block of final message
        assert prepared["messages"][-1]["content"][-1]["cache_control"] == self._marker(
            "1h"
        )
        assert proxy_mod._count_cache_control_markers(prepared) == 3

    def test_multi_target_max_breakpoints_truncation(self):
        state = self._state(
            provider_cache_control=CacheControlConfig(enabled=True),
            model_rules=[
                ModelRuleEntry(
                    prefix="deepseek-ai/DeepSeek-V4-Pro",
                    provider="doubleword",
                    cache_control=CacheControlConfig(
                        enabled=True,
                        targets=["tools", "system", "last_message"],
                        max_breakpoints=2,
                    ),
                ),
            ],
        )
        body: dict[str, Any] = {
            "model": "deepseek-ai/DeepSeek-V4-Pro",
            "messages": [
                {"role": "system", "content": "you are helpful"},
                {"role": "user", "content": "hello"},
            ],
            "tools": [
                {"type": "function", "function": {"name": "alpha"}},
            ],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "deepseek-ai/DeepSeek-V4-Pro", "doubleword", state
        )

        # Canonical order tools -> system -> last_message; budget 2 stops after system.
        assert prepared["tools"][-1].get("cache_control") == self._marker()
        assert prepared["messages"][0]["content"][-1].get("cache_control") == (
            self._marker()
        )
        # Budget exhausted: final message untouched (string content stays string).
        assert prepared["messages"][-1]["content"] == "hello"
        assert proxy_mod._count_cache_control_markers(prepared) == 2

    def test_client_marker_suppresses_multi_target_injection(self):
        state = self._state(
            provider_cache_control=CacheControlConfig(enabled=True),
            model_rules=[
                ModelRuleEntry(
                    prefix="deepseek-ai/DeepSeek-V4-Pro",
                    provider="doubleword",
                    cache_control=CacheControlConfig(
                        enabled=True, targets=["tools", "system", "last_message"]
                    ),
                ),
            ],
        )
        marker = self._marker()
        body: dict[str, Any] = {
            "model": "deepseek-ai/DeepSeek-V4-Pro",
            "messages": [
                {"role": "system", "content": "you are helpful"},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "client marker",
                            "cache_control": marker,
                        }
                    ],
                },
            ],
            "tools": [{"type": "function", "function": {"name": "alpha"}}],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "deepseek-ai/DeepSeek-V4-Pro", "doubleword", state
        )

        assert prepared == body

    def test_last_message_skips_when_no_messages(self):
        state = self._state(
            provider_cache_control=CacheControlConfig(
                enabled=True, target="last_message"
            )
        )
        body: dict[str, Any] = {
            "model": "deepseek-ai/DeepSeek-V4-Pro",
            "tools": [{"type": "function", "function": {"name": "alpha"}}],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "deepseek-ai/DeepSeek-V4-Pro", "doubleword", state
        )

        assert prepared == body

    def test_rule_targets_outrank_provider_single_target(self):
        state = self._state(
            provider_cache_control=CacheControlConfig(enabled=True, target="system"),
            model_rules=[
                ModelRuleEntry(
                    prefix="deepseek-ai/DeepSeek-V4-Pro",
                    provider="doubleword",
                    cache_control=CacheControlConfig(
                        enabled=True, targets=["tools", "system", "last_message"]
                    ),
                ),
            ],
        )
        body: dict[str, Any] = {
            "model": "deepseek-ai/DeepSeek-V4-Pro",
            "messages": [
                {"role": "system", "content": "you are helpful"},
                {"role": "user", "content": "hello"},
            ],
            "tools": [{"type": "function", "function": {"name": "alpha"}}],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "deepseek-ai/DeepSeek-V4-Pro", "doubleword", state
        )

        assert proxy_mod._count_cache_control_markers(prepared) == 3

    def test_targets_duplicates_deduped(self):
        state = self._state(
            provider_cache_control=CacheControlConfig(enabled=True),
            model_rules=[
                ModelRuleEntry(
                    prefix="deepseek-ai/DeepSeek-V4-Pro",
                    provider="doubleword",
                    cache_control=CacheControlConfig(
                        enabled=True, targets=["system", "system", "last_message"]
                    ),
                ),
            ],
        )
        body: dict[str, Any] = {
            "model": "deepseek-ai/DeepSeek-V4-Pro",
            "messages": [
                {"role": "system", "content": "you are helpful"},
                {"role": "user", "content": "hello"},
            ],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "deepseek-ai/DeepSeek-V4-Pro", "doubleword", state
        )

        # Duplicate "system" collapses to one marker; "last_message" applies too.
        assert proxy_mod._count_cache_control_markers(prepared) == 2

    def test_multi_target_no_mutation_of_original_body(self):
        state = self._state(
            provider_cache_control=CacheControlConfig(enabled=True),
            model_rules=[
                ModelRuleEntry(
                    prefix="deepseek-ai/DeepSeek-V4-Pro",
                    provider="doubleword",
                    cache_control=CacheControlConfig(
                        enabled=True, targets=["tools", "system", "last_message"]
                    ),
                ),
            ],
        )
        body: dict[str, Any] = {
            "model": "deepseek-ai/DeepSeek-V4-Pro",
            "messages": [
                {"role": "system", "content": "you are helpful"},
                {"role": "user", "content": "hello"},
            ],
            "tools": [{"type": "function", "function": {"name": "alpha"}}],
        }

        prepared, _ = proxy_mod._prepare_body_for_candidate(
            body, "deepseek-ai/DeepSeek-V4-Pro", "doubleword", state
        )

        assert prepared is not body
        assert body["messages"][0]["content"] == "you are helpful"
        assert body["messages"][-1]["content"] == "hello"
        assert "cache_control" not in str(body)
        assert body["tools"] == [{"type": "function", "function": {"name": "alpha"}}]


class TestAdminReloadAuth:
    def test_reload_rejected_without_token(self, configured_proxy):
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/admin/reload-config")

        assert resp.status_code == 403
        assert "admin token" in resp.text.lower()

    def test_reload_rejected_with_invalid_token(self, configured_proxy, admin_token):
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/admin/reload-config",
                headers=_admin_headers("wrong-token"),
            )

        assert resp.status_code == 403
        assert "invalid admin bearer token" in resp.text.lower()


class TestProxyRoutingErrors:
    def test_chat_completions_requires_messages(self, configured_proxy) -> None:
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/chat/completions", json={"model": "optiproxai/auto"}
            )

        assert resp.status_code == 400
        payload = resp.json()
        assert payload["error"]["message"] == "messages is required"

    def test_route_debug_requires_messages(self, configured_proxy) -> None:
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/v1/route", json={"profile": "auto"})

        assert resp.status_code == 400
        payload = resp.json()
        assert payload["error"]["message"] == "messages is required"

    def test_chat_completions_rejects_non_object_json(self, configured_proxy) -> None:
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/v1/chat/completions", json=[])

        assert resp.status_code == 400
        payload = resp.json()
        assert payload["error"]["message"] == "JSON body must be an object"

    def test_chat_completions_requires_messages_array(self, configured_proxy) -> None:
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "optiproxai/auto", "messages": "hello"},
            )

        assert resp.status_code == 400
        payload = resp.json()
        assert payload["error"]["message"] == "messages must be an array"

    def test_chat_completions_requires_message_objects(self, configured_proxy) -> None:
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "optiproxai/auto", "messages": ["bad"]},
            )

        assert resp.status_code == 400
        payload = resp.json()
        assert payload["error"]["message"] == "messages must contain only objects"

    def test_route_debug_rejects_non_object_json(self, configured_proxy) -> None:
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/v1/route", json=[])

        assert resp.status_code == 400
        payload = resp.json()
        assert payload["error"]["message"] == "JSON body must be an object"

    def test_route_debug_requires_messages_array(self, configured_proxy) -> None:
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/route", json={"profile": "auto", "messages": "hello"}
            )

        assert resp.status_code == 400
        payload = resp.json()
        assert payload["error"]["message"] == "messages must be an array"

    def test_route_debug_requires_message_objects(self, configured_proxy) -> None:
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/route", json={"profile": "auto", "messages": ["bad"]}
            )

        assert resp.status_code == 400
        payload = resp.json()
        assert payload["error"]["message"] == "messages must contain only objects"

    def test_chat_completions_returns_structured_input_limit_error(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "config.yaml"
        path.write_text(_config_text(input_limit_too_small=True))
        configure(str(path))

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "optiproxai/auto",
                    "messages": [{"role": "user", "content": "hello world"}],
                },
            )

        assert resp.status_code == 400
        payload = resp.json()
        assert payload["error"]["type"] == "input_limit_not_satisfied"
        assert "No input-limit-eligible model candidate" in payload["error"]["message"]

    def test_route_debug_exposes_tools_policy_without_schema_contents(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "config.yaml"
        path.write_text(_config_text(tools_capability_detection="active"))
        configure(str(path))

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/route",
                json={
                    "profile": "auto",
                    "messages": [{"role": "user", "content": "hello world"}],
                    "tools": [
                        {
                            "type": "function",
                            "function": {"name": "sensitive_internal_tool"},
                        }
                    ],
                },
            )

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["tools_capability_detection"] == {
            "policy": "active",
            "declared": True,
            "required": False,
            "trigger": "declaration_ignored",
        }
        assert "sensitive_internal_tool" not in resp.text


class TestDecorativeToolSchemaHandling:
    def _tool_request(self, *, model: str = "optiproxai/auto") -> dict[str, Any]:
        return {
            "model": model,
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "sensitive_internal_tool"},
                }
            ],
            "functions": [{"name": "sensitive_legacy_function"}],
            "tool_choice": "auto",
            "function_call": "auto",
        }

    def test_routed_preserves_tool_fields_by_default(self, tmp_path: Path):
        path = tmp_path / "config.yaml"
        path.write_text(_config_text(tools_capability_detection="active"))
        configure(str(path))
        captured: list[dict[str, Any]] = []

        async def fake_proxy_upstream(*args, **kwargs):
            captured.append(args[2])
            return JSONResponse(content={"ok": True})

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(proxy_mod, "_proxy_upstream", fake_proxy_upstream)
            mp.setattr("optiproxai.scorer.Scorer.classify", _simple_classification)
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post("/v1/chat/completions", json=self._tool_request())

        assert resp.status_code == 200
        assert len(captured) == 1
        assert captured[0]["model"] == "auto-simple"
        for field in ("tools", "functions", "tool_choice", "function_call"):
            assert field in captured[0]

    @pytest.mark.parametrize("stream", [False, True])
    def test_routed_strips_decorative_tool_fields_when_enabled(
        self, tmp_path: Path, stream: bool
    ):
        path = tmp_path / "config.yaml"
        path.write_text(
            _config_text(
                tools_capability_detection="active",
                decorative_tool_schema_handling="strip",
            )
        )
        configure(str(path))
        captured: list[dict[str, Any]] = []
        request_body = self._tool_request()
        request_body["stream"] = stream

        async def fake_proxy_upstream(*args, **kwargs):
            captured.append(args[2])
            return JSONResponse(content={"ok": True})

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(proxy_mod, "_proxy_upstream", fake_proxy_upstream)
            mp.setattr("optiproxai.scorer.Scorer.classify", _simple_classification)
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post("/v1/chat/completions", json=request_body)

        assert resp.status_code == 200
        assert len(captured) == 1
        assert captured[0]["model"] == "auto-simple"
        assert captured[0]["stream"] is stream
        for field in ("tools", "functions", "tool_choice", "function_call"):
            assert field not in captured[0]
        for field in ("tools", "functions", "tool_choice", "function_call"):
            assert field in request_body

    @pytest.mark.parametrize(
        "forced_field",
        [
            {"tool_choice": "required"},
            {"function_call": {"name": "sensitive_legacy_function"}},
        ],
    )
    def test_routed_strip_preserves_forced_tool_use(
        self, tmp_path: Path, forced_field: dict[str, object]
    ):
        path = tmp_path / "config.yaml"
        path.write_text(
            _config_text(
                tools_capability_detection="active",
                decorative_tool_schema_handling="strip",
                model_rules="""
model_rules:
  - prefix: "auto-simple"
    capabilities: [tools]
""",
            )
        )
        configure(str(path))
        captured: list[dict[str, Any]] = []
        request_body = self._tool_request()
        request_body.update(forced_field)

        async def fake_proxy_upstream(*args, **kwargs):
            captured.append(args[2])
            return JSONResponse(content={"ok": True})

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(proxy_mod, "_proxy_upstream", fake_proxy_upstream)
            mp.setattr("optiproxai.scorer.Scorer.classify", _simple_classification)
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post("/v1/chat/completions", json=request_body)

        assert resp.status_code == 200
        assert len(captured) == 1
        for field, value in forced_field.items():
            assert captured[0][field] == value
        assert "tools" in captured[0]
        assert "functions" in captured[0]
        assert "tool_choice" in captured[0]
        assert "function_call" in captured[0]

    def test_routed_strip_preserves_active_tool_history(self, tmp_path: Path):
        path = tmp_path / "config.yaml"
        path.write_text(
            _config_text(
                tools_capability_detection="active",
                decorative_tool_schema_handling="strip",
                model_rules="""
model_rules:
  - prefix: "auto-simple"
    capabilities: [tools]
""",
            )
        )
        configure(str(path))
        captured: list[dict[str, Any]] = []
        request_body = self._tool_request()
        request_body["messages"] = [
            {"role": "user", "content": "use a tool"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "sensitive_internal_tool"},
                    }
                ],
            },
        ]

        async def fake_proxy_upstream(*args, **kwargs):
            captured.append(args[2])
            return JSONResponse(content={"ok": True})

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(proxy_mod, "_proxy_upstream", fake_proxy_upstream)
            mp.setattr("optiproxai.scorer.Scorer.classify", _simple_classification)
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post("/v1/chat/completions", json=request_body)

        assert resp.status_code == 200
        assert len(captured) == 1
        for field in ("tools", "functions", "tool_choice", "function_call"):
            assert field in captured[0]

    def test_passthrough_preserves_tool_fields_even_when_strip_configured(
        self, tmp_path: Path
    ):
        path = tmp_path / "config.yaml"
        path.write_text(
            _config_text(
                tools_capability_detection="active",
                decorative_tool_schema_handling="strip",
            )
        )
        configure(str(path))
        captured: list[dict[str, Any]] = []

        async def fake_proxy_upstream(*args, **kwargs):
            captured.append(args[2])
            return JSONResponse(content={"ok": True})

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(proxy_mod, "_proxy_upstream", fake_proxy_upstream)
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post(
                    "/v1/chat/completions",
                    json=self._tool_request(model="direct-model"),
                )

        assert resp.status_code == 200
        assert len(captured) == 1
        assert captured[0]["model"] == "direct-model"
        for field in ("tools", "functions", "tool_choice", "function_call"):
            assert field in captured[0]

    def test_fallback_attempts_reuse_adapted_payload(self, tmp_path: Path):
        path = tmp_path / "config.yaml"
        path.write_text(
            _config_text(
                tools_capability_detection="active",
                decorative_tool_schema_handling="strip",
                fallback_models='["fallback-simple"]',
            )
        )
        configure(str(path))
        captured: list[dict[str, Any]] = []

        async def fake_proxy_upstream(*args, **kwargs):
            captured.append(args[2])
            if len(captured) == 1:
                return JSONResponse(status_code=502, content={"error": "retry"})
            return JSONResponse(content={"ok": True})

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(proxy_mod, "_proxy_upstream", fake_proxy_upstream)
            mp.setattr("optiproxai.scorer.Scorer.classify", _simple_classification)
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post("/v1/chat/completions", json=self._tool_request())

        assert resp.status_code == 200
        assert [body["model"] for body in captured] == [
            "auto-simple",
            "fallback-simple",
        ]
        for body in captured:
            for field in ("tools", "functions", "tool_choice", "function_call"):
                assert field not in body

    def test_route_debug_exposes_strip_audit_without_schema_contents(
        self, tmp_path: Path
    ):
        path = tmp_path / "config.yaml"
        path.write_text(
            _config_text(
                tools_capability_detection="active",
                decorative_tool_schema_handling="strip",
            )
        )
        configure(str(path))

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/route",
                json={"profile": "auto", **self._tool_request(model="ignored")},
            )

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["decorative_tool_schema_handling"] == {
            "policy": "strip",
            "declared": True,
            "required": False,
            "applied": True,
            "stripped_fields": [
                "tools",
                "functions",
                "tool_choice",
                "function_call",
            ],
        }
        assert "sensitive_internal_tool" not in resp.text
        assert "sensitive_legacy_function" not in resp.text

    def test_chat_logs_strip_audit_without_schema_contents(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        path = tmp_path / "config.yaml"
        path.write_text(
            _config_text(
                tools_capability_detection="active",
                decorative_tool_schema_handling="strip",
            )
        )
        configure(str(path))

        async def fake_proxy_upstream(*args, **kwargs):
            return JSONResponse(content={"ok": True})

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(proxy_mod, "_proxy_upstream", fake_proxy_upstream)
            mp.setattr("optiproxai.scorer.Scorer.classify", _simple_classification)
            with caplog.at_level("INFO", logger="optiproxai.proxy"):
                with TestClient(app, raise_server_exceptions=False) as client:
                    resp = client.post(
                        "/v1/chat/completions", json=self._tool_request()
                    )

        assert resp.status_code == 200
        audit_logs = [
            record.getMessage()
            for record in caplog.records
            if record.name == "optiproxai.proxy"
            and "DECORATIVE_TOOL_SCHEMA" in record.getMessage()
        ]
        assert audit_logs
        assert "policy=strip" in audit_logs[0]
        assert "applied=True" in audit_logs[0]
        assert "sensitive_internal_tool" not in caplog.text
        assert "sensitive_legacy_function" not in caplog.text


class TestAdminReloadBehavior:
    def test_reload_success_updates_state_and_models(
        self,
        configured_proxy,
        admin_token,
        config_path: Path,
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            before_health = client.get("/health").json()
            before_models = client.get("/v1/models").json()
            before_ids = {m["id"] for m in before_models["data"]}
            assert "optiproxai/premium" not in before_ids

            config_path.write_text(_config_text(include_alt_profile=True))
            resp = client.post(
                "/admin/reload-config",
                headers=_admin_headers(admin_token),
            )
            assert resp.status_code == 200
            payload = resp.json()
            assert payload["ok"] is True
            assert payload["reloaded"] is True
            assert payload["version"] > before_health["config_version"]

            after_models = client.get("/v1/models").json()
            after_ids = {m["id"] for m in after_models["data"]}
            assert "optiproxai/premium" in after_ids

    def test_reload_strict_validation_failure_keeps_state(
        self,
        configured_proxy,
        admin_token,
        config_path: Path,
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            before = client.get("/health").json()

            config_path.write_text(
                """
host: "0.0.0.0"
port: 18420
default_provider: dummy
providers:
  dummy:
    name: dummy
    base_url: "http://localhost:9999"
    api_key: "fake"
"""
            )
            resp = client.post(
                "/admin/reload-config",
                headers=_admin_headers(admin_token),
            )
            assert resp.status_code == 400
            assert "Reload validation failed" in resp.text

            after = client.get("/health").json()
            assert after["config_version"] == before["config_version"]
            assert after["config_loaded_at"] == before["config_loaded_at"]

    def test_reload_rejects_non_reloadable_fields(
        self,
        configured_proxy,
        admin_token,
        config_path: Path,
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            before = client.get("/health").json()

            config_path.write_text(_config_text(host="127.0.0.1"))
            resp = client.post(
                "/admin/reload-config",
                headers=_admin_headers(admin_token),
            )
            assert resp.status_code == 409
            payload = resp.json()
            assert payload["ok"] is False
            assert "host" in payload["non_reloadable_changes"]

            after = client.get("/health").json()
            assert after["config_version"] == before["config_version"]


class TestAdminReloadBackoff:
    def test_reload_updates_backoff_config_without_resetting_state(
        self,
        tmp_path: Path,
        admin_token,
    ):
        path = tmp_path / "config.yaml"
        path.write_text(_config_text(fallback_backoff_enabled=True))
        configure(str(path))
        state = proxy_mod._require_runtime_state()
        state.fallback_backoff_state.record_retryable_failure("auto-simple", "dummy")
        before_entry = state.fallback_backoff_state.get_entry("auto-simple", "dummy")
        assert before_entry is not None

        with TestClient(app, raise_server_exceptions=False) as client:
            path.write_text(
                _config_text(fallback_backoff_enabled=True).replace(
                    "initial_delay_seconds: 5", "initial_delay_seconds: 9"
                )
            )
            resp = client.post(
                "/admin/reload-config",
                headers=_admin_headers(admin_token),
            )
            assert resp.status_code == 200

        after_state = proxy_mod._require_runtime_state()
        after_entry = after_state.fallback_backoff_state.get_entry(
            "auto-simple", "dummy"
        )
        assert after_entry == before_entry
        assert (
            after_state.config.smart_proxy.fallback_backoff.initial_delay_seconds == 9
        )


class TestInFlightSnapshotBehavior:
    def test_routed_request_keeps_start_of_request_state(self, configured_proxy):
        old_state = proxy_mod._require_runtime_state()

        old_decision = RoutingDecision(
            model="old-model",
            provider="dummy",
            base_url="http://localhost:9999",
            api_key="fake",
            profile="auto",
            tier="SIMPLE",
            score=0.1,
            confidence=0.9,
        )
        new_decision = RoutingDecision(
            model="new-model",
            provider="dummy",
            base_url="http://localhost:9999",
            api_key="fake",
            profile="auto",
            tier="SIMPLE",
            score=0.8,
            confidence=0.95,
        )

        old_router = MagicMock()
        old_router.route.return_value = old_decision
        new_router = MagicMock()
        new_router.route.return_value = new_decision

        proxy_mod._activate_state(
            RuntimeState(
                config_path=old_state.config_path,
                config=old_state.config,
                router=old_router,
                fallback_backoff_state=old_state.fallback_backoff_state,
                config_loaded_at=old_state.config_loaded_at,
                version=old_state.version,
            )
        )

        fallback_response = JSONResponse(content={"ok": True})
        try:

            async def fake_try_with_fallbacks(
                body,
                decision,
                profile=None,
                *,
                request_id=None,
                state=None,
                fallback_body_base=None,
            ):
                proxy_mod._activate_state(
                    RuntimeState(
                        config_path=old_state.config_path,
                        config=old_state.config,
                        router=new_router,
                        fallback_backoff_state=old_state.fallback_backoff_state,
                        config_loaded_at=old_state.config_loaded_at,
                        version=old_state.version + 1,
                    )
                )
                return fallback_response

            with pytest.MonkeyPatch.context() as mp:
                try_with_fallbacks = AsyncMock(side_effect=fake_try_with_fallbacks)
                mp.setattr(proxy_mod, "_try_with_fallbacks", try_with_fallbacks)

                with TestClient(app, raise_server_exceptions=False) as client:
                    resp = client.post(
                        "/v1/chat/completions",
                        json={
                            "model": "optiproxai/auto",
                            "messages": [{"role": "user", "content": "hello"}],
                        },
                    )

                assert resp.status_code == 200
                assert try_with_fallbacks.await_count == 1
                called_decision = try_with_fallbacks.await_args_list[0].args[1]
                assert called_decision.model == "old-model"

        finally:
            proxy_mod._activate_state(old_state)


class TestPassThroughMissingProvider:
    def test_passthrough_returns_structured_error_when_provider_missing(
        self, tmp_path, monkeypatch
    ) -> None:
        """Missing default_provider returns OpenAI-style JSON error, not raw 500 (AC #6)."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
default_provider: missing-provider
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
"""
        )
        configure(str(config_path))

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "direct-model",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )

        assert resp.status_code == 500
        payload = resp.json()
        assert payload["error"]["message"] == (
            "Default provider 'missing-provider' is not configured"
        )
        assert payload["error"]["type"] == "server_error"


class TestAdminTokenCompareDigest:
    def test_admin_token_uses_compare_digest(self, configured_proxy, admin_token):
        """Admin token comparison must use secrets.compare_digest (AC #7)."""
        with (
            patch(
                "optiproxai.proxy.secrets.compare_digest", wraps=secrets.compare_digest
            ) as mock_compare,
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            resp = client.post(
                "/admin/reload-config",
                headers=_admin_headers(admin_token),
            )

        assert mock_compare.call_count >= 1
        assert resp.status_code in (
            200,
            400,
            500,
        )  # auth passed; reload may fail for other reasons
