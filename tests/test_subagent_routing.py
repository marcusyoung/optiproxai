"""Tests for config-declared named-custom-subagent routing pins (TASK-28)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from optiproxai.config import (
    DEFAULT_SUBAGENT_REQUIRE,
    DEFAULT_SUBAGENT_SIGNATURE,
    OptiproxaiConfig,
    ProfileConfig,
    ProviderConfig,
    SubagentRoute,
    TierModelConfig,
)
from optiproxai.router import Router

GENERIC = DEFAULT_SUBAGENT_REQUIRE
WEB_SIGNATURE = DEFAULT_SUBAGENT_SIGNATURE.format(name="web-researcher")


def _providers() -> dict[str, ProviderConfig]:
    return {
        "openrouter": ProviderConfig(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="test-key",
        ),
        "retrieval": ProviderConfig(
            name="retrieval",
            base_url="https://retrieval.example/v1",
            api_key="retrieval-key",
        ),
    }


def _profiles() -> dict[str, ProfileConfig]:
    return {
        "auto": ProfileConfig(
            tiers={
                "SIMPLE": TierModelConfig(primary="model-simple"),
                "MEDIUM": TierModelConfig(primary="model-medium"),
                "COMPLEX": TierModelConfig(primary="model-complex"),
                "REASONING": TierModelConfig(primary="model-reasoning"),
            }
        )
    }


def _config(**overrides) -> OptiproxaiConfig:
    base = {
        "providers": _providers(),
        "default_provider": "openrouter",
        "profiles": _profiles(),
        "default_profile": "auto",
    }
    base.update(overrides)
    return OptiproxaiConfig(**base)


def _web_route(**overrides) -> SubagentRoute:
    data = {
        "name": "web-researcher",
        "provider": "retrieval",
        "model": "hy3-retrieval",
        "reasoning_effort": "high",
    }
    data.update(overrides)
    return SubagentRoute(**data)


def _subagent_message() -> dict[str, str]:
    """A realistic subagent message carrying both markers."""
    return {
        "role": "user",
        "content": f"<system_reminder>\n{GENERIC}\n\n{WEB_SIGNATURE}\n</system_reminder>\n<user_query>research X</user_query>",
    }


class TestSubagentRouteModel:
    def test_default_signature_substitutes_name(self) -> None:
        route = _web_route()
        assert route.resolved_signature == WEB_SIGNATURE
        assert route.resolved_require == GENERIC

    def test_custom_literal_signature_without_placeholder(self) -> None:
        route = _web_route(signature="CUSTOM MARKER {name} and more")
        assert route.resolved_signature == "CUSTOM MARKER web-researcher and more"

    def test_fully_literal_signature_is_untouched(self) -> None:
        route = _web_route(signature="no placeholder here")
        assert route.resolved_signature == "no placeholder here"

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _web_route(name="   ")

    def test_blank_model_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _web_route(model="  ")

    def test_blank_signature_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _web_route(signature="   ")

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            _web_route(unexpected="x")


class TestSubagentConfigValidation:
    def test_empty_routes_is_noop(self) -> None:
        assert _config().subagent_routes == []

    def test_valid_route_blank_provider_resolves_to_default(self) -> None:
        cfg = _config(subagent_routes=[_web_route(provider="")])
        assert cfg.subagent_routes[0].provider == ""

    def test_unknown_provider_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _config(subagent_routes=[_web_route(provider="nope")])

    def test_duplicate_resolved_signature_rejected(self) -> None:
        route_a = _web_route()
        # Different name, but an explicit signature identical to route_a's.
        route_b = _web_route(name="other", signature=WEB_SIGNATURE)
        with pytest.raises(ValidationError):
            _config(subagent_routes=[route_a, route_b])

    def test_same_signature_distinct_require_is_allowed(self) -> None:
        # Matching uses the (signature, require) pair, so a shared signature with
        # different requirements stays reachable and must be accepted.
        route_a = SubagentRoute(
            name="a",
            signature="SHARED",
            require="REQ-A",
            provider="retrieval",
            model="m1",
        )
        route_b = SubagentRoute(
            name="b",
            signature="SHARED",
            require="REQ-B",
            provider="retrieval",
            model="m2",
        )
        cfg = _config(subagent_routes=[route_a, route_b])
        assert len(cfg.subagent_routes) == 2

    def test_distinct_signatures_accepted(self) -> None:
        cfg = _config(
            subagent_routes=[
                _web_route(),
                _web_route(name="code-reviewer", model="review-model"),
            ]
        )
        assert len(cfg.subagent_routes) == 2


class TestSubagentDetection:
    def test_both_markers_pin(self) -> None:
        router = Router(_config(subagent_routes=[_web_route()]))
        decision = router.route([_subagent_message()], profile="auto")
        assert decision.model == "hy3-retrieval"
        assert decision.provider == "retrieval"
        assert decision.reasoning_effort == "high"
        assert decision.signals == ["subagent_pin"]
        assert decision.score == 1.0
        assert decision.confidence == 1.0
        assert decision.fallbacks == []

    def test_named_marker_without_generic_does_not_pin(self) -> None:
        router = Router(_config(subagent_routes=[_web_route()]))
        messages = [{"role": "user", "content": f"{WEB_SIGNATURE} no generic here"}]
        with patch.object(
            Router,
            "_classify",
            return_value={
                "tier": "SIMPLE",
                "score": 0.1,
                "confidence": 0.9,
                "signals": [],
                "agentic_score": 0.0,
            },
        ) as mock_classify:
            decision = router.route(messages, profile="auto")
        assert mock_classify.call_count == 1
        assert decision.model == "model-simple"
        assert decision.signals != ["subagent_pin"]

    def test_require_empty_matches_named_marker_alone(self) -> None:
        router = Router(_config(subagent_routes=[_web_route(require="")]))
        messages = [{"role": "user", "content": f"{WEB_SIGNATURE}"}]
        decision = router.route(messages, profile="auto")
        assert decision.model == "hy3-retrieval"

    def test_marker_in_older_message_pins(self) -> None:
        router = Router(_config(subagent_routes=[_web_route()]))
        messages = [
            {"role": "system", "content": "system prompt"},
            _subagent_message(),
            {"role": "assistant", "content": "working"},
            {"role": "user", "content": "continue"},
        ]
        decision = router.route(messages, profile="auto")
        assert decision.model == "hy3-retrieval"

    def test_marker_split_across_list_text_parts(self) -> None:
        router = Router(_config(subagent_routes=[_web_route()]))
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": GENERIC},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                    {"type": "text", "text": WEB_SIGNATURE},
                ],
            }
        ]
        decision = router.route(messages, profile="auto")
        assert decision.model == "hy3-retrieval"

    def test_signature_and_require_in_different_messages_pins(self) -> None:
        # Cursor injects the named-signature line and the generic reminder into
        # different messages of the same turn; the guard must match both across
        # the whole list, not require them in the same message.
        router = Router(_config(subagent_routes=[_web_route()]))
        messages = [
            {"role": "system", "content": "parent system prompt"},
            {"role": "user", "content": f"{WEB_SIGNATURE}"},
            {"role": "assistant", "content": "working"},
            {"role": "user", "content": f"{GENERIC}"},
        ]
        decision = router.route(messages, profile="auto")
        assert decision.model == "hy3-retrieval"
        assert decision.signals == ["subagent_pin"]

    def test_require_anywhere_without_signature_does_not_pin(self) -> None:
        # The generic reminder alone (no named signature anywhere) must not pin.
        router = Router(_config(subagent_routes=[_web_route()]))
        messages = [{"role": "user", "content": f"{GENERIC} but no named line"}]
        with patch.object(
            Router,
            "_classify",
            return_value={
                "tier": "SIMPLE",
                "score": 0.1,
                "confidence": 0.9,
                "signals": [],
                "agentic_score": 0.0,
            },
        ) as mock_classify:
            decision = router.route(messages, profile="auto")
        assert mock_classify.call_count == 1
        assert decision.signals != ["subagent_pin"]

    def test_unrecognised_subagent_name_does_not_pin(self) -> None:
        router = Router(_config(subagent_routes=[_web_route()]))
        messages = [
            {
                "role": "user",
                "content": f"<system_reminder>\n{GENERIC}\n\n"
                'You are operating as the "other-agent" custom subagent.\n</system_reminder>',
            }
        ]
        with patch.object(
            Router,
            "_classify",
            return_value={
                "tier": "MEDIUM",
                "score": 0.4,
                "confidence": 0.8,
                "signals": [],
                "agentic_score": 0.0,
            },
        ) as mock_classify:
            decision = router.route(messages, profile="auto")
        assert mock_classify.call_count == 1
        assert decision.model == "model-medium"

    def test_competing_routes_first_wins(self) -> None:
        router = Router(
            _config(
                subagent_routes=[
                    SubagentRoute(
                        name="a",
                        signature="MARKER-A",
                        require="",
                        provider="retrieval",
                        model="first-model",
                    ),
                    SubagentRoute(
                        name="b",
                        signature="MARKER-B",
                        require="",
                        provider="retrieval",
                        model="second-model",
                    ),
                ]
            )
        )
        messages = [{"role": "user", "content": "MARKER-B then MARKER-A"}]
        decision = router.route(messages, profile="auto")
        assert decision.model == "first-model"

    def test_empty_config_parent_request_unchanged(self) -> None:
        router = Router(_config())
        with patch.object(
            Router,
            "_classify",
            return_value={
                "tier": "COMPLEX",
                "score": 0.9,
                "confidence": 0.9,
                "signals": [],
                "agentic_score": 0.0,
            },
        ) as mock_classify:
            decision = router.route([_subagent_message()], profile="auto")
        # No routes configured -> even a subagent-looking message routes normally.
        assert mock_classify.call_count == 1
        assert decision.model == "model-complex"

    def test_pinned_decision_logs_signal(self) -> None:
        from datetime import datetime, timezone

        from optiproxai.logger import RoutingLogger

        router = Router(_config(subagent_routes=[_web_route()]))
        router.route([_subagent_message()], profile="auto")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = RoutingLogger._log_dir / f"routing-{today}.jsonl"
        text = log_file.read_text(encoding="utf-8")
        assert "subagent_pin" in text
        assert "hy3-retrieval" in text

    def test_pin_requires_capabilities(self) -> None:
        # The pinned model declares no capabilities; a vision request must fail
        # rather than silently route an image to a non-vision model.
        from optiproxai.router import CapabilityNotSatisfiedError

        router = Router(_config(subagent_routes=[_web_route()]))
        with pytest.raises(CapabilityNotSatisfiedError):
            router.route(
                [_subagent_message()],
                profile="auto",
                required_capabilities={"vision"},
            )

    def test_pin_succeeds_when_capability_declared(self) -> None:
        from optiproxai.config import ModelRuleEntry

        router = Router(
            _config(
                subagent_routes=[_web_route()],
                model_rules=[
                    ModelRuleEntry(prefix="hy3-retrieval", capabilities=["vision"])
                ],
            )
        )
        decision = router.route(
            [_subagent_message()],
            profile="auto",
            required_capabilities={"vision"},
        )
        assert decision.model == "hy3-retrieval"
        # The decision must still report the requirements the proxy computed.
        assert decision.required_capabilities == ["vision"]

    def test_pinned_log_preserves_real_context(self) -> None:
        from datetime import datetime, timezone

        from optiproxai.logger import RoutingLogger

        router = Router(_config(subagent_routes=[_web_route()]))
        message = _subagent_message()
        message["content"] = "BLUEFIN marker " + message["content"]
        router.route([message], profile="auto")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = (
            (RoutingLogger._log_dir / f"routing-{today}.jsonl")
            .read_text(encoding="utf-8")
            .strip()
            .splitlines()
        )
        entry = __import__("json").loads(lines[-1])
        # The real classification text is logged, not the constant signature.
        assert "BLUEFIN" in entry["prompt"]
        assert entry["classification_context"]["subagent_route"] == "web-researcher"
        assert "last_user_message" in entry["classification_context"]
