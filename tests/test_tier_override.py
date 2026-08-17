"""Tests for the per-turn tier override (parse_tier_override + Router.route())."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from optiproxai.config import (
    OptiproxaiConfig,
    ProviderConfig,
    ProfileConfig,
    TierModelConfig,
)
from optiproxai.router import Router, parse_tier_override


def _make_config() -> OptiproxaiConfig:
    """Create a minimal routing config covering all four tiers."""
    return OptiproxaiConfig(
        host="0.0.0.0",
        port=18420,
        providers={
            "default": ProviderConfig(
                name="default",
                base_url="https://api.example.com/v1",
                api_key="test-key",
            )
        },
        default_provider="default",
        profiles={
            "auto": ProfileConfig(
                tiers={
                    "SIMPLE": TierModelConfig(primary="simple-model"),
                    "MEDIUM": TierModelConfig(primary="medium-model"),
                    "COMPLEX": TierModelConfig(primary="complex-model"),
                    "REASONING": TierModelConfig(primary="reasoning-model"),
                }
            )
        },
        default_profile="auto",
    )


class TestParseTierOverride:
    """Unit tests for the parse_tier_override helper."""

    @pytest.mark.parametrize("tier", ["SIMPLE", "MEDIUM", "COMPLEX", "REASONING"])
    def test_valid_tier_string_content(self, tier: str) -> None:
        """A valid token at position 0 of string content sets the override."""
        messages = [{"role": "user", "content": f"/optiproxai:{tier} hello world"}]
        override, stripped = parse_tier_override(messages)
        assert override == tier
        assert stripped[-1]["content"] == "hello world"

    def test_list_content_first_text_part(self) -> None:
        """List content strips the token from the first text part."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "/optiproxai:complex describe this"},
                    {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
                ],
            }
        ]
        override, stripped = parse_tier_override(messages)
        assert override == "COMPLEX"
        assert stripped[-1]["content"][0]["text"] == "describe this"
        # original input not mutated
        assert messages[0]["content"][0]["text"] == "/optiproxai:complex describe this"

    def test_list_content_token_in_later_part_ignored(self) -> None:
        """A token in a later part (not the first text part) is ignored."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "plain text first"},
                    {"type": "text", "text": "/optiproxai:reasoning later"},
                ],
            }
        ]
        override, stripped = parse_tier_override(messages)
        assert override is None
        assert stripped is messages

    def test_token_and_leading_whitespace_stripped(self) -> None:
        """The token and its trailing whitespace are stripped from content."""
        messages = [{"role": "user", "content": "/optiproxai:medium   spaced text"}]
        override, stripped = parse_tier_override(messages)
        assert override == "MEDIUM"
        assert stripped[-1]["content"] == "spaced text"

    @pytest.mark.parametrize("token", ["reasoning", "REASONING", "Reasoning"])
    def test_case_insensitive_tier_matching(self, token: str) -> None:
        """Tier matching is case-insensitive."""
        messages = [{"role": "user", "content": f"/optiproxai:{token} question"}]
        override, _ = parse_tier_override(messages)
        assert override == "REASONING"

    def test_invalid_tier_stripped_no_override(self) -> None:
        """An invalid tier yields override=None but the token is stripped."""
        messages = [{"role": "user", "content": "/optiproxai:foo hello"}]
        override, stripped = parse_tier_override(messages)
        assert override is None
        assert stripped[-1]["content"] == "hello"

    def test_invalid_tier_emits_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """An invalid tier logs a warning at log.warning level."""
        messages = [{"role": "user", "content": "/optiproxai:nope hello"}]
        with caplog.at_level("WARNING", logger="optiproxai.router"):
            parse_tier_override(messages)
        assert any("Invalid tier override" in rec.message for rec in caplog.records)

    def test_no_prefix_returns_original(self) -> None:
        """Messages without the token are returned unchanged (same object)."""
        messages: list[dict[str, Any]] = [{"role": "user", "content": "plain"}]
        override, stripped = parse_tier_override(messages)
        assert override is None
        assert stripped is messages

    def test_token_in_assistant_message_ignored(self) -> None:
        """Tokens in assistant messages do not trigger an override."""
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "/optiproxai:reasoning reply"},
        ]
        override, stripped = parse_tier_override(messages)
        assert override is None
        assert stripped is messages

    def test_token_in_earlier_user_message_ignored(self) -> None:
        """Only the latest user message is scanned."""
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": "/optiproxai:reasoning old turn"},
            {"role": "assistant", "content": "response"},
            {"role": "user", "content": "plain current turn"},
        ]
        override, stripped = parse_tier_override(messages)
        assert override is None
        assert stripped is messages

    def test_empty_content_after_stripping_preserved(self) -> None:
        """Stripping to empty preserves "" (the message is not removed)."""
        messages = [{"role": "user", "content": "/optiproxai:simple"}]
        override, stripped = parse_tier_override(messages)
        assert override == "SIMPLE"
        assert len(stripped) == 1
        assert stripped[-1]["content"] == ""


class TestRouterTierOverride:
    """Integration tests for Router.route() with tier_override."""

    @pytest.mark.parametrize("tier", ["SIMPLE", "MEDIUM", "COMPLEX", "REASONING"])
    def test_override_pins_tier(self, tier: str) -> None:
        """A valid override pins decision.tier and the tier's model."""
        router = Router(_make_config())
        decision = router.route(
            [{"role": "user", "content": "anything"}],
            profile="auto",
            tier_override=tier,
        )
        assert decision.tier == tier
        assert decision.model == f"{tier.lower()}-model"

    def test_override_lowercase_accepted(self) -> None:
        """A lowercase override is normalized to uppercase."""
        router = Router(_make_config())
        decision = router.route(
            [{"role": "user", "content": "anything"}],
            profile="auto",
            tier_override="reasoning",
        )
        assert decision.tier == "REASONING"

    def test_override_skips_scorer(self) -> None:
        """A valid override means _classify is never called."""
        router = Router(_make_config())
        with patch.object(Router, "_classify") as mock_classify:
            decision = router.route(
                [{"role": "user", "content": "anything"}],
                profile="auto",
                tier_override="REASONING",
            )
        mock_classify.assert_not_called()
        assert decision.score == 1.0
        assert decision.confidence == 1.0
        assert decision.signals == ["tier_override"]
        assert decision.agentic_score == 0.0

    def test_invalid_override_falls_through_to_scorer(self) -> None:
        """An invalid override logs a warning and runs normal scoring."""
        router = Router(_make_config())
        with (
            patch.object(
                Router,
                "_classify",
                return_value={
                    "tier": "MEDIUM",
                    "score": 0.3,
                    "confidence": 0.5,
                    "signals": {"method": "default"},
                    "agentic_score": 0.0,
                    "dimensions": [],
                },
            ) as mock_classify,
            patch("optiproxai.router.log.warning") as mock_warning,
        ):
            router.route(
                [{"role": "user", "content": "anything"}],
                profile="auto",
                tier_override="not-a-tier",
            )
        mock_classify.assert_called_once()
        assert any(
            "Invalid tier_override" in str(call.args[0])
            for call in mock_warning.call_args_list
        )

    def test_invalid_override_still_routes(self) -> None:
        """An invalid override still produces a routing decision."""
        router = Router(_make_config())
        decision = router.route(
            [{"role": "user", "content": "simple question"}],
            profile="auto",
            tier_override="not-a-tier",
        )
        assert decision.model  # some model selected by normal scoring

    def test_override_respects_capability_escalation(self) -> None:
        """Override with required_capabilities still escalates tiers if needed."""
        config = OptiproxaiConfig(
            host="0.0.0.0",
            port=18420,
            providers={
                "default": ProviderConfig(
                    name="default",
                    base_url="https://api.example.com/v1",
                    api_key="test-key",
                )
            },
            default_provider="default",
            profiles={
                "auto": ProfileConfig(
                    tiers={
                        "SIMPLE": TierModelConfig(primary="text-only-model"),
                        "MEDIUM": TierModelConfig(primary="text-only-model"),
                        "COMPLEX": TierModelConfig(primary="vision-model"),
                        "REASONING": TierModelConfig(primary="vision-model"),
                    }
                )
            },
            default_profile="auto",
            model_rules=[],
        )
        from optiproxai.config import ModelCapabilityEntry

        config.model_rules = [
            ModelCapabilityEntry(prefix="vision-model", capabilities=["vision"]),
        ]
        router = Router(config)
        decision = router.route(
            [{"role": "user", "content": "describe this"}],
            profile="auto",
            required_capabilities={"vision"},
            tier_override="SIMPLE",
        )
        # Escalated from overridden SIMPLE to a tier with a vision-capable model
        assert decision.model == "vision-model"


class TestProxyTierOverride:
    """Proxy integration tests for the tier override (chat completions + debug)."""

    _CONFIG_YAML = """\
default_provider: dummy
default_profile: auto
providers:
  dummy:
    name: dummy
    base_url: "http://localhost:9999/v1"
    api_key: "fake"
profiles:
  auto:
    tiers:
      SIMPLE: {primary: "auto-simple"}
      MEDIUM: {primary: "auto-medium"}
      COMPLEX: {primary: "auto-complex"}
      REASONING: {primary: "auto-reason"}
"""

    _UPSTREAM_RESPONSE = {
        "id": "x",
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }

    @pytest.fixture()
    def proxy_client(self, tmp_path, monkeypatch):
        """Configured proxy app with upstream forwarding mocked.

        Yields (client, captured) where `captured` accumulates dicts holding
        the exact body and decision handed to _proxy_upstream.
        """
        import optiproxai.proxy as proxy_mod
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient

        cfg = tmp_path / "config.yaml"
        cfg.write_text(self._CONFIG_YAML)
        monkeypatch.setenv("OPTIPROXAI_DATA_DIR", str(tmp_path / "data"))
        proxy_mod.configure(str(cfg))

        captured: list[dict[str, Any]] = []

        async def fake_proxy_upstream(
            base_url: str,
            api_key: str,
            body: dict[str, Any],
            decision: Any,
            profile: Any = None,
            **kwargs: Any,
        ):
            captured.append({"body": body, "decision": decision})
            response = JSONResponse(content=self._UPSTREAM_RESPONSE)
            if decision is not None:
                for key, value in proxy_mod._optiproxai_headers(decision).items():
                    response.headers[key] = value
            return response

        monkeypatch.setattr(proxy_mod, "_proxy_upstream", fake_proxy_upstream)

        with TestClient(proxy_mod.app) as client:
            yield client, captured

    @pytest.mark.parametrize("tier", ["SIMPLE", "MEDIUM", "COMPLEX", "REASONING"])
    def test_valid_override_pins_tier_via_header(self, proxy_client, tier: str) -> None:
        """A valid /optiproxai:<tier> token forces the overridden tier (AC #4)."""
        client, _ = proxy_client
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "optiproxai/auto",
                "messages": [{"role": "user", "content": f"/optiproxai:{tier} hi"}],
            },
        )
        assert resp.status_code == 200
        assert resp.headers["X-Optiproxai-Tier"] == tier

    def test_token_stripped_from_upstream_body(self, proxy_client) -> None:
        """The /optiproxai:<tier> token never reaches the upstream model (AC #5)."""
        client, captured = proxy_client
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "optiproxai/auto",
                "messages": [
                    {"role": "user", "content": "/optiproxai:complex hi there"}
                ],
            },
        )
        assert resp.status_code == 200
        upstream_body = captured[0]["body"]
        contents = [m["content"] for m in upstream_body["messages"]]
        assert contents == ["hi there"]
        assert not any("/optiproxai:" in str(c) for c in contents)
        assert upstream_body["model"] == "auto-complex"

    def test_tier_override_log_line(self, proxy_client, caplog) -> None:
        """A TIER_OVERRIDE INFO line is logged with request id + tier (AC #3)."""
        client, _ = proxy_client
        with caplog.at_level(logging.INFO, logger="optiproxai.proxy"):
            client.post(
                "/v1/chat/completions",
                json={
                    "model": "optiproxai/auto",
                    "messages": [
                        {"role": "user", "content": "/optiproxai:reasoning hi"}
                    ],
                },
            )
        tier_lines = [r for r in caplog.records if "TIER_OVERRIDE" in r.getMessage()]
        assert tier_lines, "expected a TIER_OVERRIDE log line"
        assert "tier_override=REASONING" in tier_lines[0].getMessage()

    def test_invalid_tier_stripped_and_normal_routing(self, proxy_client) -> None:
        """/optiproxai:foo strips the token but routes normally (AC #6)."""
        client, captured = proxy_client
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "optiproxai/auto",
                "messages": [{"role": "user", "content": "/optiproxai:foo hello"}],
            },
        )
        assert resp.status_code == 200
        contents = [m["content"] for m in captured[0]["body"]["messages"]]
        assert contents == ["hello"]
        # Normal scoring still ran — tier is not pinned to an invalid value
        assert resp.headers["X-Optiproxai-Tier"] in {
            "SIMPLE",
            "MEDIUM",
            "COMPLEX",
            "REASONING",
        }

    def test_token_in_history_not_triggered(self, proxy_client) -> None:
        """A token in history does not trigger override or stripping (AC #7)."""
        client, captured = proxy_client
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "optiproxai/auto",
                "messages": [
                    {"role": "user", "content": "/optiproxai:reasoning earlier turn"},
                    {"role": "assistant", "content": "earlier reply"},
                    {"role": "user", "content": "plain current turn"},
                ],
            },
        )
        assert resp.status_code == 200
        contents = [m["content"] for m in captured[0]["body"]["messages"]]
        assert contents[0] == "/optiproxai:reasoning earlier turn"

    def test_token_in_assistant_message_not_triggered(self, proxy_client) -> None:
        """A token in an assistant message does not trigger anything (AC #7)."""
        client, captured = proxy_client
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "optiproxai/auto",
                "messages": [
                    {"role": "assistant", "content": "/optiproxai:complex quoted"},
                    {"role": "user", "content": "plain question"},
                ],
            },
        )
        assert resp.status_code == 200
        contents = [m["content"] for m in captured[0]["body"]["messages"]]
        assert contents[0] == "/optiproxai:complex quoted"

    def test_route_debug_honours_override(self, proxy_client) -> None:
        """route_debug parses and passes the override to route() (ACs #8/#9)."""
        client, _ = proxy_client
        resp = client.post(
            "/v1/route",
            json={"messages": [{"role": "user", "content": "/optiproxai:complex hi"}]},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["tier_override"] == "COMPLEX"
        assert payload["tier"] == "COMPLEX"
        assert payload["model"] == "auto-complex"

    def test_route_debug_no_token_reports_null(self, proxy_client) -> None:
        """route_debug without a token reports tier_override: null (AC #9)."""
        client, _ = proxy_client
        resp = client.post(
            "/v1/route",
            json={"messages": [{"role": "user", "content": "plain prompt"}]},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["tier_override"] is None

    def test_route_debug_routes_with_stripped_messages(self, proxy_client) -> None:
        """route_debug routes with the token stripped (parity with chat endpoint)."""
        import optiproxai.proxy as proxy_mod

        client, _ = proxy_client
        state = proxy_mod._require_runtime_state()
        route_spy = MagicMock(wraps=state.router.route)
        with patch.object(state.router, "route", new=route_spy):
            resp = client.post(
                "/v1/route",
                json={
                    "messages": [
                        {"role": "user", "content": "/optiproxai:foo hello world"}
                    ]
                },
            )
        assert resp.status_code == 200
        routed_messages = route_spy.call_args.args[0]
        contents = [m["content"] for m in routed_messages]
        assert contents == ["hello world"]
        # /optiproxai:foo is an invalid tier, so the scorer runs on stripped content
        payload = resp.json()
        assert payload["tier_override"] is None


class TestCliTierOverride:
    """CLI integration tests for the tier override in `optiproxai route`."""

    _CONFIG_YAML = """\
default_provider: dummy
default_profile: auto
providers:
  dummy:
    name: dummy
    base_url: "http://localhost:9999/v1"
    api_key: "fake"
profiles:
  auto:
    tiers:
      SIMPLE: {primary: "simple-model"}
      MEDIUM: {primary: "medium-model"}
      COMPLEX: {primary: "complex-model"}
      REASONING: {primary: "reasoning-model"}
"""

    @pytest.fixture()
    def config_path(self, tmp_path):
        """Write a minimal config and return its path."""
        path = tmp_path / "config.yaml"
        path.write_text(self._CONFIG_YAML)
        return path

    @pytest.fixture()
    def runner(self):
        from click.testing import CliRunner

        return CliRunner()

    def test_route_calls_parse_tier_override(self, runner, config_path) -> None:
        """route_cmd() calls parse_tier_override on the messages list (AC #1)."""
        from optiproxai.cli import main

        with patch(
            "optiproxai.router.parse_tier_override", wraps=parse_tier_override
        ) as spy:
            result = runner.invoke(
                main, ["route", "hello", "--config", str(config_path)]
            )
        assert result.exit_code == 0, result.output
        spy.assert_called_once()

    def test_route_passes_tier_override_to_router(self, runner, config_path) -> None:
        """route_cmd() passes tier_override to router.route() (AC #2)."""
        from optiproxai.cli import main
        from optiproxai.config import load_config
        from optiproxai.router import Router

        cfg = load_config(str(config_path), strict=True)
        router = Router(cfg)
        route_spy = MagicMock(wraps=router.route)
        with patch("optiproxai.router.Router", return_value=router):
            with patch.object(router, "route", new=route_spy):
                result = runner.invoke(
                    main,
                    ["route", "/optiproxai:reasoning hi", "--config", str(config_path)],
                )
        assert result.exit_code == 0, result.output
        assert route_spy.call_args.kwargs.get("tier_override") == "REASONING"

    @pytest.mark.parametrize("tier", ["REASONING", "SIMPLE"])
    def test_override_shows_tier_in_output(
        self, runner, config_path, tier: str
    ) -> None:
        """A /optiproxai:<tier> prompt shows the overridden tier in the JSON output (ACs #3/#4)."""
        import json

        from optiproxai.cli import main

        result = runner.invoke(
            main,
            [
                "route",
                f"/optiproxai:{tier} explain something",
                "--config",
                str(config_path),
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["tier"] == tier

    def test_invalid_tier_falls_through_to_normal_scoring(
        self, runner, config_path
    ) -> None:
        """An invalid /optiproxai:foo prompt falls through to normal scoring (AC #5)."""
        import json

        from optiproxai.cli import main

        result = runner.invoke(
            main, ["route", "/optiproxai:foo hello", "--config", str(config_path)]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["tier"] in {"SIMPLE", "MEDIUM", "COMPLEX", "REASONING"}

    def test_no_prefix_routes_normally(self, runner, config_path) -> None:
        """A prompt without /optiproxai: prefix routes normally (AC #6)."""
        import json

        from optiproxai.cli import main

        result = runner.invoke(
            main, ["route", "hello world", "--config", str(config_path)]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["tier"] in {"SIMPLE", "MEDIUM", "COMPLEX", "REASONING"}

    def test_route_routes_with_stripped_messages(self, runner, config_path) -> None:
        """route_cmd() routes on stripped messages so invalid tokens don't pollute the scorer."""
        from optiproxai.cli import main
        from optiproxai.config import load_config
        from optiproxai.router import Router

        cfg = load_config(str(config_path), strict=True)
        router = Router(cfg)
        route_spy = MagicMock(wraps=router.route)
        with patch("optiproxai.router.Router", return_value=router):
            with patch.object(router, "route", new=route_spy):
                result = runner.invoke(
                    main,
                    [
                        "route",
                        "/optiproxai:foo hello world",
                        "--config",
                        str(config_path),
                    ],
                )
        assert result.exit_code == 0, result.output
        routed_messages = route_spy.call_args.args[0]
        contents = [m["content"] for m in routed_messages]
        assert contents == ["hello world"]
