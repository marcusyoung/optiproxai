"""Tests for optiproxai distilled feature scoring engine."""

from __future__ import annotations

import pickle
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np

from optiproxai.scorer import (
    DistilledFeatureClassifier,
    LocalEmbeddingBackend,
    RuntimeEmbeddingSettings,
    SEMANTIC_DIMENSIONS,
    ClassificationResult,
    Scorer,
    ScoringConfig,
    Tier,
    _tier_from_score,
    inspect_feature_classifier_runtime_status,
)


class _FakeClassifier:
    def __init__(self, encoded_label: int = 2, *, fail: bool = False) -> None:
        self.encoded_label = encoded_label
        self.fail = fail
        self.seen_shape: tuple[int, ...] | None = None

    def predict(self, embeddings: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
        self.seen_shape = tuple(embeddings.shape)
        if self.fail:
            raise RuntimeError("prediction failed")
        return np.full((1, len(SEMANTIC_DIMENSIONS)), self.encoded_label)


class _FakeLabelEncoder:
    def inverse_transform(self, values: list[int]) -> list[str]:
        mapping = {0: "low", 1: "medium", 2: "high"}
        return [mapping[int(value)] for value in values]


class _FakeEmbeddings:
    def __init__(self, embedding: list[float], *, delay: float = 0.0) -> None:
        self.embedding = embedding
        self.delay = delay
        self.calls: list[dict[str, Any]] = []

    def create(self, *, input: list[str], model: str) -> Any:
        self.calls.append({"input": input, "model": model})
        if self.delay:
            time.sleep(self.delay)
        item = type("EmbeddingItem", (), {"embedding": self.embedding})()
        return type("EmbeddingResponse", (), {"data": [item]})()


class _FakeEmbeddingClient:
    def __init__(self, embedding: list[float], *, delay: float = 0.0) -> None:
        self.embeddings = _FakeEmbeddings(embedding, delay=delay)


def _bundle(*, classifier: Any | None = None, embedding_dim: int = 3) -> dict[str, Any]:
    return {
        "classifier": classifier or _FakeClassifier(),
        "label_encoders": {
            dimension: _FakeLabelEncoder() for dimension in SEMANTIC_DIMENSIONS
        },
        "semantic_dimensions": list(SEMANTIC_DIMENSIONS),
        "embedding_model": "text-embedding-test",
        "embedding_dim": embedding_dim,
        "training_size": 1,
        "class_distribution": {},
        "weights": {
            "tokenCount": 0.2,
            **{dimension: 1.0 for dimension in SEMANTIC_DIMENSIONS},
        },
        "tier_thresholds": {"SIMPLE": 0.2, "MEDIUM": 0.45, "COMPLEX": 0.7},
        "feature_schema_version": "test-v1",
    }


def _write_bundle(model_dir: Path, bundle: dict[str, Any] | None = None) -> Path:
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "feature_classifier.pkl"
    with model_path.open("wb") as f:
        pickle.dump(bundle or _bundle(), f)
    return model_path


class TestDistilledFeatureScorer:
    def test_bundle_schema_validates_current_semantic_dimensions(
        self, tmp_path
    ) -> None:
        model_path = _write_bundle(tmp_path)

        classifier = DistilledFeatureClassifier.load(tmp_path)

        assert classifier.model_path == model_path
        assert classifier.embedding_dim == 3
        assert tuple(classifier.label_encoders) == SEMANTIC_DIMENSIONS

    def test_bundle_compat_predicts_with_mock_embedding(self, tmp_path) -> None:
        fake_classifier = _FakeClassifier(encoded_label=1)
        _write_bundle(tmp_path, _bundle(classifier=fake_classifier))
        embedding_client = _FakeEmbeddingClient([0.1, 0.2, 0.3])

        with patch(
            "optiproxai.scorer._resolve_runtime_embedding_client",
            return_value=(embedding_client, "unused-model"),
        ):
            classifier = DistilledFeatureClassifier.load(tmp_path)
            labels, confidence = classifier.predict("hello")

        assert confidence == 0.85
        assert set(labels) == set(SEMANTIC_DIMENSIONS)
        assert set(labels.values()) == {"medium"}
        assert classifier.classifier.seen_shape == (1, 3)
        assert embedding_client.embeddings.calls[0]["model"] == "text-embedding-test"

    def test_feature_model_dir_success_returns_distilled_features(
        self, tmp_path
    ) -> None:
        _write_bundle(tmp_path)
        embedding_client = _FakeEmbeddingClient([0.1, 0.2, 0.3])

        with patch(
            "optiproxai.scorer._resolve_runtime_embedding_client",
            return_value=(embedding_client, "unused-model"),
        ):
            result = Scorer(
                feature_model_dir=tmp_path, enable_routing_log=False
            ).classify("prove this theorem")

        assert isinstance(result, ClassificationResult)
        assert result.signals["method"]["raw"] == "distilled-features"
        assert result.signals["featureVersion"] == "test-v1"
        assert set(result.signals["semanticLabels"].values()) == {"high"}
        assert len(result.dimensions) == 15
        assert result.tier == Tier.REASONING
        assert result.agentic_score == 1.0

    def test_missing_model_returns_default_fallback(self, tmp_path) -> None:
        result = Scorer(feature_model_dir=tmp_path, enable_routing_log=False).classify(
            "prove why this is complex"
        )

        assert result.tier == Tier.MEDIUM
        assert result.confidence == 0.35
        assert result.score == 0.0
        assert result.signals["method"]["raw"] == "default"
        assert result.dimensions == []

    def test_load_failure_returns_configured_default_fallback(self, tmp_path) -> None:
        (tmp_path / "feature_classifier.pkl").write_bytes(b"not a pickle")
        config = ScoringConfig(fallback_tier=Tier.COMPLEX, fallback_confidence=0.31)

        result = Scorer(
            config=config, feature_model_dir=tmp_path, enable_routing_log=False
        ).classify("anything")

        assert result.tier == Tier.COMPLEX
        assert result.confidence == 0.31
        assert result.signals["method"]["raw"] == "default"

    def test_embedding_failure_returns_default_fallback(self, tmp_path) -> None:
        _write_bundle(tmp_path)

        with patch(
            "optiproxai.scorer._resolve_runtime_embedding_client",
            side_effect=RuntimeError("no embedding config"),
        ):
            result = Scorer(
                feature_model_dir=tmp_path, enable_routing_log=False
            ).classify("fix this bug")

        assert result.signals["method"]["raw"] == "default"
        assert result.dimensions == []

    def test_embedding_timeout_returns_default_fallback(self, tmp_path, caplog) -> None:
        _write_bundle(tmp_path)
        embedding_client = _FakeEmbeddingClient([0.1, 0.2, 0.3], delay=0.05)

        with (
            patch(
                "optiproxai.scorer._resolve_runtime_embedding_settings",
                return_value=RuntimeEmbeddingSettings(
                    mode="api",
                    model="text-embedding-test",
                    base_url="http://example.test/v1",
                    api_key="test-key",
                    timeout_seconds=0.001,
                ),
            ),
            patch(
                "optiproxai.scorer._resolve_runtime_embedding_client",
                return_value=(embedding_client, "text-embedding-test", 0.001),
            ),
            caplog.at_level("WARNING"),
        ):
            result = Scorer(
                feature_model_dir=tmp_path, enable_routing_log=False
            ).classify("hello")

        assert result.signals["method"]["raw"] == "default"
        assert result.confidence == 0.35
        assert "Runtime embedding timed out, using default fallback" in caplog.text
        assert "Traceback" not in caplog.text

    def test_api_embedding_uses_configured_model_and_timeout(self, tmp_path) -> None:
        _write_bundle(tmp_path)
        embedding_client = _FakeEmbeddingClient([0.1, 0.2, 0.3])

        with (
            patch(
                "optiproxai.scorer._resolve_runtime_embedding_settings",
                return_value=RuntimeEmbeddingSettings(
                    mode="api",
                    model="configured-embedding",
                    base_url="http://example.test/v1",
                    api_key="test-key",
                    timeout_seconds=1.25,
                ),
            ),
            patch(
                "optiproxai.scorer._resolve_runtime_embedding_client",
                return_value=(embedding_client, "configured-embedding", 1.25),
            ),
        ):
            classifier = DistilledFeatureClassifier.load(tmp_path)
            classifier.predict("hello")

        assert embedding_client.embeddings.calls == [
            {"input": ["hello"], "model": "configured-embedding"}
        ]

    def test_local_embedding_backend_does_not_call_api(self, tmp_path) -> None:
        _write_bundle(tmp_path)
        api_client = _FakeEmbeddingClient([9.0, 9.0, 9.0])

        with (
            patch(
                "optiproxai.scorer._resolve_runtime_embedding_settings",
                return_value=RuntimeEmbeddingSettings(
                    mode="local",
                    model="local-test-model",
                    base_url=None,
                    api_key="",
                    timeout_seconds=1.0,
                ),
            ),
            patch.object(
                LocalEmbeddingBackend,
                "embed",
                return_value=np.asarray([0.1, 0.2, 0.3], dtype=np.float32),
            ),
            patch(
                "optiproxai.scorer._resolve_runtime_embedding_client",
                return_value=(api_client, "should-not-use", 1.0),
            ),
        ):
            result = Scorer(
                feature_model_dir=tmp_path, enable_routing_log=False
            ).classify("hello")

        assert result.signals["method"]["raw"] == "distilled-features"
        assert api_client.embeddings.calls == []

    def test_embedding_disabled_returns_default_fallback(self, tmp_path) -> None:
        _write_bundle(tmp_path)

        with patch(
            "optiproxai.scorer._resolve_runtime_embedding_settings",
            return_value=RuntimeEmbeddingSettings(
                mode="disabled",
                model="",
                base_url=None,
                api_key="",
                timeout_seconds=1.0,
            ),
        ):
            result = Scorer(
                feature_model_dir=tmp_path, enable_routing_log=False
            ).classify("hello")

        assert result.tier == Tier.MEDIUM
        assert result.confidence == 0.35
        assert result.score == 0.0
        assert result.signals["method"]["raw"] == "default"

    def test_embedding_model_mismatch_is_warned(self, tmp_path, caplog) -> None:
        _write_bundle(tmp_path)

        with (
            patch(
                "optiproxai.scorer._resolve_runtime_embedding_settings",
                return_value=RuntimeEmbeddingSettings(
                    mode="api",
                    model="different-runtime-model",
                    base_url="http://example.test/v1",
                    api_key="test-key",
                    timeout_seconds=1.0,
                ),
            ),
            caplog.at_level("WARNING"),
        ):
            classifier = DistilledFeatureClassifier.load(tmp_path)

        assert classifier.embedding_model_mismatch is True
        assert "Runtime embedding model mismatch" in caplog.text

    def test_embedding_dimension_mismatch_returns_default_fallback(
        self, tmp_path
    ) -> None:
        _write_bundle(tmp_path, _bundle(embedding_dim=4))

        with (
            patch(
                "optiproxai.scorer._resolve_runtime_embedding_settings",
                return_value=RuntimeEmbeddingSettings(
                    mode="local",
                    model="local-test-model",
                    base_url=None,
                    api_key="",
                    timeout_seconds=1.0,
                ),
            ),
            patch.object(
                LocalEmbeddingBackend,
                "embed",
                return_value=np.asarray([0.1, 0.2, 0.3], dtype=np.float32),
            ),
        ):
            result = Scorer(
                feature_model_dir=tmp_path, enable_routing_log=False
            ).classify("hello")

        assert result.signals["method"]["raw"] == "default"

    def test_prediction_failure_returns_default_fallback(self, tmp_path) -> None:
        _write_bundle(tmp_path, _bundle(classifier=_FakeClassifier(fail=True)))
        embedding_client = _FakeEmbeddingClient([0.1, 0.2, 0.3])

        with patch(
            "optiproxai.scorer._resolve_runtime_embedding_client",
            return_value=(embedding_client, "unused-model"),
        ):
            result = Scorer(
                feature_model_dir=tmp_path, enable_routing_log=False
            ).classify("hello")

        assert result.signals["method"]["raw"] == "default"
        assert result.dimensions == []

    def test_default_fallback_never_uses_heuristic_semantic_labels(
        self, tmp_path
    ) -> None:
        with patch(
            "optiproxai.scorer._heuristic_semantic_labels",
            side_effect=AssertionError("heuristic fallback must not run"),
            create=True,
        ) as heuristic:
            result = Scorer(
                feature_model_dir=tmp_path, enable_routing_log=False
            ).classify("fix this bug and run tests")

        heuristic.assert_not_called()
        assert result.signals["method"]["raw"] == "default"
        assert "semanticLabels" not in result.signals

    def test_inspect_feature_classifier_runtime_status_reports_unloadable(
        self, tmp_path
    ) -> None:
        (tmp_path / "feature_classifier.pkl").write_bytes(b"bad")

        status = inspect_feature_classifier_runtime_status(tmp_path)

        assert status.supported is True
        assert status.exists is True
        assert status.loadable is False
        assert "unloadable" in status.message


def _thresholds() -> dict[str, float]:
    return {"SIMPLE": 0.2, "MEDIUM": 0.58, "COMPLEX": 0.72}


class TestAmbiguousBands:
    def test_no_bands_keeps_plain_threshold_mapping(self) -> None:
        assert _tier_from_score(0.10, _thresholds()) == Tier.SIMPLE
        assert _tier_from_score(0.40, _thresholds()) == Tier.MEDIUM
        assert _tier_from_score(0.60, _thresholds()) == Tier.COMPLEX
        assert _tier_from_score(0.80, _thresholds()) == Tier.REASONING

    def test_prefer_upper_fails_toward_higher_tier(self) -> None:
        bands = {
            "SIMPLE_MEDIUM": {"band": 0.05, "prefer": "UPPER"},
            "MEDIUM_COMPLEX": {"band": 0.05, "prefer": "UPPER"},
            "COMPLEX_REASONING": {"band": 0.05, "prefer": "UPPER"},
        }
        # SIMPLE/MEDIUM boundary (0.20): 0.18 and 0.22 both ambiguous -> MEDIUM
        assert _tier_from_score(0.18, _thresholds(), bands) == Tier.MEDIUM
        assert _tier_from_score(0.22, _thresholds(), bands) == Tier.MEDIUM
        # 0.12 is safely SIMPLE
        assert _tier_from_score(0.12, _thresholds(), bands) == Tier.SIMPLE
        # MEDIUM/COMPLEX boundary (0.58): 0.55 -> COMPLEX
        assert _tier_from_score(0.55, _thresholds(), bands) == Tier.COMPLEX
        # COMPLEX/REASONING boundary (0.72): 0.70 -> REASONING (fail up)
        assert _tier_from_score(0.70, _thresholds(), bands) == Tier.REASONING
        # 0.80 is safely REASONING
        assert _tier_from_score(0.80, _thresholds(), bands) == Tier.REASONING

    def test_prefer_lower_fails_toward_cheaper_tier(self) -> None:
        bands = {"COMPLEX_REASONING": {"band": 0.05, "prefer": "LOWER"}}
        # 0.70 is below the boundary -> COMPLEX regardless
        assert _tier_from_score(0.70, _thresholds(), bands) == Tier.COMPLEX
        # 0.74 is just above the 0.72 boundary -> pulled down to COMPLEX
        assert _tier_from_score(0.74, _thresholds(), bands) == Tier.COMPLEX
        # 0.78 is safely REASONING
        assert _tier_from_score(0.78, _thresholds(), bands) == Tier.REASONING

    def test_zero_band_is_noop(self) -> None:
        bands = {"SIMPLE_MEDIUM": {"band": 0.0, "prefer": "UPPER"}}
        assert _tier_from_score(0.10, _thresholds(), bands) == Tier.SIMPLE

    def test_invalid_band_rejected(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            _tier_from_score(
                0.5,
                _thresholds(),
                {"SIMPLE_MEDIUM": {"band": -1.0, "prefer": "UPPER"}},
            )
        with pytest.raises(ValueError):
            _tier_from_score(0.5, _thresholds(), {"SIMPLE_MEDIUM": {"band": "wide"}})
        with pytest.raises(ValueError):
            _tier_from_score(
                0.5, _thresholds(), {"SIMPLE_MEDIUM": {"band": 0.05, "prefer": "FAST"}}
            )
        with pytest.raises(ValueError):
            _tier_from_score(
                0.5, _thresholds(), {"unknown": {"band": 0.05, "prefer": "UPPER"}}
            )

    def test_axis_override_still_disables_ambiguity_when_disabled(self) -> None:
        # With disable_axis_overrides=True the base tier (with ambiguity) is returned.
        from optiproxai.scorer import _tier_from_axes

        bands = {"COMPLEX_REASONING": {"band": 0.1, "prefer": "LOWER"}}
        labels = {dim: "low" for dim in SEMANTIC_DIMENSIONS}
        labels["reasoningMarkers"] = "high"
        tier = _tier_from_axes(
            0.74,
            labels,
            _thresholds(),
            disable_axis_overrides=True,
            ambiguous_bands=bands,
        )
        assert tier == Tier.COMPLEX
