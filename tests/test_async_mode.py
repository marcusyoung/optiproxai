"""Tests for async_mode config model, resolution order, and delivery modes."""

from __future__ import annotations

from typing import Any

import optiproxai.proxy as proxy_mod
from optiproxai.config import (
    AsyncModeConfig,
    ModelRuleEntry,
    OptiproxaiConfig,
    ProviderConfig,
)
from optiproxai.proxy import RuntimeState
from optiproxai.router import Router


def _state(
    model_rules: list[ModelRuleEntry] | None = None,
    providers: dict[str, ProviderConfig] | None = None,
    provider_async: AsyncModeConfig | None = None,
) -> RuntimeState:
    provs = providers or {
        "doubleword": ProviderConfig(
            name="doubleword",
            base_url="https://api.doubleword.ai/v1",
        ),
    }
    if provider_async is not None:
        provs = dict(provs)
        provs["doubleword"] = provs["doubleword"].model_copy(
            update={"async_mode": provider_async}
        )
    cfg = OptiproxaiConfig(providers=provs, model_rules=model_rules or [])
    return RuntimeState(
        config_path=None,
        config=cfg,
        router=Router(cfg),
        fallback_backoff_state=Router(cfg).fallback_backoff_state,
        config_loaded_at="test",
        version=1,
    )


def _body() -> dict[str, Any]:
    return {
        "model": "moonshotai/kimi-k3",
        "messages": [{"role": "user", "content": "hello"}],
    }


class TestAsyncModeDelivery:
    """Each delivery mode applies correctly."""

    def test_body_delivery_injects_field_into_body(self):
        state = _state(
            [
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    provider="doubleword",
                    async_mode=AsyncModeConfig(
                        enabled=True,
                        delivery="body",
                        field="service_tier",
                        value="flex",
                    ),
                ),
            ]
        )
        prepared, headers = proxy_mod._prepare_body_for_candidate(
            _body(), "moonshotai/kimi-k3", "doubleword", state
        )
        assert prepared["service_tier"] == "flex"
        assert headers == {}

    def test_header_delivery_injects_header(self):
        state = _state(
            [
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    provider="doubleword",
                    async_mode=AsyncModeConfig(
                        enabled=True,
                        delivery="header",
                        field="X-Async-Mode",
                        value="batch",
                    ),
                ),
            ]
        )
        prepared, headers = proxy_mod._prepare_body_for_candidate(
            _body(), "moonshotai/kimi-k3", "doubleword", state
        )
        assert headers == {"X-Async-Mode": "batch"}
        assert "service_tier" not in prepared

    def test_model_suffix_delivery_appends_suffix(self):
        state = _state(
            [
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    provider="doubleword",
                    async_mode=AsyncModeConfig(
                        enabled=True,
                        delivery="model_suffix",
                        suffix="flex",
                    ),
                ),
            ]
        )
        prepared, headers = proxy_mod._prepare_body_for_candidate(
            _body(), "moonshotai/kimi-k3", "doubleword", state
        )
        assert prepared["model"] == "moonshotai/kimi-k3:flex"
        assert headers == {}


class TestAsyncModeResolution:
    """Resolution order: ModelEntry -> ModelRuleEntry -> ProviderConfig -> None."""

    def test_entry_level_wins_over_rule_level(self):
        state = _state(
            [
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    provider="doubleword",
                    async_mode=AsyncModeConfig(
                        enabled=True,
                        delivery="header",
                        field="X-Rule",
                        value="rule",
                    ),
                ),
            ]
        )
        entry_async = AsyncModeConfig(
            enabled=True,
            delivery="body",
            field="service_tier",
            value="flex",
        )
        prepared, headers = proxy_mod._prepare_body_for_candidate(
            _body(),
            "moonshotai/kimi-k3",
            "doubleword",
            state,
            entry_async_mode=entry_async,
        )
        assert prepared["service_tier"] == "flex"
        assert headers == {}

    def test_rule_level_wins_over_provider_level(self):
        state = _state(
            [
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    provider="doubleword",
                    async_mode=AsyncModeConfig(
                        enabled=True,
                        delivery="header",
                        field="X-Rule",
                        value="rule",
                    ),
                ),
            ],
            provider_async=AsyncModeConfig(
                enabled=True,
                delivery="body",
                field="service_tier",
                value="flex",
            ),
        )
        prepared, headers = proxy_mod._prepare_body_for_candidate(
            _body(), "moonshotai/kimi-k3", "doubleword", state
        )
        assert headers == {"X-Rule": "rule"}
        assert "service_tier" not in prepared

    def test_provider_level_applies_when_no_rule(self):
        state = _state(
            provider_async=AsyncModeConfig(
                enabled=True,
                delivery="body",
                field="service_tier",
                value="flex",
            ),
        )
        prepared, headers = proxy_mod._prepare_body_for_candidate(
            _body(), "moonshotai/kimi-k3", "doubleword", state
        )
        assert prepared["service_tier"] == "flex"
        assert headers == {}

    def test_no_op_when_unconfigured(self):
        state = _state()
        prepared, headers = proxy_mod._prepare_body_for_candidate(
            _body(), "moonshotai/kimi-k3", "doubleword", state
        )
        assert "service_tier" not in prepared
        assert headers == {}
        assert prepared["model"] == "moonshotai/kimi-k3"

    def test_rule_provider_specific_outranks_provider_agnostic(self):
        state = _state(
            [
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    async_mode=AsyncModeConfig(
                        enabled=True,
                        delivery="body",
                        field="service_tier",
                        value="realtime",
                    ),
                ),
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    provider="doubleword",
                    async_mode=AsyncModeConfig(
                        enabled=True,
                        delivery="body",
                        field="service_tier",
                        value="flex",
                    ),
                ),
            ]
        )
        prepared, _ = proxy_mod._prepare_body_for_candidate(
            _body(), "moonshotai/kimi-k3", "doubleword", state
        )
        assert prepared["service_tier"] == "flex"


class TestAsyncModeExplicitDisable:
    """enabled: false at a higher level wins over enabled: true below."""

    def test_model_level_disabled_blocks_provider_enabled(self):
        state = _state(
            provider_async=AsyncModeConfig(
                enabled=True,
                delivery="body",
                field="service_tier",
                value="flex",
            ),
        )
        entry_async = AsyncModeConfig(
            enabled=False,
            delivery="body",
            field="service_tier",
            value="flex",
        )
        prepared, headers = proxy_mod._prepare_body_for_candidate(
            _body(),
            "moonshotai/kimi-k3",
            "doubleword",
            state,
            entry_async_mode=entry_async,
        )
        assert "service_tier" not in prepared
        assert headers == {}

    def test_rule_level_disabled_blocks_provider_enabled(self):
        state = _state(
            [
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    provider="doubleword",
                    async_mode=AsyncModeConfig(
                        enabled=False,
                        delivery="body",
                        field="service_tier",
                        value="flex",
                    ),
                ),
            ],
            provider_async=AsyncModeConfig(
                enabled=True,
                delivery="body",
                field="service_tier",
                value="flex",
            ),
        )
        prepared, headers = proxy_mod._prepare_body_for_candidate(
            _body(), "moonshotai/kimi-k3", "doubleword", state
        )
        assert "service_tier" not in prepared
        assert headers == {}


class TestAsyncModeExtraBodyCoexistence:
    """extra_body remains functional for non-async fields alongside async body injection."""

    def test_extra_body_merges_alongside_async_body_injection(self):
        state = _state(
            [
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    provider="doubleword",
                    extra_body={"temperature": 0},
                ),
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    provider="doubleword",
                    async_mode=AsyncModeConfig(
                        enabled=True,
                        delivery="body",
                        field="service_tier",
                        value="flex",
                    ),
                ),
            ]
        )
        prepared, _ = proxy_mod._prepare_body_for_candidate(
            _body(), "moonshotai/kimi-k3", "doubleword", state
        )
        assert prepared["temperature"] == 0
        assert prepared["service_tier"] == "flex"

    def test_async_body_wins_over_extra_body_on_conflict(self):
        state = _state(
            [
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    provider="doubleword",
                    extra_body={"service_tier": "realtime"},
                ),
                ModelRuleEntry(
                    prefix="moonshotai/kimi-k3",
                    provider="doubleword",
                    async_mode=AsyncModeConfig(
                        enabled=True,
                        delivery="body",
                        field="service_tier",
                        value="flex",
                    ),
                ),
            ]
        )
        prepared, _ = proxy_mod._prepare_body_for_candidate(
            _body(), "moonshotai/kimi-k3", "doubleword", state
        )
        assert prepared["service_tier"] == "flex"


class TestAsyncModePropagation:
    """ModelEntry.async_mode propagates through RoutingDecision and FallbackEntry."""

    def test_route_propagates_primary_async_mode(self):
        cfg = OptiproxaiConfig(
            providers={
                "doubleword": ProviderConfig(
                    name="doubleword",
                    base_url="https://api.doubleword.ai/v1",
                ),
            },
            default_provider="doubleword",
            profiles={
                "auto": {
                    "tiers": {
                        "SIMPLE": {
                            "primary": {
                                "model": "kimi-k3",
                                "async_mode": {
                                    "enabled": True,
                                    "delivery": "body",
                                    "field": "service_tier",
                                    "value": "flex",
                                },
                            },
                            "fallback": [],
                        },
                        "MEDIUM": {"primary": "kimi-k3", "fallback": []},
                        "COMPLEX": {"primary": "kimi-k3", "fallback": []},
                        "REASONING": {"primary": "kimi-k3", "fallback": []},
                    }
                }
            },
        )
        router = Router(cfg)
        decision = router.resolve_model(profile="auto", tier="SIMPLE")
        assert decision.async_mode is not None
        assert decision.async_mode.enabled is True
        assert decision.async_mode.delivery == "body"
        assert decision.async_mode.field == "service_tier"
        assert decision.async_mode.value == "flex"

    def test_route_propagates_fallback_async_mode(self):
        cfg = OptiproxaiConfig(
            providers={
                "doubleword": ProviderConfig(
                    name="doubleword",
                    base_url="https://api.doubleword.ai/v1",
                ),
                "other": ProviderConfig(
                    name="other",
                    base_url="https://api.other.ai/v1",
                ),
            },
            default_provider="doubleword",
            profiles={
                "auto": {
                    "tiers": {
                        "SIMPLE": {
                            "primary": "kimi-k3",
                            "fallback": [
                                {
                                    "model": "glm-5",
                                    "provider": "other",
                                    "async_mode": {
                                        "enabled": True,
                                        "delivery": "model_suffix",
                                        "suffix": "flex",
                                    },
                                },
                            ],
                        },
                        "MEDIUM": {"primary": "kimi-k3", "fallback": []},
                        "COMPLEX": {"primary": "kimi-k3", "fallback": []},
                        "REASONING": {"primary": "kimi-k3", "fallback": []},
                    }
                }
            },
        )
        router = Router(cfg)
        decision = router.resolve_model(profile="auto", tier="SIMPLE")
        assert len(decision.fallbacks) == 1
        fb = decision.fallbacks[0]
        assert fb.async_mode is not None
        assert fb.async_mode.enabled is True
        assert fb.async_mode.delivery == "model_suffix"
        assert fb.async_mode.suffix == "flex"
