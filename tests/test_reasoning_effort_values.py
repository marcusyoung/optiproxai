"""Tests for the per-provider/model reasoning_effort_values allow-list (TASK-25).

Covers: resolution precedence (model rule > provider > style default), in-list
pass-through, out-of-list coercion, empty-list suppression, the unchanged style
default for providers without the field, and both the primary and fallback
injection paths (including the /optiproxai:<tier> override).
"""

from __future__ import annotations

from typing import Any

import optiproxai.proxy as proxy_mod
from optiproxai.config import (
    ModelRuleEntry,
    OptiproxaiConfig,
    ProviderConfig,
)
from optiproxai.proxy import RuntimeState
from optiproxai.router import Router

_FULL_VOCAB = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]


def _state(
    model_rules: list[ModelRuleEntry] | None = None,
    providers: dict[str, ProviderConfig] | None = None,
) -> RuntimeState:
    """RuntimeState with an xai-styled doubleword provider by default."""
    provs = providers or {
        "doubleword": ProviderConfig(
            name="doubleword",
            base_url="https://api.doubleword.ai/v1",
            reasoning_style="xai",
        ),
    }
    cfg = OptiproxaiConfig(providers=provs, model_rules=model_rules or [])
    return RuntimeState(
        config_path=None,
        config=cfg,
        router=Router(cfg),
        fallback_backoff_state=Router(cfg).fallback_backoff_state,
        config_loaded_at="test",
        version=1,
    )


class TestResolutionPrecedence:
    """Model rule > provider > style default, with [] as a real declaration."""

    def test_model_rule_wins_over_provider(self) -> None:
        state = _state(
            model_rules=[
                ModelRuleEntry(
                    prefix="deepseek-ai/DeepSeek-V4.1-Flash",
                    provider="doubleword",
                    reasoning_effort_values=["max"],
                )
            ],
            providers={
                "doubleword": ProviderConfig(
                    name="doubleword",
                    base_url="https://api.doubleword.ai/v1",
                    reasoning_style="xai",
                    reasoning_effort_values=["low"],
                )
            },
        )
        resolved = proxy_mod._resolve_reasoning_effort_values(
            "deepseek-ai/DeepSeek-V4.1-Flash", "doubleword", state
        )
        assert resolved == ["max"]

    def test_provider_wins_over_style_default(self) -> None:
        state = _state(
            providers={
                "doubleword": ProviderConfig(
                    name="doubleword",
                    base_url="https://api.doubleword.ai/v1",
                    reasoning_style="xai",
                    reasoning_effort_values=_FULL_VOCAB,
                )
            }
        )
        resolved = proxy_mod._resolve_reasoning_effort_values(
            "any-model", "doubleword", state
        )
        assert resolved == _FULL_VOCAB

    def test_unset_everywhere_returns_none(self) -> None:
        state = _state()
        resolved = proxy_mod._resolve_reasoning_effort_values(
            "any-model", "doubleword", state
        )
        assert resolved is None

    def test_empty_rule_list_wins_over_provider(self) -> None:
        state = _state(
            model_rules=[
                ModelRuleEntry(
                    prefix="silent-model",
                    provider="doubleword",
                    reasoning_effort_values=[],
                )
            ],
            providers={
                "doubleword": ProviderConfig(
                    name="doubleword",
                    base_url="https://api.doubleword.ai/v1",
                    reasoning_style="xai",
                    reasoning_effort_values=_FULL_VOCAB,
                )
            },
        )
        resolved = proxy_mod._resolve_reasoning_effort_values(
            "silent-model", "doubleword", state
        )
        assert resolved == []

    def test_most_specific_prefix_wins(self) -> None:
        state = _state(
            model_rules=[
                ModelRuleEntry(prefix="deepseek", reasoning_effort_values=["low"]),
                ModelRuleEntry(
                    prefix="deepseek-ai/DeepSeek-V4.1-Flash",
                    reasoning_effort_values=["max"],
                ),
            ]
        )
        resolved = proxy_mod._resolve_reasoning_effort_values(
            "deepseek-ai/DeepSeek-V4.1-Flash", "doubleword", state
        )
        assert resolved == ["max"]

    def test_provider_specific_rule_outranks_agnostic(self) -> None:
        state = _state(
            model_rules=[
                ModelRuleEntry(prefix="*", reasoning_effort_values=["low"]),
                ModelRuleEntry(
                    prefix="model-x",
                    provider="doubleword",
                    reasoning_effort_values=["max"],
                ),
            ]
        )
        resolved = proxy_mod._resolve_reasoning_effort_values(
            "model-x", "doubleword", state
        )
        assert resolved == ["max"]

    def test_rule_for_other_provider_ignored(self) -> None:
        state = _state(
            model_rules=[
                ModelRuleEntry(
                    prefix="model-x",
                    provider="other",
                    reasoning_effort_values=["max"],
                )
            ]
        )
        assert (
            proxy_mod._resolve_reasoning_effort_values("model-x", "doubleword", state)
            is None
        )

    def test_unknown_provider_returns_none(self) -> None:
        state = _state()
        assert proxy_mod._resolve_reasoning_effort_values("m", "missing", state) is None


class TestNormalization:
    """Allow-list overrides change which labels survive normalization."""

    def test_full_vocab_passes_max_through(self) -> None:
        assert proxy_mod._normalize_reasoning_effort("xai", "max", _FULL_VOCAB) == "max"

    def test_full_vocab_passes_xhigh_and_minimal_through(self) -> None:
        assert (
            proxy_mod._normalize_reasoning_effort("xai", "xhigh", _FULL_VOCAB)
            == "xhigh"
        )
        assert (
            proxy_mod._normalize_reasoning_effort("xai", "minimal", _FULL_VOCAB)
            == "minimal"
        )

    def test_out_of_list_max_coerces_to_high(self) -> None:
        assert (
            proxy_mod._normalize_reasoning_effort(
                "xai", "max", ["none", "low", "medium", "high"]
            )
            == "high"
        )

    def test_out_of_list_unknown_coerces_to_medium(self) -> None:
        assert (
            proxy_mod._normalize_reasoning_effort("xai", "minimal", ["none", "low"])
            == "medium"
        )

    def test_aliases_still_apply_with_explicit_allow_list(self) -> None:
        # extra-high aliases to xhigh, which is present in the allow-list.
        assert (
            proxy_mod._normalize_reasoning_effort("xai", "extra-high", _FULL_VOCAB)
            == "xhigh"
        )

    def test_allow_list_comparison_is_case_insensitive(self) -> None:
        assert (
            proxy_mod._normalize_reasoning_effort("xai", "max", ["MAX", " High "])
            == "max"
        )

    def test_style_default_unchanged_when_no_override(self) -> None:
        # Providers without the field keep the current xai cap.
        assert proxy_mod._normalize_reasoning_effort("xai", "max") == "high"
        assert proxy_mod._normalize_reasoning_effort("xai", "xhigh") == "high"
        assert proxy_mod._normalize_reasoning_effort("xai", "low") == "low"
        # anthropic default still accepts max/xhigh
        assert proxy_mod._normalize_reasoning_effort("anthropic", "max") == "max"
        # unknown style keeps the legacy {low, medium, high} set
        assert proxy_mod._normalize_reasoning_effort("weird", "max") == "high"
        assert proxy_mod._normalize_reasoning_effort("weird", "none") == "medium"


class TestApplyInjection:
    """_apply_reasoning_for_style honours the resolved allow-list."""

    def _body(self) -> dict[str, Any]:
        return {"model": "m", "messages": [{"role": "user", "content": "hi"}]}

    def test_injects_max_with_provider_vocab(self) -> None:
        body = proxy_mod._apply_reasoning_for_style(
            self._body(), "xai", effort="max", allowed_values=_FULL_VOCAB
        )
        assert body["reasoning_effort"] == "max"

    def test_empty_allow_list_suppresses_injection(self) -> None:
        body = proxy_mod._apply_reasoning_for_style(
            self._body(), "xai", effort="max", allowed_values=[]
        )
        assert "reasoning_effort" not in body

    def test_explicit_client_control_preserved(self) -> None:
        body = self._body()
        body["reasoning_effort"] = "low"
        result = proxy_mod._apply_reasoning_for_style(
            body, "xai", effort="max", allowed_values=_FULL_VOCAB
        )
        assert result["reasoning_effort"] == "low"

    def test_none_style_unchanged(self) -> None:
        body = proxy_mod._apply_reasoning_for_style(
            self._body(), "none", effort="max", allowed_values=_FULL_VOCAB
        )
        assert "reasoning_effort" not in body


_UPSTREAM_RESPONSE = {
    "id": "x",
    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}


def _make_proxy(tmp_path: Any, monkeypatch: Any, config_text: str, *, fail_first=False):
    """Configure the proxy with upstream mocked; return (client, captured, ctx)."""
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    cfg = tmp_path / "config.yaml"
    cfg.write_text(config_text)
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
    ) -> Any:
        captured.append(body)
        if fail_first and len(captured) == 1:
            return JSONResponse(status_code=500, content={"error": "boom"})
        return JSONResponse(content=_UPSTREAM_RESPONSE)

    monkeypatch.setattr(proxy_mod, "_proxy_upstream", fake_proxy_upstream)
    return TestClient(proxy_mod.app), captured


def _reasoning_request(client: Any) -> Any:
    return client.post(
        "/v1/chat/completions",
        json={
            "model": "optiproxai/auto",
            "messages": [{"role": "user", "content": "/optiproxai:reasoning hi"}],
        },
    )


_PROVIDER_FULL_VOCAB = """\
default_provider: doubleword
default_profile: auto
providers:
  doubleword:
    name: doubleword
    base_url: "http://localhost:9999/v1"
    api_key: "fake"
    reasoning_style: xai
    reasoning_effort_values: [none, minimal, low, medium, high, xhigh, max]
model_rules:
  - prefix: "deepseek-ai/DeepSeek-V4.1-Flash"
    provider: doubleword
    reasoning_style: xai
    reasoning_effort_values: [none, minimal, low, medium, high, xhigh, max]
  - prefix: "zai-org/GLM-5.3-Flash"
    provider: doubleword
    reasoning_style: xai
    reasoning_effort_values: [none, low, medium, high]
profiles:
  auto:
    tiers:
      SIMPLE: {primary: "deepseek-ai/DeepSeek-V4.1-Flash"}
      MEDIUM: {primary: "deepseek-ai/DeepSeek-V4.1-Flash"}
      COMPLEX: {primary: "deepseek-ai/DeepSeek-V4.1-Flash"}
      REASONING:
        primary: "deepseek-ai/DeepSeek-V4.1-Flash"
        fallback: ["zai-org/GLM-5.3-Flash"]
        reasoning_effort: "max"
"""

_NO_OVERRIDE = """\
default_provider: doubleword
default_profile: auto
providers:
  doubleword:
    name: doubleword
    base_url: "http://localhost:9999/v1"
    api_key: "fake"
    reasoning_style: xai
model_rules:
  - prefix: "deepseek-ai/DeepSeek-V4.1-Flash"
    provider: doubleword
    reasoning_style: xai
profiles:
  auto:
    tiers:
      SIMPLE: {primary: "deepseek-ai/DeepSeek-V4.1-Flash"}
      MEDIUM: {primary: "deepseek-ai/DeepSeek-V4.1-Flash"}
      COMPLEX: {primary: "deepseek-ai/DeepSeek-V4.1-Flash"}
      REASONING:
        primary: "deepseek-ai/DeepSeek-V4.1-Flash"
        reasoning_effort: "max"
"""

_SUPPRESS_PRIMARY = """\
default_provider: doubleword
default_profile: auto
providers:
  doubleword:
    name: doubleword
    base_url: "http://localhost:9999/v1"
    api_key: "fake"
    reasoning_style: xai
model_rules:
  - prefix: "deepseek-ai/DeepSeek-V4.1-Flash"
    provider: doubleword
    reasoning_style: xai
    reasoning_effort_values: []
profiles:
  auto:
    tiers:
      SIMPLE: {primary: "deepseek-ai/DeepSeek-V4.1-Flash"}
      MEDIUM: {primary: "deepseek-ai/DeepSeek-V4.1-Flash"}
      COMPLEX: {primary: "deepseek-ai/DeepSeek-V4.1-Flash"}
      REASONING:
        primary: "deepseek-ai/DeepSeek-V4.1-Flash"
        reasoning_effort: "max"
"""


class TestProxyInjectionPaths:
    """End-to-end: the resolved allow-list governs what reaches upstream."""

    def test_primary_passes_max_with_provider_vocab(self, tmp_path, monkeypatch):
        client, captured = _make_proxy(tmp_path, monkeypatch, _PROVIDER_FULL_VOCAB)
        with client:
            resp = _reasoning_request(client)
        assert resp.status_code == 200
        assert captured[0]["model"] == "deepseek-ai/DeepSeek-V4.1-Flash"
        assert captured[0]["reasoning_effort"] == "max"

    def test_regression_without_field_coerces_max_to_high(self, tmp_path, monkeypatch):
        client, captured = _make_proxy(tmp_path, monkeypatch, _NO_OVERRIDE)
        with client:
            resp = _reasoning_request(client)
        assert resp.status_code == 200
        assert captured[0]["reasoning_effort"] == "high"

    def test_empty_allow_list_suppresses_primary_injection(self, tmp_path, monkeypatch):
        client, captured = _make_proxy(tmp_path, monkeypatch, _SUPPRESS_PRIMARY)
        with client:
            resp = _reasoning_request(client)
        assert resp.status_code == 200
        assert "reasoning_effort" not in captured[0]

    def test_fallback_uses_its_own_vocab(self, tmp_path, monkeypatch):
        client, captured = _make_proxy(
            tmp_path, monkeypatch, _PROVIDER_FULL_VOCAB, fail_first=True
        )
        with client:
            resp = _reasoning_request(client)
        assert resp.status_code == 200
        assert len(captured) == 2
        # Primary has the full vocab -> max passes through.
        assert captured[0]["reasoning_effort"] == "max"
        # Fallback has a narrower vocab -> max coerces to high.
        assert captured[1]["model"] == "zai-org/GLM-5.3-Flash"
        assert captured[1]["reasoning_effort"] == "high"
