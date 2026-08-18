"""OptiProxAI smart router – maps incoming messages to the best model+provider."""

from __future__ import annotations

import hashlib
import logging
import re
from threading import Lock
from typing import Any

from pydantic import BaseModel, Field

from optiproxai.classification_context import (
    ClassificationInput,
    build_classification_input,
)
from optiproxai.config import (
    OptiproxaiConfig,
    ProviderConfig,
    ResolvedModelCandidate,
    resolve_env,
)
from optiproxai.fallback_backoff import FallbackBackoffState
from optiproxai.tokens import _estimate_tokens

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class CapabilityNotSatisfiedError(Exception):
    """Raised when no model with required capabilities is available."""

    def __init__(self, required_capabilities: set[str]) -> None:
        self.required_capabilities = required_capabilities
        caps_str = ", ".join(sorted(required_capabilities))
        super().__init__(
            f"No available model supports required capabilities: {caps_str}"
        )


class InputLimitNotSatisfiedError(Exception):
    """Raised when no candidate can safely accept the estimated prompt tokens."""

    def __init__(self, prompt_tokens: int, profile: str, tier: str) -> None:
        self.prompt_tokens = prompt_tokens
        self.profile = profile
        self.tier = tier
        super().__init__(
            "No input-limit-eligible model candidate is available "
            f"for profile '{profile}' tier '{tier}' with estimated prompt tokens "
            f"{prompt_tokens}."
        )


# ---------------------------------------------------------------------------
# Routing result
# ---------------------------------------------------------------------------


class FallbackEntry(BaseModel):
    """A fallback model with provider connection info."""

    model: str
    provider: str
    base_url: str
    api_key: str = ""
    max_input_tokens: int | None = None


class RoutingDecision(BaseModel):
    """The outcome of a routing decision."""

    model: str
    provider: str
    base_url: str
    api_key: str = ""
    tier: str
    score: float
    confidence: float
    signals: list[str] = Field(default_factory=list)
    agentic_score: float = 0.0
    profile: str | None = None
    fallbacks: list[FallbackEntry] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    reasoning_effort: str | None = None  # tier-level reasoning effort override


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

# Default tier when the scorer can't decide
_DEFAULT_TIER = "MEDIUM"

# Ordered tiers from simplest to most complex
_TIER_ORDER = ["SIMPLE", "MEDIUM", "COMPLEX", "REASONING"]


def _session_hash(session_key: str) -> int:
    """Deterministic hash for session-sticky candidate selection."""
    return int.from_bytes(hashlib.sha256(session_key.encode()).digest()[:8], "big")


# Per-turn tier override token, e.g. "/optiproxai:reasoning" (decision record doc-2)
_TIER_OVERRIDE_PATTERN = re.compile(r"^/optiproxai:(\w+)\s*")


def parse_tier_override(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Extract and strip a tier override token from the latest user message.

    Only the latest user message is scanned (history, assistant, and system
    messages are ignored). The token must be at position 0 of the content;
    for list content, only the first ``{"type": "text", "text": ...}`` part
    is checked. Tier names are matched case-insensitively against
    ``_TIER_ORDER``.

    Returns:
        ``(tier_override, stripped_messages)`` where ``tier_override`` is the
        upper-cased tier name for a valid token, or ``None`` for an invalid or
        absent token. When a token is found (valid or not), the returned
        message list is a shallow copy in which only the latest user message
        dict is deep-copied with the token and leading whitespace removed.
        When no token is found, the original list is returned unchanged.
    """
    for idx in range(len(messages) - 1, -1, -1):
        message = messages[idx]
        if message.get("role") != "user":
            continue

        content = message.get("content")
        new_message: dict[str, Any] | None = None
        tier_name: str | None = None

        if isinstance(content, str):
            match = _TIER_OVERRIDE_PATTERN.match(content)
            if match is not None:
                tier_name = match.group(1)
                new_message = dict(message)
                new_message["content"] = content[match.end() :]
        elif isinstance(content, list):
            # Only the first text part is eligible (decision record doc-2)
            for part_idx, part in enumerate(content):
                if not (isinstance(part, dict) and part.get("type") == "text"):
                    continue
                text = part.get("text")
                if isinstance(text, str):
                    match = _TIER_OVERRIDE_PATTERN.match(text)
                    if match is not None:
                        tier_name = match.group(1)
                        new_content = list(content)
                        new_part = dict(part)
                        new_part["text"] = text[match.end() :]
                        new_content[part_idx] = new_part
                        new_message = dict(message)
                        new_message["content"] = new_content
                break

        if tier_name is None or new_message is None:
            return None, messages

        stripped_messages = list(messages)
        stripped_messages[idx] = new_message

        tier_override = tier_name.upper()
        if tier_override not in _TIER_ORDER:
            log.warning(
                "Invalid tier override %r in latest user message; "
                "token stripped, falling back to normal scoring",
                tier_name,
            )
            return None, stripped_messages
        return tier_override, stripped_messages

    return None, messages


class Router:
    """Given chat messages, decides which model and provider to use."""

    def __init__(
        self,
        config: OptiproxaiConfig,
        *,
        fallback_backoff_state: FallbackBackoffState | None = None,
    ) -> None:
        self.config = config
        self._rr_state: dict[tuple[str, str], int] = {}
        self._rr_lock = Lock()
        self.fallback_backoff_state = fallback_backoff_state or FallbackBackoffState(
            config.smart_proxy.fallback_backoff
        )
        # Persistent scorer so feature_classifier.pkl is loaded at most once per
        # Router lifetime (a new Router is constructed on config reload).
        self._scorer: Any | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(
        self,
        messages: list[dict[str, Any]],
        *,
        profile: str | None = None,
        model: str | None = None,
        required_capabilities: set[str] | None = None,
        session_key: str | None = None,
        tier_override: str | None = None,
    ) -> RoutingDecision:
        """Route a chat request to the right model+provider.

        Args:
            messages: OpenAI-style message list.
            profile: Explicit profile name (auto/eco/premium/agentic).
            model: If set, may contain 'optiproxai/<profile>' to select a profile,
                   or an explicit model ID to pass through.
            required_capabilities: Set of required capabilities (e.g., {'vision', 'tools', 'json_mode'}).
            session_key: Optional session key for session-sticky primary selection.
            tier_override: If set to a valid tier (SIMPLE/MEDIUM/COMPLEX/REASONING,
                   case-insensitive), skips the scorer and pins the tier.

        Returns:
            A RoutingDecision with all the info needed to proxy the request.

        Raises:
            CapabilityNotSatisfiedError: When no model with required capabilities is available.
        """
        if required_capabilities is None:
            required_capabilities = set()
        # --- Resolve profile from model string if needed ---
        profile = self._resolve_profile(profile, model)

        profile_cfg = self.config.profiles.get(profile)
        if profile_cfg is None:
            log.warning(
                "Profile %r not found, falling back to %r",
                profile,
                self.config.default_profile,
            )
            profile = self.config.default_profile
            profile_cfg = self.config.profiles.get(profile)

        if profile_cfg is None:
            # Absolute fallback – shouldn't happen with a valid config
            raise ValueError(
                f"No profile configuration found for '{profile}' "
                f"and default profile '{self.config.default_profile}' is also missing."
            )

        # --- Build classification input from conversation context ---
        classification_input = build_classification_input(messages)

        # --- Resolve tier: valid override pins the tier and skips the scorer ---
        score: float
        confidence: float
        signals: list[str]
        signal_details: dict[str, Any] | list[str]
        agentic_score: float

        if tier_override is not None and tier_override.upper() in _TIER_ORDER:
            tier = tier_override.upper()
            score = 1.0
            confidence = 1.0
            signals = ["tier_override"]
            signal_details = list(signals)
            agentic_score = 0.0
        else:
            if tier_override is not None:
                log.warning(
                    "Invalid tier_override %r, falling back to normal scoring",
                    tier_override,
                )

            # --- Run scorer ---
            classification = self._classify(
                classification_input=classification_input,
                messages=messages,
                profile=profile,
            )

            tier = str(classification.get("tier") or _DEFAULT_TIER)
            if tier not in _TIER_ORDER:
                log.warning(
                    "Invalid scorer tier %r, falling back to %s", tier, _DEFAULT_TIER
                )
                tier = _DEFAULT_TIER
            score = self._coerce_probability(classification.get("score", 0.5), 0.5)
            confidence = self._coerce_probability(
                classification.get("confidence", 0.5), 0.5
            )
            raw_signals = classification.get("signals", [])
            signals = (
                [str(signal) for signal in raw_signals]
                if isinstance(raw_signals, list)
                else []
            )
            raw_signal_details = classification.get("signal_details", signals)
            signal_details = (
                raw_signal_details
                if isinstance(raw_signal_details, dict | list)
                else signals
            )
            agentic_score = self._coerce_probability(
                classification.get("agentic_score", 0.0), 0.0
            )

            # --- Override tier for agentic profile if agentic_score is high ---
            if profile == "agentic" and agentic_score > 0.6 and tier == "SIMPLE":
                tier = "MEDIUM"

        # --- Look up model in profile tier config with capability/input-limit filtering ---
        resolved_tier, tier_cfg = self._resolve_tier_config(profile_cfg, tier)
        prompt_tokens = _estimate_tokens(messages)
        log.debug(
            "Estimated prompt tokens for routing profile=%s tier=%s tokens=%d",
            profile,
            resolved_tier,
            prompt_tokens,
        )

        primary_capable_candidates = self._capable_primary_candidates(
            tier_cfg,
            required_capabilities,
        )
        fallback_capable_candidates = self._capable_fallback_candidates(
            tier_cfg,
            required_capabilities,
        )
        any_capable_candidates = bool(
            primary_capable_candidates or fallback_capable_candidates
        )
        primary_candidates = self._filter_input_limit_candidates(
            primary_capable_candidates,
            prompt_tokens=prompt_tokens,
            tier_provider=tier_cfg.provider,
        )
        fallback_candidates = self._filter_input_limit_candidates(
            fallback_capable_candidates,
            prompt_tokens=prompt_tokens,
            tier_provider=tier_cfg.provider,
        )

        if required_capabilities and not primary_candidates and not fallback_candidates:
            current_tier = resolved_tier
            for tier_name in self._escalation_path(profile_cfg, current_tier):
                escalated_cfg = profile_cfg.tiers.get(tier_name)
                if escalated_cfg is None:
                    continue
                escalated_primary_capable = self._capable_primary_candidates(
                    escalated_cfg,
                    required_capabilities,
                )
                escalated_fallbacks_capable = self._capable_fallback_candidates(
                    escalated_cfg,
                    required_capabilities,
                )
                any_capable_candidates = any_capable_candidates or bool(
                    escalated_primary_capable or escalated_fallbacks_capable
                )
                escalated_primary = self._filter_input_limit_candidates(
                    escalated_primary_capable,
                    prompt_tokens=prompt_tokens,
                    tier_provider=escalated_cfg.provider,
                )
                escalated_fallbacks = self._filter_input_limit_candidates(
                    escalated_fallbacks_capable,
                    prompt_tokens=prompt_tokens,
                    tier_provider=escalated_cfg.provider,
                )
                if escalated_primary or escalated_fallbacks:
                    resolved_tier = tier_name
                    tier_cfg = escalated_cfg
                    primary_candidates = escalated_primary
                    fallback_candidates = escalated_fallbacks
                    break

        if (
            not primary_candidates
            and not fallback_candidates
            and required_capabilities
            and not any_capable_candidates
        ):
            raise CapabilityNotSatisfiedError(required_capabilities)

        cooled_primary_candidates = self._filter_cooled_candidates(
            primary_candidates,
            tier_provider=tier_cfg.provider,
        )
        cooled_fallback_candidates = self._filter_cooled_candidates(
            fallback_candidates,
            tier_provider=tier_cfg.provider,
        )

        promoted_from_fallback = False
        if cooled_primary_candidates:
            selection_candidates = cooled_primary_candidates
        elif fallback_candidates and cooled_fallback_candidates:
            promoted_from_fallback = True
            selection_candidates = cooled_fallback_candidates
            log.warning(
                "No available primary candidates; promoting fallback candidate profile=%s tier=%s fallback_count=%d",
                profile,
                resolved_tier,
                len(selection_candidates),
            )
        elif primary_candidates:
            selection_candidates = primary_candidates
            log.warning(
                "All primary candidates cooling down; ignoring cooldown profile=%s tier=%s",
                profile,
                resolved_tier,
            )
        elif fallback_candidates:
            promoted_from_fallback = True
            selection_candidates = fallback_candidates
            log.warning(
                "All input-limit-eligible fallback candidates cooling down; ignoring cooldown profile=%s tier=%s",
                profile,
                resolved_tier,
            )
        else:
            log.warning(
                "No input-limit-eligible candidates found profile=%s tier=%s prompt_tokens=%d",
                profile,
                resolved_tier,
                prompt_tokens,
            )
            raise InputLimitNotSatisfiedError(prompt_tokens, profile, resolved_tier)

        # --- Resolve primary model and provider ---
        primary_candidate = self._select_primary_candidate(
            profile,
            resolved_tier,
            tier_cfg,
            filter_to_candidates=selection_candidates,
            session_key=session_key,
        )
        model_id = primary_candidate.model

        # Resolve provider name: entry override > tier default > config default
        provider_name = self._resolve_provider_name(
            primary_candidate.provider,
            tier_cfg.provider,
        )

        provider_cfg = self._lookup_provider(provider_name)

        # --- Build fallback entries with capability filtering ---
        fallback_entries: list[FallbackEntry] = []

        for fallback_candidate in fallback_candidates:
            fb_provider_name = self._resolve_provider_name(
                fallback_candidate.provider,
                tier_cfg.provider,
            )
            if promoted_from_fallback and (
                fallback_candidate.model == model_id
                and fb_provider_name == provider_name
            ):
                continue
            fb_provider_cfg = self._lookup_provider(fb_provider_name)
            fallback_entries.append(
                FallbackEntry(
                    model=fallback_candidate.model,
                    provider=fb_provider_name,
                    base_url=fb_provider_cfg.base_url,
                    api_key=resolve_env(fb_provider_cfg.api_key),
                    max_input_tokens=fallback_candidate.max_input_tokens,
                )
            )

        try:
            from optiproxai.logger import RoutingLogger

            RoutingLogger.log_decision(
                classification_input.text,
                tier=tier,
                score=score,
                confidence=confidence,
                signals=signal_details,
                agentic_score=agentic_score,
                model=model_id,
                provider=provider_name,
                profile=profile,
                context=classification_input.__dict__,
            )
        except Exception:
            log.exception("Failed to persist routing decision log")

        return RoutingDecision(
            model=model_id,
            provider=provider_name,
            base_url=provider_cfg.base_url,
            api_key=resolve_env(provider_cfg.api_key),
            tier=tier,
            score=score,
            confidence=confidence,
            signals=signals,
            agentic_score=agentic_score,
            profile=profile,
            fallbacks=fallback_entries,
            required_capabilities=sorted(list(required_capabilities)),
            reasoning_effort=tier_cfg.reasoning_effort,
        )

    def resolve_model(
        self,
        *,
        profile: str | None = None,
        tier: str = "SIMPLE",
    ) -> RoutingDecision:
        """Resolve a model for internal use without running scorer or logging.

        Resolves via the Router's profile/tier resolution path, skipping scorer
        classification and RoutingLogger so internal resolution is not polluted
        with routing logs.

        Args:
            profile: Profile name, or None to use default_profile.
            tier: Tier name (SIMPLE, MEDIUM, COMPLEX, REASONING).

        Returns:
            A RoutingDecision with resolved model, base_url, api_key, provider,
            and fallbacks. score/confidence/signals are zero/empty as they are
            not applicable for internal resolution.
        """
        resolved_profile = profile or self.config.default_profile

        profile_cfg = self.config.profiles.get(resolved_profile)
        if profile_cfg is None:
            log.warning(
                "Profile %r not found, falling back to %r",
                resolved_profile,
                self.config.default_profile,
            )
            resolved_profile = self.config.default_profile
            profile_cfg = self.config.profiles.get(resolved_profile)

        if profile_cfg is None:
            raise ValueError(
                f"No profile configuration found for '{resolved_profile}' "
                f"and default profile '{self.config.default_profile}' is also missing."
            )

        resolved_tier, tier_cfg = self._resolve_tier_config(profile_cfg, tier)

        primary_candidate = self._select_primary_candidate(
            resolved_profile,
            resolved_tier,
            tier_cfg,
        )
        primary_model = primary_candidate.model
        provider_name = self._resolve_provider_name(
            primary_candidate.provider,
            tier_cfg.provider,
        )
        provider_cfg = self._lookup_provider(provider_name)

        fallback_entries: list[FallbackEntry] = []
        for fallback_candidate in tier_cfg.resolve_fallback_candidate_entries():
            fb_provider_name = self._resolve_provider_name(
                fallback_candidate.provider, tier_cfg.provider
            )
            fb_provider_cfg = self._lookup_provider(fb_provider_name)
            fallback_entries.append(
                FallbackEntry(
                    model=fallback_candidate.model,
                    provider=fb_provider_name,
                    base_url=fb_provider_cfg.base_url,
                    api_key=resolve_env(fb_provider_cfg.api_key),
                    max_input_tokens=fallback_candidate.max_input_tokens,
                )
            )

        return RoutingDecision(
            model=primary_model,
            provider=provider_name,
            base_url=provider_cfg.base_url,
            api_key=resolve_env(provider_cfg.api_key),
            tier=resolved_tier,
            score=0.0,
            confidence=0.0,
            signals=[],
            agentic_score=0.0,
            profile=resolved_profile,
            fallbacks=fallback_entries,
            reasoning_effort=tier_cfg.reasoning_effort,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_model_capabilities(
        self, model_id: str, provider_name: str = ""
    ) -> set[str]:
        """Get capabilities for a model using prefix/provider matching from config.

        Returns:
            Set of capability strings (e.g., {'vision', 'tools', 'json_mode'}).
            Empty set if model not found in config.
        """
        capabilities: set[str] = set()
        for entry in self.config.model_rules:
            prefix_matches = entry.prefix == "*" or model_id.startswith(entry.prefix)
            if not prefix_matches:
                continue
            if entry.provider and entry.provider != provider_name:
                continue
            capabilities.update(entry.capabilities)
        return capabilities

    def _capable_primary_candidates(
        self,
        tier_cfg: Any,
        required_capabilities: set[str],
    ) -> list[ResolvedModelCandidate]:
        """Return primary candidates that satisfy capability metadata."""
        return self._filter_capable_candidates(
            tier_cfg.resolve_primary_candidate_entries(),
            required_capabilities,
            tier_provider=tier_cfg.provider,
        )

    def _capable_fallback_candidates(
        self,
        tier_cfg: Any,
        required_capabilities: set[str],
    ) -> list[ResolvedModelCandidate]:
        """Return fallback candidates that satisfy capability metadata."""
        return self._filter_capable_candidates(
            tier_cfg.resolve_fallback_candidate_entries(),
            required_capabilities,
            tier_provider=tier_cfg.provider,
        )

    def _filter_capable_candidates(
        self,
        candidates: list[ResolvedModelCandidate] | list[tuple[str, str]],
        required_capabilities: set[str],
        *,
        tier_provider: str = "default",
    ) -> list[ResolvedModelCandidate]:
        """Filter candidates to those that have all required capabilities.

        Args:
            candidates: List of normalized model candidates.
            required_capabilities: Set of required capability strings.

        Returns:
            Filtered list of candidates with all required capabilities.
            Returns all candidates if no capabilities are required.
        """
        normalized_candidates = self._normalize_candidates(candidates)
        if not required_capabilities:
            return normalized_candidates

        capable = []
        for candidate in normalized_candidates:
            resolved_provider = self._resolve_provider_name(
                candidate.provider,
                tier_provider,
            )
            model_caps = self._get_model_capabilities(
                candidate.model, resolved_provider
            )
            if required_capabilities.issubset(model_caps):
                capable.append(candidate)

        return capable

    @staticmethod
    def _coerce_probability(value: Any, default: float) -> float:
        try:
            probability = float(value)
        except (TypeError, ValueError):
            return default
        return min(max(probability, 0.0), 1.0)

    @staticmethod
    def _normalize_candidates(
        candidates: list[ResolvedModelCandidate] | list[tuple[str, str]],
    ) -> list[ResolvedModelCandidate]:
        """Normalize legacy tuple candidates for private helper compatibility."""
        normalized: list[ResolvedModelCandidate] = []
        for candidate in candidates:
            if isinstance(candidate, ResolvedModelCandidate):
                normalized.append(candidate)
            else:
                model_id, provider_name = candidate
                normalized.append(
                    ResolvedModelCandidate(model=model_id, provider=provider_name)
                )
        return normalized

    def _filter_input_limit_candidates(
        self,
        candidates: list[ResolvedModelCandidate],
        *,
        prompt_tokens: int,
        tier_provider: str,
    ) -> list[ResolvedModelCandidate]:
        """Filter out candidates with known insufficient input-token limits."""
        eligible: list[ResolvedModelCandidate] = []
        for candidate in candidates:
            max_input_tokens = candidate.max_input_tokens
            if max_input_tokens is not None and prompt_tokens > max_input_tokens:
                resolved_provider = self._resolve_provider_name(
                    candidate.provider,
                    tier_provider,
                )
                log.info(
                    "Skipping input-limit-ineligible candidate model=%s provider=%s prompt_tokens=%d max_input_tokens=%d",
                    candidate.model,
                    resolved_provider,
                    prompt_tokens,
                    max_input_tokens,
                )
                continue
            eligible.append(candidate)
        return eligible

    def _filter_cooled_candidates(
        self,
        candidates: list[ResolvedModelCandidate],
        *,
        tier_provider: str,
    ) -> list[ResolvedModelCandidate]:
        """Filter out model/provider pairs that are currently in cooldown."""
        if not self.fallback_backoff_state.enabled:
            return candidates

        available: list[ResolvedModelCandidate] = []
        for candidate in candidates:
            resolved_provider = self._resolve_provider_name(
                candidate.provider,
                tier_provider,
            )
            if self.fallback_backoff_state.is_in_cooldown(
                candidate.model,
                resolved_provider,
            ):
                logger = log
                logger.info(
                    "Skipping cooled candidate during routing model=%s provider=%s",
                    candidate.model,
                    resolved_provider,
                )
                continue
            available.append(candidate)
        return available

    def _resolve_tier_config(self, profile_cfg: Any, tier: str) -> tuple[str, Any]:
        """Resolve a tier config, falling back to adjacent tiers if needed."""
        tier_cfg = profile_cfg.tiers.get(tier)
        if tier_cfg is not None:
            return tier, tier_cfg

        fallback_tier = self._fallback_tier_name(profile_cfg, tier)
        if fallback_tier is None:
            raise ValueError(f"No tier config for '{tier}'")
        return fallback_tier, profile_cfg.tiers[fallback_tier]

    def _escalation_path(self, profile_cfg: Any, current_tier: str) -> list[str]:
        """Generate escalation path from current tier to higher tiers.

        Searches upward in _TIER_ORDER, skipping the current tier.
        """
        try:
            idx = _TIER_ORDER.index(current_tier)
        except ValueError:
            idx = 1  # MEDIUM

        path = []
        for offset in range(1, len(_TIER_ORDER)):
            candidate_idx = idx + offset
            if 0 <= candidate_idx < len(_TIER_ORDER):
                candidate = _TIER_ORDER[candidate_idx]
                if candidate in profile_cfg.tiers:
                    path.append(candidate)
        return path

    def _select_primary_candidate(
        self,
        profile: str,
        tier: str,
        tier_cfg: Any,
        filter_to_candidates: list[ResolvedModelCandidate] | None = None,
        session_key: str | None = None,
    ) -> ResolvedModelCandidate:
        """Select a primary candidate via per profile+tier round-robin or session-sticky hash.

        Args:
            profile: Profile name.
            tier: Tier name.
            tier_cfg: Tier config.
            filter_to_candidates: If provided, select only from this list.
                                 Otherwise use all primary candidates.
            session_key: Optional session key for session-sticky selection.

        Returns:
            Selected normalized model candidate.
        """
        if filter_to_candidates is not None:
            candidates = filter_to_candidates
        else:
            candidates = tier_cfg.resolve_primary_candidate_entries()

        if len(candidates) == 1:
            return candidates[0]

        # Session-sticky: deterministic selection by session key hash
        if session_key is not None and tier_cfg.primary_selection == "session_sticky":
            selected_idx = _session_hash(session_key) % len(candidates)
            selected = candidates[selected_idx]
            log.debug(
                "Primary session-sticky selected profile=%s tier=%s index=%d/%d model=%s provider=%s session_key=%s",
                profile,
                tier,
                selected_idx,
                len(candidates),
                selected.model,
                selected.provider or "",
                session_key[:8] + "...",
            )
            return selected

        # Round-robin (default / fallback when no session key)
        state_key = (profile, tier)
        with self._rr_lock:
            next_idx = self._rr_state.get(state_key, 0)
            selected_idx = next_idx % len(candidates)
            self._rr_state[state_key] = (selected_idx + 1) % len(candidates)

        selected = candidates[selected_idx]
        log.debug(
            "Primary round-robin selected profile=%s tier=%s index=%d/%d model=%s provider=%s",
            profile,
            tier,
            selected_idx,
            len(candidates),
            selected.model,
            selected.provider or "",
        )
        return selected

    def _resolve_provider_name(self, entry_provider: str, tier_provider: str) -> str:
        """Resolve provider name: entry override > tier default > config default."""
        if entry_provider:
            return entry_provider
        if tier_provider and tier_provider != "default":
            return tier_provider
        return self.config.default_provider

    def _lookup_provider(self, provider_name: str) -> ProviderConfig:
        """Look up a ProviderConfig by name."""
        provider_cfg = self.config.providers.get(provider_name)
        if provider_cfg is None:
            raise ValueError(f"Provider '{provider_name}' not found in config")
        return provider_cfg

    def _resolve_profile(self, profile: str | None, model: str | None) -> str:
        """Determine the profile name from explicit arg or model string."""
        if profile:
            return profile

        if model and model.startswith("optiproxai/"):
            return model.removeprefix("optiproxai/")

        return self.config.default_profile

    def _classify(
        self,
        classification_input: ClassificationInput,
        messages: list[dict[str, Any]],
        *,
        profile: str,
    ) -> dict[str, Any]:
        """Run the scorer to classify the prompt complexity.

        Returns a dict with keys: score, tier, confidence, signals, agentic_score.
        Falls back conservatively if the scorer module isn't available.
        """
        del messages

        try:
            from optiproxai.scorer import Scorer, ScoringConfig

            if self._scorer is None:
                self._scorer = Scorer(
                    ScoringConfig(
                        disable_axis_overrides=self.config.disable_axis_overrides,
                        ambiguous_bands=self.config.ambiguous_bands,
                    ),
                    enable_routing_log=False,
                )
            result = self._scorer.classify(classification_input.text)
            tier_val = result.tier
            if hasattr(tier_val, "value"):
                tier_val = tier_val.value
            signal_details = result.signals
            signals = signal_details
            if isinstance(signal_details, dict):
                signals = list(signal_details.keys())
            return {
                "score": result.score,
                "tier": str(tier_val) if tier_val else None,
                "confidence": result.confidence,
                "signals": signals,
                "signal_details": signal_details,
                "agentic_score": result.agentic_score,
            }
        except ImportError:
            log.warning("Scorer module not available, using conservative default")
            return self._default_classify()

    @staticmethod
    def _default_classify() -> dict[str, Any]:
        """Conservative fallback when the scorer is unavailable."""
        return {
            "score": 0.0,
            "tier": _DEFAULT_TIER,
            "confidence": 0.35,
            "signals": ["scorer_unavailable"],
            "agentic_score": 0.0,
        }

    @staticmethod
    def _fallback_tier_name(profile_cfg: Any, tier: str) -> str | None:
        """Try adjacent tiers and return fallback tier name."""
        try:
            idx = _TIER_ORDER.index(tier)
        except ValueError:
            idx = 1  # MEDIUM

        # Search downward first, then upward
        for offset in range(1, len(_TIER_ORDER)):
            for direction in (-1, 1):
                candidate_idx = idx + direction * offset
                if 0 <= candidate_idx < len(_TIER_ORDER):
                    candidate = _TIER_ORDER[candidate_idx]
                    if candidate in profile_cfg.tiers:
                        return candidate
        return None
