"""OptiProxAI configuration models and loader.

Supports YAML config files with ${ENV_VAR} resolution and merging with defaults.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class ConfigNotFoundError(Exception):
    """Raised when no configuration file can be found."""

    def __init__(self, searched_paths: list[Path] | None = None) -> None:
        from optiproxai.dirs import config_dir

        xdg_path = config_dir() / "config.yaml"
        paths_str = ""
        if searched_paths:
            paths_str = "\n".join(f"  - {p}" for p in searched_paths)

        msg = (
            "No OptiProxAI configuration file found.\n"
            "\n"
            "Run `optiproxai init` to create a starter config, or create one manually.\n"
        )
        if paths_str:
            msg += f"\nSearched:\n{paths_str}\n"
        msg += f"\nDefault location: {xdg_path}\nOr set OPTIPROXAI_CONFIG=/path/to/config.yaml"
        super().__init__(msg)
        self.searched_paths = searched_paths


class ConfigIncompleteError(Exception):
    """Raised when config exists but is missing required sections (e.g. profiles)."""

    def __init__(self, missing: str, config_path: Path | None = None) -> None:
        loc = f" ({config_path})" if config_path else ""
        msg = (
            f"Configuration{loc} is missing required section: {missing}\n"
            "\n"
            "Run `optiproxai init` to generate a complete starter config,\n"
            "or add the missing section to your config file.\n"
            "See: https://github.com/marcusyoung/optiproxai#configuration"
        )
        super().__init__(msg)
        self.missing = missing
        self.config_path = config_path


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ContentPartPolicy(BaseModel):
    """Candidate-specific message content part normalization policy."""

    mode: Literal["preserve", "normalize"] = "preserve"
    allowed_types: list[str] = Field(default_factory=list)
    text_types: list[str] = Field(default_factory=list)
    image_types: list[str] = Field(default_factory=list)
    drop_types: list[str] = Field(default_factory=list)
    unknown: Literal["preserve", "text", "drop"] = "preserve"


class AsyncModeConfig(BaseModel):
    """Explicit async/batch request declaration with three delivery modes.

    - ``body``: inject ``{field: value}`` into the request body JSON
      (e.g. OpenAI ``service_tier: flex``).
    - ``header``: inject HTTP header ``{field: value}`` on the upstream request.
    - ``model_suffix``: append ``:{suffix}`` to the model name sent upstream.

    Opt-in model (TASK-10): the **provider** declares the mechanism
    (``delivery``/``field``/``value``/``suffix``), the **model/rule** declares
    intent (``enabled: true`` opts in).  Provider-level ``enabled`` is ignored
    (vestigial).  Default is ``enabled: false`` (sync).

    ``{enabled: true}`` alone is valid — the mechanism may be inherited from
    a lower-precedence level at runtime.  Partial mechanisms (some mechanism
    fields explicitly set but incomplete for the effective delivery) are
    rejected at config load to catch typos.
    """

    enabled: bool = False
    delivery: Literal["body", "header", "model_suffix"] = "body"
    field: str = ""  # body/header: the field or header name
    value: str = ""  # body/header: the value to send
    suffix: str = ""  # model_suffix: the suffix appended after ':'

    @model_validator(mode="after")
    def _validate_enabled_fields(self) -> "AsyncModeConfig":
        """Reject partial mechanisms; accept ``{enabled: true}`` as a pure flag.

        Only fires when mechanism fields are explicitly set (via
        ``model_fields_set``).  A pure ``{enabled: true}`` flag is valid —
        the mechanism may come from a lower-precedence level at runtime.
        """
        explicit = self.model_fields_set
        # No mechanism fields explicitly set → pure flag (or pure default).
        mechanism_fields = explicit & {"delivery", "field", "value", "suffix"}
        if not mechanism_fields:
            return self

        # At least one mechanism field is set — require a complete mechanism
        # for the effective delivery.
        if self.delivery in ("body", "header"):
            if not self.field:
                raise ValueError(
                    f"async_mode.field is required when delivery={self.delivery!r} "
                    f"and mechanism fields are set"
                )
            if not self.value:
                raise ValueError(
                    f"async_mode.value is required when delivery={self.delivery!r} "
                    f"and mechanism fields are set"
                )
        elif self.delivery == "model_suffix":
            if not self.suffix:
                raise ValueError(
                    "async_mode.suffix is required when delivery='model_suffix' "
                    "and mechanism fields are set"
                )
        return self


class CacheControlConfig(BaseModel):
    """Opt-in prompt-caching marker injection policy.

    Providers that require explicit ``cache_control`` markers (e.g. Doubleword,
    which follows Anthropic's prefix-cache model) get markers injected into the
    stable request prefix so cache discounts apply.  A marker caches everything
    from the start of the request up to and including the marked block.

    - ``target: system`` — marker on the last content block of the first system
      message (caches tools + system prompt).
    - ``target: tools`` — marker on the last object of the ``tools`` array
      (caches tool definitions only).
    - ``target: last_message`` — marker on the last content block of the final
      message (caches the multi-turn conversation prefix; TASK-21).

    ``targets`` optionally overrides ``target`` with a list of breakpoints
    applied in canonical request order (tools -> system -> last_message),
    e.g. ``targets: [tools, system, last_message]``.  When ``targets`` is
    unset, the effective target list is ``[target]`` (single-target
    behavior).  When both are set, ``targets`` wins (doc-11).

    Resolution is presence-based highest-precedence-wins (decision doc-7):
    best-matching ``ModelRuleEntry.cache_control`` -> ``ProviderConfig.cache_control``
    -> none.  No field-by-field merge.
    """

    enabled: bool = False
    ttl: Literal["5m", "1h"] = "5m"
    target: Literal["system", "tools", "last_message"] = "system"
    targets: list[Literal["system", "tools", "last_message"]] | None = Field(
        default=None,
        description=(
            "Multiple breakpoint targets applied in canonical request order "
            "(tools -> system -> last_message).  Overrides ``target`` when "
            "set; unset means the single ``target`` behavior."
        ),
    )
    max_breakpoints: int = Field(
        default=4,
        gt=0,
        description=(
            "Maximum cache_control markers per request.  Default 4 is "
            "Doubleword's documented ceiling; tunable per provider."
        ),
    )


class ModelPricingConfig(BaseModel):
    """Optional per-model pricing metadata for dashboard cost estimates.

    All values are USD per 1M tokens.  Any field may be omitted; the
    dashboard's savings estimate only uses the components present.  This is
    display-only metadata (decision doc-10) — routing behavior is unaffected.
    """

    model_config = ConfigDict(extra="forbid")

    input_per_mtok: float | None = Field(default=None, ge=0)
    cache_read_per_mtok: float | None = Field(default=None, ge=0)
    cache_write_per_mtok: float | None = Field(default=None, ge=0)


class ProviderConfig(BaseModel):
    """A backend LLM provider (OpenRouter, Anthropic, local proxy, etc.)."""

    name: str  # e.g. 'openrouter', 'cliproxy', 'anthropic'
    base_url: str  # e.g. 'https://openrouter.ai/api/v1'
    api_key: str = ""  # can reference env var with ${ENV_VAR}
    models: list[str] = Field(default_factory=list)  # optional model whitelist
    reasoning_style: Literal[
        "openai", "xai", "anthropic", "dashscope", "gemini", "none"
    ] = "none"
    supports_reasoning_content: bool = False
    async_mode: AsyncModeConfig | None = Field(
        default=None,
        description=(
            "Provider-level async/batch mechanism declaration (delivery/"
            "field/value/suffix).  The provider declares HOW async works; "
            "model/rule-level async_mode.enabled declares WHO opts in.  "
            "Provider-level enabled is ignored (vestigial)."
        ),
    )
    cache_control: CacheControlConfig | None = Field(
        default=None,
        description=(
            "Provider-level opt-in prompt-caching marker injection policy.  "
            "May be overridden per-model via model_rules[].cache_control."
        ),
    )


class ModelEntry(BaseModel):
    """A model with optional routing metadata and provider override."""

    model_config = ConfigDict(extra="forbid")

    model: str
    provider: str = ""  # empty = inherit from tier or default
    max_input_tokens: int | None = Field(
        default=None,
        gt=0,
        description="Optional maximum input prompt tokens for routing eligibility.",
    )
    async_mode: AsyncModeConfig | None = Field(
        default=None,
        description=(
            "Per-model async/batch opt-in or override.  Set enabled: true to "
            "opt in (mechanism inherited from provider/rule).  Set enabled: "
            "false to explicitly opt out.  Mechanism fields (delivery/field/"
            "value/suffix) override the provider's mechanism."
        ),
    )
    pricing: ModelPricingConfig | None = Field(
        default=None,
        description=(
            "Optional per-model pricing metadata (USD per 1M tokens) used by "
            "the dashboard to estimate prompt-cache savings.  Display-only."
        ),
    )


class ResolvedModelCandidate(BaseModel):
    """A normalized model candidate with preserved routing metadata."""

    model: str
    provider: str = ""
    max_input_tokens: int | None = None
    async_mode: AsyncModeConfig | None = None
    pricing: ModelPricingConfig | None = None

    def as_tuple(self) -> tuple[str, str]:
        """Return the backward-compatible (model, provider) tuple."""
        return self.model, self.provider


class TierModelConfig(BaseModel):
    """Model selection for a single complexity tier within a profile."""

    primary: str | ModelEntry | list[str | ModelEntry]
    fallback: list[str | ModelEntry] = Field(default_factory=list)
    provider: str = "default"  # tier-level default provider
    reasoning_effort: str | None = (
        None  # low | medium | high | none (tier-level override)
    )
    primary_selection: Literal["round_robin", "session_sticky"] = "round_robin"

    @model_validator(mode="after")
    def _validate_primary_not_empty(self) -> "TierModelConfig":
        """Ensure normalized primary candidate list is non-empty."""
        if not self.resolve_primary_candidates():
            raise ValueError("primary must contain at least one candidate")
        return self

    def resolve_primary_candidate_entries(self) -> list[ResolvedModelCandidate]:
        """Return ordered primary candidates with routing metadata preserved."""
        primary_entries: list[str | ModelEntry]
        if isinstance(self.primary, list):
            primary_entries = self.primary
        else:
            primary_entries = [self.primary]

        return [self._resolve_candidate_entry(entry) for entry in primary_entries]

    def resolve_primary_candidates(self) -> list[tuple[str, str]]:
        """Return ordered list of (model_id, provider_name) primary candidates."""
        return [entry.as_tuple() for entry in self.resolve_primary_candidate_entries()]

    def resolve_primary(self) -> tuple[str, str]:
        """Return first primary candidate for backward compatibility."""
        return self.resolve_primary_candidates()[0]

    def resolve_fallback_candidate_entries(self) -> list[ResolvedModelCandidate]:
        """Return fallback candidates with routing metadata preserved."""
        return [self._resolve_candidate_entry(entry) for entry in self.fallback]

    def resolve_fallbacks(self) -> list[tuple[str, str]]:
        """Return list of (model_id, provider_name) tuples."""
        return [entry.as_tuple() for entry in self.resolve_fallback_candidate_entries()]

    @staticmethod
    def _resolve_candidate_entry(entry: str | ModelEntry) -> ResolvedModelCandidate:
        """Normalize string/object candidate entries without losing metadata."""
        if isinstance(entry, ModelEntry):
            return ResolvedModelCandidate(
                model=entry.model,
                provider=entry.provider,
                max_input_tokens=entry.max_input_tokens,
                async_mode=entry.async_mode,
                pricing=entry.pricing,
            )
        return ResolvedModelCandidate(model=entry)

    def primary_model_id(self) -> str:
        """Return first primary model ID (for backward compat)."""
        model_id, _ = self.resolve_primary()
        return model_id

    def primary_model_ids(self) -> list[str]:
        """Return all primary model IDs."""
        return [model_id for model_id, _ in self.resolve_primary_candidates()]

    def fallback_model_ids(self) -> list[str]:
        """Return just the model ID strings (for backward compat)."""
        result: list[str] = []
        for entry in self.fallback:
            if isinstance(entry, ModelEntry):
                result.append(entry.model)
            else:
                result.append(entry)
        return result


class ProfileConfig(BaseModel):
    """A routing profile (auto, eco, premium, agentic)."""

    tiers: dict[str, TierModelConfig]  # SIMPLE, MEDIUM, COMPLEX, REASONING


class _AuxLLMConfigBase(BaseModel):
    """Common base for auxiliary LLM settings."""

    model: str = "google/gemini-2.5-flash-lite"
    provider: str = ""

    # Disallow direct base_url/api_key storage; they must be resolved by provider.
    model_config = ConfigDict(extra="forbid")


class LLMClassifierConfig(_AuxLLMConfigBase):
    """Configuration for the LLM-as-judge escalation classifier."""


class FeatureAnnotatorConfig(_AuxLLMConfigBase):
    """Configuration for offline feature annotation."""


class EmbeddingConfig(BaseModel):
    """Configuration for embeddings used by training and scoring."""

    enabled: bool = True
    mode: Literal["api", "local", "disabled"] = "api"
    timeout_seconds: float = Field(default=5.0, gt=0.0)
    local_model: str = ""
    model: str = "text-embedding-3-small"
    provider: str = ""
    base_url: str = ""
    api_key: str = ""

    @model_validator(mode="after")
    def _normalize_legacy_enabled(self) -> "EmbeddingConfig":
        """Preserve legacy enabled=false as explicit disabled mode."""
        if not self.enabled:
            self.mode = "disabled"
        if self.mode == "local" and not self.local_model:
            raise ValueError(
                "embedding.local_model is required when embedding.mode=local"
            )
        return self

    @property
    def effective_mode(self) -> Literal["api", "local", "disabled"]:
        """Return normalized embedding mode."""
        if not self.enabled:
            return "disabled"
        return self.mode

    @property
    def effective_model(self) -> str:
        """Return the runtime embedding model identity for diagnostics."""
        return self.local_model if self.effective_mode == "local" else self.model


class FallbackBackoffConfig(BaseModel):
    """Configuration for process-local fallback exponential backoff."""

    enabled: bool = True
    initial_delay_seconds: float = Field(default=5.0, ge=0.0)
    multiplier: float = Field(default=2.0, ge=1.0)
    max_delay_seconds: float = Field(default=300.0, ge=0.0)

    @model_validator(mode="after")
    def _validate_delay_bounds(self) -> "FallbackBackoffConfig":
        """Ensure the max delay is not lower than the initial delay."""
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ValueError(
                "max_delay_seconds must be greater than or equal to initial_delay_seconds"
            )
        return self


class RoutingConfig(BaseModel):
    """Configuration for routing-level settings."""

    session_header: str = "X-Session-Id"


class ImageHistoryStrippingConfig(BaseModel):
    """Opt-in policy aging images out of older conversation turns.

    Vision capability is detected from the whole message history, so a single
    image anywhere in a session pins all subsequent turns to vision-capable
    models.  When this policy is enabled, an image stays vision-relevant for
    ``image_ttl_turns`` user turns after it is sent (the session routes to
    vision-capable models with the full body); once aged out, the image parts
    are stripped from history for non-vision candidates and ``vision`` stops
    being required, so sessions route back to cheaper/larger non-vision
    models (decision doc-13).  Disabled by default: absent or ``enabled:
    false`` preserves current behavior bit-for-bit.
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Opt-in master switch. When false (or the block is absent), "
            "behavior is identical to pre-TASK-17: any image in history "
            "requires vision and nothing is ever stripped."
        ),
    )
    image_ttl_turns: int = Field(
        default=3,
        gt=0,
        description=(
            "User turns an image stays vision-relevant, counted from the "
            "turn it was sent (send turn + this many - 1 follow-ups). "
            "Aging counts user-role messages only; assistant/tool turns in "
            "between do not advance it. While any image is within TTL, "
            "vision is required; once all images are aged out, non-vision "
            "candidates become eligible and receive image-free bodies. The "
            "latest user message is always vision-relevant regardless of "
            "this value."
        ),
    )
    placeholder: str = Field(
        default="[image omitted]",
        description=(
            "Text sent to the model in place of a stripped image part. An "
            "empty string drops the part silently instead."
        ),
    )


class SmartProxyConfig(BaseModel):
    """Smart-proxy feature configuration."""

    tools_capability_detection: Literal["declared", "active"] = Field(
        default="declared",
        description=(
            "Policy for requiring the tools capability from OpenAI tool/function "
            "request fields. 'declared' preserves fail-closed legacy behavior; "
            "'active' ignores decorative schemas unless tool use is explicit or active."
        ),
    )
    decorative_tool_schema_handling: Literal["preserve", "strip"] = Field(
        default="preserve",
        description=(
            "Policy for forwarding decorative top-level tool schemas upstream. "
            "'preserve' keeps OpenAI-compatible payloads unchanged; 'strip' removes "
            "top-level tool schema fields only when tool use is declared but not required."
        ),
    )
    image_history_stripping: ImageHistoryStrippingConfig | None = Field(
        default=None,
        description=(
            "Opt-in image-history aging policy (TASK-17). None or "
            "enabled: false = off. When enabled, images stay vision-relevant "
            "for image_ttl_turns user turns, then are stripped for non-vision "
            "candidates so sessions route back to non-vision models."
        ),
    )
    fallback_backoff: FallbackBackoffConfig = Field(
        default_factory=FallbackBackoffConfig
    )


class ModelRuleEntry(BaseModel):
    """Model rule declaration using prefix matching."""

    prefix: str  # e.g. 'claude-', 'gpt-4', 'google/gemini'
    provider: str = ""  # optional provider filter; empty matches any provider
    capabilities: list[str] = Field(
        default_factory=list
    )  # e.g. ['vision', 'tools', 'json_mode']
    reasoning_style: (
        Literal["openai", "xai", "anthropic", "dashscope", "gemini", "none"] | None
    ) = None
    supports_reasoning_content: bool | None = None
    content_part_policy: ContentPartPolicy | None = None
    async_mode: AsyncModeConfig | None = Field(
        default=None,
        description=(
            "Rule-level async/batch opt-in or mechanism override for matching "
            "model/provider prefixes.  Set enabled: true to opt in (mechanism "
            "inherited from provider); set enabled: false to explicitly opt out. "
            "Mechanism fields override the provider's mechanism.  Provider-specific "
            "rules outrank provider-agnostic rules before prefix specificity."
        ),
    )
    extra_body: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional extra request-body fields injected for any candidate "
            "matching this rule (e.g. {'service_tier': 'flex'}). Merged last, "
            "so these values win over client-provided fields."
        ),
    )
    cache_control: CacheControlConfig | None = Field(
        default=None,
        description=(
            "Rule-level opt-in prompt-caching marker injection override.  "
            "Presence-based: a matching rule's cache_control wins over the "
            "provider-level block without field-by-field merge (decision doc-7)."
        ),
    )
    pricing: ModelPricingConfig | None = Field(
        default=None,
        description=(
            "Optional per-model pricing metadata (USD per 1M tokens) matched "
            "by rule prefix and used by the dashboard to estimate prompt-cache "
            "savings.  Display-only."
        ),
    )


ModelCapabilityEntry = ModelRuleEntry


def _resolve_provider_for_aux_llm(
    *,
    aux_cfg: LLMClassifierConfig
    | FeatureAnnotatorConfig
    | EmbeddingConfig
    | None = None,
    providers: dict[str, ProviderConfig] | None = None,
    aux_key: str,
    default_provider: str,
) -> tuple[str, str]:
    """Resolve base_url/api_key for a classifier/annotator/embedding config via provider."""

    resolved_provider = (
        aux_cfg.provider if aux_cfg and aux_cfg.provider else default_provider
    )
    if providers is None:
        providers = {}

    provider_cfg = providers.get(resolved_provider)
    if provider_cfg is None:
        raise ValueError(
            f"Unknown provider '{resolved_provider}' for {aux_key}; check config or default_provider"
        )

    return provider_cfg.base_url, resolve_env(provider_cfg.api_key)


class OptiproxaiConfig(BaseModel):
    """Top-level OptiProxAI configuration."""

    host: str = "0.0.0.0"
    port: int = 18420
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    default_provider: str = "openrouter"
    profiles: dict[str, ProfileConfig] = Field(default_factory=dict)
    default_profile: str = "auto"
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    llm_classifier: LLMClassifierConfig | None = None
    feature_annotator: FeatureAnnotatorConfig | None = None
    embedding: EmbeddingConfig | None = None
    smart_proxy: SmartProxyConfig = Field(default_factory=SmartProxyConfig)
    model_rules: list[ModelRuleEntry] = Field(default_factory=list)
    model_capabilities: list[ModelRuleEntry] = Field(default_factory=list)
    disable_axis_overrides: bool = False
    # Per-boundary ambiguity handling passed to the scorer. Keys are the lower
    # tier of each boundary pair (SIMPLE, MEDIUM, COMPLEX); values are
    # {"band": <float>, "fallback": "<TIER>"}. See scorer.ScoringConfig.
    ambiguous_bands: dict[str, dict[str, float | str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalize_legacy_model_capabilities(self) -> "OptiproxaiConfig":
        """Use legacy model_capabilities as model_rules when model_rules is unset."""
        if self.model_rules and self.model_capabilities:
            raise ValueError(
                "Specify either model_rules or legacy model_capabilities, not both"
            )
        if self.model_capabilities:
            self.model_rules = self.model_capabilities
        return self

    @model_validator(mode="after")
    def _validate_aux_llm_provider_resolution(self) -> "OptiproxaiConfig":
        """Ensure auxiliary LLM configs resolve to known providers."""

        if self.llm_classifier is not None:
            _resolve_provider_for_aux_llm(
                aux_cfg=self.llm_classifier,
                providers=self.providers,
                aux_key="llm_classifier",
                default_provider=self.default_provider,
            )

        if self.feature_annotator is not None:
            _resolve_provider_for_aux_llm(
                aux_cfg=self.feature_annotator,
                providers=self.providers,
                aux_key="feature_annotator",
                default_provider=self.default_provider,
            )

        if (
            self.embedding is not None
            and self.embedding.effective_mode == "api"
            and self.embedding.provider
        ):
            _resolve_provider_for_aux_llm(
                aux_cfg=self.embedding,
                providers=self.providers,
                aux_key="embedding",
                default_provider=self.default_provider,
            )

        return self

    @model_validator(mode="after")
    def _validate_ambiguous_bands(self) -> "OptiproxaiConfig":
        """Fail at load time on invalid ambiguous_bands instead of silently
        degrading every request to the conservative default tier."""
        _AMBIGUOUS_BAND_KEYS = ("SIMPLE_MEDIUM", "MEDIUM_COMPLEX", "COMPLEX_REASONING")
        for boundary_name, spec in self.ambiguous_bands.items():
            if not isinstance(spec, dict) or boundary_name not in _AMBIGUOUS_BAND_KEYS:
                raise ValueError(
                    f"ambiguous_bands entries must be dicts keyed by "
                    f"SIMPLE_MEDIUM/MEDIUM_COMPLEX/COMPLEX_REASONING; got {boundary_name!r}"
                )
            try:
                band = float(spec.get("band", 0.0))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"ambiguous_bands[{boundary_name}].band must be numeric"
                ) from exc
            if band < 0:
                raise ValueError(
                    f"ambiguous_bands[{boundary_name}].band must be >= 0; got {band}"
                )
            prefer_name = str(spec.get("prefer", "")).strip().upper()
            if prefer_name not in ("LOWER", "UPPER"):
                raise ValueError(
                    f"ambiguous_bands[{boundary_name}].prefer must be 'LOWER' or 'UPPER'; got {prefer_name!r}"
                )
        return self

    def llm_classifier_resolved(self) -> tuple[str, str] | None:
        """Return (base_url, api_key) resolved from llm_classifier.provider/default_provider."""

        if self.llm_classifier is None:
            return None
        return _resolve_provider_for_aux_llm(
            aux_cfg=self.llm_classifier,
            providers=self.providers,
            aux_key="llm_classifier",
            default_provider=self.default_provider,
        )

    def feature_annotator_resolved(self) -> tuple[str, str] | None:
        """Return (base_url, api_key) resolved from feature_annotator.provider/default_provider."""

        if self.feature_annotator is None:
            return None
        return _resolve_provider_for_aux_llm(
            aux_cfg=self.feature_annotator,
            providers=self.providers,
            aux_key="feature_annotator",
            default_provider=self.default_provider,
        )

    def embedding_resolved(self) -> tuple[str, str] | None:
        """Return (base_url, api_key) resolved from embedding.provider/default_provider."""

        if self.embedding is None or self.embedding.effective_mode != "api":
            return None
        if self.embedding.base_url:
            return self.embedding.base_url, self.embedding.api_key
        if self.embedding.provider:
            return _resolve_provider_for_aux_llm(
                aux_cfg=self.embedding,
                providers=self.providers,
                aux_key="embedding",
                default_provider=self.default_provider,
            )
        return None


# ---------------------------------------------------------------------------
# Env-var resolution
# ---------------------------------------------------------------------------

_ENV_RE = re.compile(r"\$\{([^}]+)\}")


def resolve_env(value: str) -> str:
    """Replace ${VAR} placeholders with environment variable values."""

    def _replace(m: re.Match) -> str:
        var = m.group(1)
        return os.environ.get(var, "")

    return _ENV_RE.sub(_replace, value)


def resolve_env_recursive(obj: Any) -> Any:
    """Walk a data structure and resolve all ${VAR} strings."""
    if isinstance(obj, str):
        return resolve_env(obj)
    if isinstance(obj, dict):
        return {k: resolve_env_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_env_recursive(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _default_config_paths() -> list[Path]:
    """Return ordered list of config file search paths.

    Priority: ./config.yaml → $XDG_CONFIG_HOME/optiproxai/config.yaml → /etc/optiproxai/config.yaml
    """
    from optiproxai.dirs import config_dir

    return [
        Path("config.yaml"),
        Path("config.yml"),
        config_dir() / "config.yaml",
        Path("/etc/optiproxai/config.yaml"),
    ]


def _find_config_file(explicit_path: str | Path | None = None) -> Path | None:
    """Locate a config file, checking explicit path then defaults."""
    if explicit_path is not None:
        p = Path(explicit_path).expanduser()
        return p if p.is_file() else None

    # Check env var
    env_path = os.environ.get("OPTIPROXAI_CONFIG")
    if env_path:
        p = Path(env_path).expanduser()
        if p.is_file():
            return p

    # Search default locations (XDG-aware)
    for candidate in _default_config_paths():
        if candidate.is_file():
            return candidate

    return None


def load_config(
    path: str | Path | None = None,
    *,
    overrides: dict[str, Any] | None = None,
    strict: bool = False,
) -> OptiproxaiConfig:
    """Load OptiproxaiConfig from a YAML file with env-var resolution.

    Args:
        path: Explicit path to config YAML, or None to auto-discover.
        overrides: Dict of overrides merged on top of file config.
        strict: If True, raise ConfigNotFoundError / ConfigIncompleteError
                when the config is missing or incomplete. Default False
                preserves backward compatibility (returns empty defaults).

    Returns:
        Fully resolved OptiproxaiConfig instance.
    """
    raw: dict[str, Any] = {}

    config_file = _find_config_file(path)

    if config_file is not None:
        with open(config_file, encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                raw = loaded
    elif strict:
        # Explicit path given but not found
        if path is not None:
            raise ConfigNotFoundError([Path(path).expanduser()])
        # Auto-discovery failed
        raise ConfigNotFoundError(_default_config_paths())

    # Merge overrides
    if overrides:
        raw = _deep_merge(raw, overrides)

    # Normalize nullable tier fallbacks before validation
    raw = _normalize_tier_fallback_null(raw)

    # Resolve env vars in raw data
    raw = resolve_env_recursive(raw)

    cfg = OptiproxaiConfig.model_validate(raw)

    if strict and not cfg.profiles:
        raise ConfigIncompleteError("profiles", config_file)

    return cfg


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base* (override wins)."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _normalize_tier_fallback_null(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize profiles.*.tiers.*.fallback null values to empty lists."""
    normalized = dict(raw)
    profiles = normalized.get("profiles")
    if not isinstance(profiles, dict):
        return normalized

    normalized_profiles: dict[str, Any] = dict(profiles)
    for profile_name, profile_value in profiles.items():
        if not isinstance(profile_value, dict):
            continue
        tiers = profile_value.get("tiers")
        if not isinstance(tiers, dict):
            continue

        normalized_tiers: dict[str, Any] = dict(tiers)
        for tier_name, tier_value in tiers.items():
            if not isinstance(tier_value, dict):
                continue
            if "fallback" in tier_value and tier_value["fallback"] is None:
                normalized_tier = dict(tier_value)
                normalized_tier["fallback"] = []
                normalized_tiers[tier_name] = normalized_tier

        normalized_profile = dict(profile_value)
        normalized_profile["tiers"] = normalized_tiers
        normalized_profiles[profile_name] = normalized_profile

    normalized["profiles"] = normalized_profiles
    return normalized
