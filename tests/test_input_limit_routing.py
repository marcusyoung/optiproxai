"""Tests for input-limit-aware routing."""

from __future__ import annotations

import pytest

from pydantic import ValidationError

from optiproxai.config import (
    OptiproxaiConfig,
    ModelCapabilityEntry,
    ProviderConfig,
    ProfileConfig,
    TierModelConfig,
)
from optiproxai.fallback_backoff import FallbackBackoffState
from optiproxai.last_context_cache import LastContextCache
from optiproxai.router import InputLimitNotSatisfiedError, Router


def _messages(tokenish_length: int = 160) -> list[dict[str, str]]:
    return [{"role": "user", "content": "x " * tokenish_length}]


def _config(
    *,
    tiers: dict[str, TierModelConfig],
    capabilities: list[ModelCapabilityEntry] | None = None,
) -> OptiproxaiConfig:
    return OptiproxaiConfig(
        providers={
            "default": ProviderConfig(
                name="default",
                base_url="https://default.example/v1",
                api_key="default-key",
            ),
            "tier-provider": ProviderConfig(
                name="tier-provider",
                base_url="https://tier.example/v1",
                api_key="tier-key",
            ),
            "entry-provider": ProviderConfig(
                name="entry-provider",
                base_url="https://entry.example/v1",
                api_key="entry-key",
            ),
        },
        default_provider="default",
        profiles={"auto": ProfileConfig(tiers=tiers)},
        default_profile="auto",
        model_capabilities=capabilities or [],
    )


def _all_tiers(
    primary: str | dict[str, object], **overrides: TierModelConfig
) -> dict[str, TierModelConfig]:
    tiers = {
        "SIMPLE": TierModelConfig(primary=primary),
        "MEDIUM": TierModelConfig(primary=primary),
        "COMPLEX": TierModelConfig(primary=primary),
        "REASONING": TierModelConfig(primary=primary),
    }
    tiers.update(overrides)
    return tiers


def _force_tier(router: Router, tier: str) -> None:
    router._classify = lambda *args, **kwargs: {  # type: ignore[method-assign]
        "tier": tier,
        "score": 0.5,
        "confidence": 1.0,
        "signals": [],
        "agentic_score": 0.0,
    }


class TestInputLimitConfig:
    def test_model_entry_accepts_max_input_tokens_and_string_entries_still_validate(
        self,
    ) -> None:
        cfg = _config(
            tiers=_all_tiers(
                "string-model",
                MEDIUM=TierModelConfig(
                    primary=[
                        "string-model",
                        {
                            "model": "object-model",
                            "provider": "entry-provider",
                            "max_input_tokens": 8192,
                        },
                    ],
                    fallback=[{"model": "fallback-model", "max_input_tokens": 16384}],
                ),
            )
        )

        tier = cfg.profiles["auto"].tiers["MEDIUM"]
        primary_entries = tier.resolve_primary_candidate_entries()
        fallback_entries = tier.resolve_fallback_candidate_entries()

        assert primary_entries[0].model == "string-model"
        assert primary_entries[0].max_input_tokens is None
        assert primary_entries[1].model == "object-model"
        assert primary_entries[1].provider == "entry-provider"
        assert primary_entries[1].max_input_tokens == 8192
        assert fallback_entries[0].max_input_tokens == 16384
        assert tier.resolve_primary_candidates() == [
            ("string-model", ""),
            ("object-model", "entry-provider"),
        ]

    def test_model_rules_and_legacy_model_capabilities_are_mutually_exclusive(
        self,
    ) -> None:
        with pytest.raises(ValueError, match="model_rules"):
            OptiproxaiConfig(
                providers={
                    "default": ProviderConfig(
                        name="default",
                        base_url="https://default.example/v1",
                    ),
                },
                default_provider="default",
                profiles={"auto": ProfileConfig(tiers=_all_tiers("model"))},
                default_profile="auto",
                model_rules=[ModelCapabilityEntry(prefix="model")],
                model_capabilities=[
                    ModelCapabilityEntry(prefix="*", capabilities=["tools"])
                ],
            )

    def test_legacy_context_window_tokens_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="context_window_tokens"):
            TierModelConfig(
                primary=[{"model": "legacy-model", "context_window_tokens": 8192}]
            )

    def test_max_input_tokens_must_be_positive(self) -> None:
        with pytest.raises(ValidationError, match="greater than 0"):
            TierModelConfig(primary=[{"model": "invalid-model", "max_input_tokens": 0}])

    def test_provider_precedence_survives_candidate_metadata(self) -> None:
        cfg = _config(
            tiers=_all_tiers(
                "unused",
                MEDIUM=TierModelConfig(
                    primary=[
                        {"model": "tier-default-model", "max_input_tokens": 99999},
                        {
                            "model": "entry-provider-model",
                            "provider": "entry-provider",
                            "max_input_tokens": 99999,
                        },
                    ],
                    provider="tier-provider",
                ),
            )
        )
        router = Router(cfg)
        _force_tier(router, "MEDIUM")

        first = router.route(_messages(), profile="auto")
        second = router.route(_messages(), profile="auto")

        assert first.model == "tier-default-model"
        assert first.provider == "tier-provider"
        assert second.model == "entry-provider-model"
        assert second.provider == "entry-provider"


class TestInputLimitRouting:
    def test_long_request_skips_too_small_primary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "optiproxai.router._estimate_tokens", lambda messages, **kwargs: 1000
        )
        cfg = _config(
            tiers=_all_tiers(
                "unused",
                MEDIUM=TierModelConfig(
                    primary=[
                        {"model": "small", "max_input_tokens": 4},
                        {"model": "large", "max_input_tokens": 99999},
                    ],
                ),
            )
        )
        router = Router(cfg)
        _force_tier(router, "MEDIUM")

        decision = router.route(_messages(), profile="auto")

        assert decision.model == "large"

    def test_unknown_max_input_tokens_remains_eligible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "optiproxai.router._estimate_tokens", lambda messages, **kwargs: 1000
        )
        cfg = _config(
            tiers=_all_tiers(
                "unused",
                MEDIUM=TierModelConfig(
                    primary=[
                        {"model": "small", "max_input_tokens": 4},
                        "unknown-limit",
                    ],
                ),
            )
        )
        router = Router(cfg)
        _force_tier(router, "MEDIUM")

        decision = router.route(_messages(), profile="auto")

        assert decision.model == "unknown-limit"

    def test_prompt_equal_to_max_input_tokens_remains_eligible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "optiproxai.router._estimate_tokens", lambda messages, **kwargs: 100
        )
        cfg = _config(
            tiers=_all_tiers(
                "unused",
                MEDIUM=TierModelConfig(
                    primary=[
                        {"model": "exact-fit", "max_input_tokens": 100},
                        {"model": "large", "max_input_tokens": 99999},
                    ],
                ),
            )
        )
        router = Router(cfg)
        _force_tier(router, "MEDIUM")

        decision = router.route(_messages(), profile="auto")

        assert decision.model == "exact-fit"

    def test_fallback_can_satisfy_long_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "optiproxai.router._estimate_tokens", lambda messages, **kwargs: 1000
        )
        cfg = _config(
            tiers=_all_tiers(
                "unused",
                MEDIUM=TierModelConfig(
                    primary=[{"model": "small-primary", "max_input_tokens": 4}],
                    fallback=[{"model": "large-fallback", "max_input_tokens": 99999}],
                ),
            )
        )
        router = Router(cfg)
        _force_tier(router, "MEDIUM")

        decision = router.route(_messages(), profile="auto")

        assert decision.model == "large-fallback"
        assert decision.fallbacks == []

    def test_higher_tier_does_not_silently_satisfy_long_input_without_capabilities(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "optiproxai.router._estimate_tokens", lambda messages, **kwargs: 1000
        )
        cfg = _config(
            tiers={
                "MEDIUM": TierModelConfig(
                    primary=[{"model": "small-medium", "max_input_tokens": 4}]
                ),
                "COMPLEX": TierModelConfig(
                    primary=[{"model": "large-complex", "max_input_tokens": 99999}]
                ),
            }
        )
        router = Router(cfg)
        _force_tier(router, "MEDIUM")

        with pytest.raises(InputLimitNotSatisfiedError) as exc_info:
            router.route(_messages(), profile="auto")

        assert exc_info.value.tier == "MEDIUM"

    def test_higher_tier_can_satisfy_capability_constrained_long_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "optiproxai.router._estimate_tokens", lambda messages, **kwargs: 1000
        )
        cfg = _config(
            tiers={
                "MEDIUM": TierModelConfig(
                    primary=[{"model": "small-vision", "max_input_tokens": 4}]
                ),
                "COMPLEX": TierModelConfig(
                    primary=[{"model": "large-vision", "max_input_tokens": 99999}]
                ),
            },
            capabilities=[
                ModelCapabilityEntry(prefix="small-vision", capabilities=["vision"]),
                ModelCapabilityEntry(prefix="large-vision", capabilities=["vision"]),
            ],
        )
        router = Router(cfg)
        _force_tier(router, "MEDIUM")

        decision = router.route(
            _messages(), profile="auto", required_capabilities={"vision"}
        )

        assert decision.model == "large-vision"
        assert decision.tier == "MEDIUM"

    def test_capability_filtering_remains_mandatory_with_large_input_limit_candidate(
        self,
    ) -> None:
        cfg = _config(
            tiers=_all_tiers(
                "unused",
                MEDIUM=TierModelConfig(
                    primary=[
                        {"model": "large-text", "max_input_tokens": 99999},
                        {"model": "large-vision", "max_input_tokens": 99999},
                    ],
                ),
            ),
            capabilities=[
                ModelCapabilityEntry(prefix="large-text", capabilities=[]),
                ModelCapabilityEntry(prefix="large-vision", capabilities=["vision"]),
            ],
        )
        router = Router(cfg)

        decision = router.route(
            _messages(), profile="auto", required_capabilities={"vision"}
        )

        assert decision.model == "large-vision"

    def test_cooldown_applies_after_input_limit_filtering(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "optiproxai.router._estimate_tokens", lambda messages, **kwargs: 1000
        )
        cfg = _config(
            tiers=_all_tiers(
                "unused",
                MEDIUM=TierModelConfig(
                    primary=[
                        {"model": "small", "max_input_tokens": 4},
                        {"model": "large-cooled", "max_input_tokens": 99999},
                        {"model": "large-ready", "max_input_tokens": 99999},
                    ],
                ),
            )
        )
        state = FallbackBackoffState(
            cfg.smart_proxy.fallback_backoff.model_copy(update={"enabled": True})
        )
        state.record_retryable_failure("large-cooled", "default")
        router = Router(cfg, fallback_backoff_state=state)
        _force_tier(router, "MEDIUM")

        decision = router.route(_messages(), profile="auto")

        assert decision.model == "large-ready"

    def test_raises_when_all_known_limit_candidates_are_over_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "optiproxai.router._estimate_tokens", lambda messages, **kwargs: 1000
        )
        cfg = _config(
            tiers={
                "MEDIUM": TierModelConfig(
                    primary=[{"model": "small-primary", "max_input_tokens": 4}],
                    fallback=[{"model": "small-fallback", "max_input_tokens": 4}],
                ),
                "COMPLEX": TierModelConfig(
                    primary=[{"model": "small-complex", "max_input_tokens": 4}]
                ),
            }
        )
        router = Router(cfg)
        _force_tier(router, "MEDIUM")

        with pytest.raises(InputLimitNotSatisfiedError) as exc_info:
            router.route(_messages(), profile="auto")

        assert exc_info.value.prompt_tokens > 4
        assert exc_info.value.profile == "auto"
        assert exc_info.value.tier == "MEDIUM"

    def test_cooldown_ignore_never_reintroduces_over_limit_candidate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "optiproxai.router._estimate_tokens", lambda messages, **kwargs: 1000
        )
        cfg = _config(
            tiers=_all_tiers(
                "unused",
                MEDIUM=TierModelConfig(
                    primary=[
                        {"model": "small", "max_input_tokens": 4},
                        {"model": "large-cooled", "max_input_tokens": 99999},
                    ],
                ),
            )
        )
        state = FallbackBackoffState(
            cfg.smart_proxy.fallback_backoff.model_copy(update={"enabled": True})
        )
        state.record_retryable_failure("large-cooled", "default")
        router = Router(cfg, fallback_backoff_state=state)
        _force_tier(router, "MEDIUM")

        decision = router.route(_messages(), profile="auto")

        assert decision.model == "large-cooled"
        assert decision.model != "small"


class TestLastContextCacheRouting:
    """route() gates input limits against max(cached provider prompt, live estimate)."""

    def test_cached_oversized_prompt_skips_capped_primary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "optiproxai.router._estimate_tokens", lambda messages, **kwargs: 100
        )
        cfg = _config(
            tiers=_all_tiers(
                "unused",
                MEDIUM=TierModelConfig(
                    primary=[
                        {"model": "capped", "max_input_tokens": 160000},
                        {"model": "large", "max_input_tokens": 1000000},
                    ],
                ),
            )
        )
        cache = LastContextCache()
        cache.record("session-1", 185000)
        router = Router(cfg, last_context_cache=cache)
        _force_tier(router, "MEDIUM")

        decision = router.route(_messages(), profile="auto", session_key="session-1")

        assert decision.model == "large"

    def test_first_turn_cache_miss_uses_live_estimate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "optiproxai.router._estimate_tokens", lambda messages, **kwargs: 100
        )
        cfg = _config(
            tiers=_all_tiers(
                "unused",
                MEDIUM=TierModelConfig(
                    primary=[
                        {"model": "capped", "max_input_tokens": 160000},
                        {"model": "large", "max_input_tokens": 1000000},
                    ],
                ),
            )
        )
        router = Router(cfg, last_context_cache=LastContextCache())
        _force_tier(router, "MEDIUM")

        decision = router.route(
            _messages(), profile="auto", session_key="fresh-session"
        )

        assert decision.model == "capped"

    def test_live_estimate_larger_than_cache_wins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "optiproxai.router._estimate_tokens", lambda messages, **kwargs: 200000
        )
        cfg = _config(
            tiers=_all_tiers(
                "unused",
                MEDIUM=TierModelConfig(
                    primary=[
                        {"model": "capped", "max_input_tokens": 160000},
                        {"model": "large", "max_input_tokens": 1000000},
                    ],
                ),
            )
        )
        cache = LastContextCache()
        cache.record("session-1", 1000)
        router = Router(cfg, last_context_cache=cache)
        _force_tier(router, "MEDIUM")

        decision = router.route(_messages(), profile="auto", session_key="session-1")

        assert decision.model == "large"

    def test_no_session_key_ignores_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "optiproxai.router._estimate_tokens", lambda messages, **kwargs: 100
        )
        cfg = _config(
            tiers=_all_tiers(
                "unused",
                MEDIUM=TierModelConfig(
                    primary=[
                        {"model": "capped", "max_input_tokens": 160000},
                        {"model": "large", "max_input_tokens": 1000000},
                    ],
                ),
            )
        )
        cache = LastContextCache()
        cache.record("session-1", 185000)
        router = Router(cfg, last_context_cache=cache)
        _force_tier(router, "MEDIUM")

        decision = router.route(_messages(), profile="auto")

        assert decision.model == "capped"

    def test_empty_session_key_uses_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "optiproxai.router._estimate_tokens", lambda messages, **kwargs: 100
        )
        cfg = _config(
            tiers=_all_tiers(
                "unused",
                MEDIUM=TierModelConfig(
                    primary=[
                        {"model": "capped", "max_input_tokens": 160000},
                        {"model": "large", "max_input_tokens": 1000000},
                    ],
                ),
            )
        )
        cache = LastContextCache()
        cache.record("", 185000)
        router = Router(cfg, last_context_cache=cache)
        _force_tier(router, "MEDIUM")

        decision = router.route(_messages(), profile="auto", session_key="")

        assert decision.model == "large"

    def test_decision_carries_session_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "optiproxai.router._estimate_tokens", lambda messages, **kwargs: 100
        )
        cfg = _config(tiers=_all_tiers("unused"))
        router = Router(cfg, last_context_cache=LastContextCache())
        _force_tier(router, "MEDIUM")

        decision = router.route(_messages(), profile="auto", session_key="session-1")

        assert decision.session_key == "session-1"
        assert "session_key" not in decision.model_dump()
