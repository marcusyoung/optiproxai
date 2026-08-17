"""Build distilled feature training datasets from OptiProxAI routing logs."""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path
from typing import Any, Protocol, TypedDict

import httpx

from optiproxai.classification_context import (
    DEFAULT_CLASSIFICATION_INPUT_MAX_CHARS,
    build_classification_input,
)
from optiproxai.config import load_config
from optiproxai.dirs import data_dir, log_dir
from optiproxai.scorer import SEMANTIC_DIMENSIONS

VALID_DIMENSION_LABELS = {"low", "medium", "high"}
ANNOTATION_PROMPT_MAX_CHARS = DEFAULT_CLASSIFICATION_INPUT_MAX_CHARS

SEMANTIC_DIMENSION_CALIBRATION: dict[str, dict[str, str]] = {
    "codePresence": {
        "low": "No code, stack traces, commands, or implementation-specific syntax.",
        "medium": "Mentions code, tools, commands, errors, or files without requiring code-heavy work.",
        "high": "Contains code blocks, tracebacks, concrete implementation details, or asks to write/debug code.",
    },
    "reasoningMarkers": {
        "low": "No explicit request to explain, compare, prove, analyze, or find causes.",
        "medium": "Asks for an explanation, comparison, or analysis with limited depth.",
        "high": "Requires deep reasoning such as proof, root-cause analysis, trade-off analysis, or multi-factor diagnosis.",
    },
    "technicalTerms": {
        "low": "Uses general language with little or no specialised vocabulary.",
        "medium": "Includes a few technical terms or one concrete topic from a technical or professional field.",
        "high": "Dense specialised vocabulary from a technical, scientific, or professional domain.",
    },
    "creativeMarkers": {
        "low": "No request to draft, design, write creative copy, or generate narrative content.",
        "medium": "Asks for a short draft, wording, design idea, or light creative transformation.",
        "high": "Requires substantial creative generation, brand/design direction, story, copy, or stylistic iteration.",
    },
    "simpleIndicators": {
        "low": "Not a short greeting, acknowledgement, yes/no answer, or trivial conversational turn.",
        "medium": "Mostly simple but includes a small concrete request or context.",
        "high": "A very short greeting, acknowledgement, thanks, confirmation, or simple yes/no-style prompt.",
    },
    "multiStepPatterns": {
        "low": "Single-step request with no ordered sequence or dependency between actions.",
        "medium": "Two or more implied steps, a short procedure, or one follow-up dependency.",
        "high": "Explicit multi-stage workflow with ordered steps, verification, iteration, or coordination.",
    },
    "questionComplexity": {
        "low": "No question or a direct factual/simple question.",
        "medium": "One substantive question or a question needing some context synthesis.",
        "high": "Multiple questions, conditional questions, or a broad inquiry requiring structured reasoning.",
    },
    "imperativeVerbs": {
        "low": "No action command or only passive information-seeking wording.",
        "medium": "One clear action verb such as add, update, check, run, summarize, or explain.",
        "high": "Multiple action commands or direct instructions to implement, fix, test, investigate, verify, or to search, gather, retrieve, compare, evaluate, and synthesise across sources.",
    },
    "constraintCount": {
        "low": "No explicit constraints, prohibitions, required formats, or must/never conditions.",
        "medium": "One or two constraints such as required output, scope, or forbidden behavior.",
        "high": "Several strict constraints, acceptance criteria, forbidden actions, compliance requirements, or output-style rules such as citation format, source requirements, or evidence standards.",
    },
    "outputFormat": {
        "low": "No requested structure or format.",
        "medium": "Requests a common format such as JSON, markdown, table, list, or concise bullets, or a structured research output such as a report, summary, or citation list.",
        "high": "Requires an exact schema, machine-readable shape, strict keys, multiple formatting rules, or a precise citation/reporting structure.",
    },
    "referenceComplexity": {
        "low": "No external references, file paths, URLs, logs, or prior artifacts.",
        "medium": "One or two references such as a URL, file path, issue, config, log snippet, or external document.",
        "high": "Several references or requires cross-reading files, URLs, logs, specs, previous context, external documents, or multiple sources.",
    },
    "negationComplexity": {
        "low": "No negation, exception, or forbidden behavior.",
        "medium": "One explicit not/without/never condition or simple exception.",
        "high": "Multiple prohibitions, nuanced exceptions, safety constraints that affect execution, or methodological constraints that affect accuracy or output.",
    },
    "domainSpecificity": {
        "low": "General-purpose request that does not depend on a specialised domain.",
        "medium": "Depends on one recognisable technical, scientific, or professional domain.",
        "high": "Requires deep specialised knowledge of a domain with its own terminology, conventions, and source conventions.",
    },
    "agenticTask": {
        "low": "Only asks for an answer or explanation; no tool use, repository change, or verification expected.",
        "medium": "Asks the assistant to perform a bounded action such as inspect, run, update, produce an artifact, or a bounded research action like search, retrieve, or summarise a single source.",
        "high": "Requires autonomous implementation, debugging, multi-step tool use, verification, or repository modification, or autonomous multi-source investigation with evidence gathering, cross-referencing, and structured synthesis.",
    },
}


def _semantic_dimension_calibration_text() -> str:
    missing = set(SEMANTIC_DIMENSIONS) - set(SEMANTIC_DIMENSION_CALIBRATION)
    extra = set(SEMANTIC_DIMENSION_CALIBRATION) - set(SEMANTIC_DIMENSIONS)
    if missing or extra:
        raise ValueError(
            "Semantic dimension calibration must exactly match SEMANTIC_DIMENSIONS; "
            f"missing={sorted(missing)} extra={sorted(extra)}"
        )

    lines = ["Calibration guidance:"]
    for dim in SEMANTIC_DIMENSIONS:
        labels = SEMANTIC_DIMENSION_CALIBRATION[dim]
        invalid_labels = set(labels) - VALID_DIMENSION_LABELS
        missing_labels = VALID_DIMENSION_LABELS - set(labels)
        if invalid_labels or missing_labels:
            raise ValueError(
                "Semantic dimension calibration labels must be low, medium, high; "
                f"dimension={dim} missing={sorted(missing_labels)} "
                f"invalid={sorted(invalid_labels)}"
            )
        lines.append(f"- {dim}:")
        for label in ("low", "medium", "high"):
            lines.append(f"  - {label}: {labels[label]}")
    return "\n".join(lines)


class FeatureAnnotator(Protocol):
    def annotate(self, prompt: str) -> dict[str, str] | None: ...


class DistilledFeatureExample(TypedDict):
    prompt: str
    tokenCount: int
    codePresence: str
    reasoningMarkers: str
    technicalTerms: str
    creativeMarkers: str
    simpleIndicators: str
    multiStepPatterns: str
    questionComplexity: str
    imperativeVerbs: str
    constraintCount: str
    outputFormat: str
    referenceComplexity: str
    negationComplexity: str
    domainSpecificity: str
    agenticTask: str
    timestamp: str | None
    source: str


class LLMFeatureAnnotator:
    """Offline annotator that labels semantic dimensions with an LLM."""

    _SYSTEM_PROMPT = (
        "You are a prompt classifier for llm model routing distillation. "
        "Return ONLY a JSON object with exactly these keys: "
        f"{', '.join(SEMANTIC_DIMENSIONS)}. "
        "Each value MUST be one of: low, medium, high. "
        "No other text. No markdown. No explanation. "
        "If unsure, use 'medium'.\n\n"
        "HIGH-IMPACT dimensions (prioritize accuracy): "
        "reasoningMarkers, agenticTask, multiStepPatterns, "
        "questionComplexity, constraintCount, technicalTerms.\n\n"
        # Single source of truth: SEMANTIC_DIMENSION_CALIBRATION (above) rendered via
        # _semantic_dimension_calibration_text(). The eager class-attribute evaluation is
        # safe because the function is defined earlier in this module and raises at
        # import time if the calibration dict drifts from SEMANTIC_DIMENSIONS.
        + _semantic_dimension_calibration_text()
    )

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        cfg = None
        resolved = None
        try:
            loaded = load_config()
            cfg = loaded.feature_annotator
            resolved = loaded.feature_annotator_resolved()
        except Exception:
            pass

        self.model = (
            model
            or os.environ.get("OPTIPROXAI_LLM_ANNOTATOR_MODEL")
            or (cfg.model if cfg else None)
            or "google/gemini-2.5-flash-lite"
        )
        self.base_url = (
            base_url
            or os.environ.get("OPTIPROXAI_LLM_ANNOTATOR_BASE_URL")
            or (resolved[0] if resolved else None)
            or "https://openrouter.ai/api/v1"
        ).rstrip("/")
        self.api_key = (
            api_key
            or os.environ.get("OPTIPROXAI_LLM_ANNOTATOR_API_KEY")
            or (resolved[1] if resolved else None)
            or os.environ.get("OPENROUTER_API_KEY", "")
        )

    def annotate(self, prompt: str) -> dict[str, str] | None:
        if not self.api_key:
            raise RuntimeError(
                "OPTIPROXAI_LLM_ANNOTATOR_API_KEY or OPENROUTER_API_KEY is required"
            )

        user_text = prompt[:ANNOTATION_PROMPT_MAX_CHARS]

        max_retries = 5
        base_delay = 2.0
        for attempt in range(max_retries):
            try:
                response = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {
                                "role": "system",
                                "content": self._SYSTEM_PROMPT,
                            },
                            {
                                "role": "user",
                                "content": user_text,
                            },
                        ],
                        "temperature": 0.0,
                        "max_tokens": 1024,
                        "response_format": {"type": "json_object"},
                    },
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()
                break
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    delay = base_delay * (2**attempt) + random.uniform(0, 1)
                    print(
                        f"  [RATE LIMIT] Retrying in {delay:.1f}s... (attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(delay)
                    continue
                print(f"  [DEBUG] API/JSON error for prompt '{prompt[:80]}...': {e}")
                return None
            except (httpx.HTTPError, json.JSONDecodeError) as e:
                print(f"  [DEBUG] API/JSON error for prompt '{prompt[:80]}...': {e}")
                return None

        else:
            return None

        content = (
            data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        )
        if not content:
            print(f"  [DEBUG] Empty response for prompt '{prompt[:80]}...'")
            return None
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as e:
            print(f"  [DEBUG] Invalid JSON for prompt '{prompt[:80]}...': {e}")
            print(f"  [DEBUG] Raw content: {stripped[:200]}...")
            return None
        if not isinstance(parsed, dict):
            print(
                f"  [DEBUG] Non-dict response for prompt '{prompt[:80]}...': {parsed}"
            )
            return None

        expected_keys = set(SEMANTIC_DIMENSIONS)
        parsed_keys = set(parsed)
        if parsed_keys != expected_keys:
            return None

        labels: dict[str, str] = {}
        for dim in SEMANTIC_DIMENSIONS:
            value = str(parsed[dim]).strip().lower()
            if value not in VALID_DIMENSION_LABELS:
                return None
            labels[dim] = value
        return labels


def deterministic_token_count(prompt: str) -> int:
    return max(1, len(prompt.split()))


def load_routing_records(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    records.append(payload)
    return records


def _validate_semantic_labels(labels: dict[str, str]) -> bool:
    for dim in SEMANTIC_DIMENSIONS:
        value = labels.get(dim)
        if value not in VALID_DIMENSION_LABELS:
            return False
    return True


def _extract_semantic_labels_from_record(
    record: dict[str, Any],
) -> dict[str, str] | None:
    signals = record.get("signals")
    if not isinstance(signals, dict):
        return None

    raw_labels = signals.get("semanticLabels")
    if not isinstance(raw_labels, dict):
        return None

    labels: dict[str, str] = {
        key: str(value).strip().lower() for key, value in raw_labels.items()
    }
    if not _validate_semantic_labels(labels):
        return None
    return {dim: labels[dim] for dim in SEMANTIC_DIMENSIONS}


def _classification_prompt_from_record(record: dict[str, Any]) -> str:
    context = record.get("classification_context")
    if isinstance(context, dict):
        context_text = str(context.get("text") or "").strip()
        if context_text:
            return context_text

    messages = record.get("messages")
    if isinstance(messages, list):
        try:
            return build_classification_input(messages).text
        except Exception:
            pass

    return str(record.get("prompt") or record.get("prompt_preview") or "").strip()


def _make_example(
    prompt: str,
    labels: dict[str, str],
    record: dict[str, Any],
    source: str,
) -> DistilledFeatureExample:
    return {
        "prompt": prompt,
        "tokenCount": deterministic_token_count(prompt),
        "codePresence": labels["codePresence"],
        "reasoningMarkers": labels["reasoningMarkers"],
        "technicalTerms": labels["technicalTerms"],
        "creativeMarkers": labels["creativeMarkers"],
        "simpleIndicators": labels["simpleIndicators"],
        "multiStepPatterns": labels["multiStepPatterns"],
        "questionComplexity": labels["questionComplexity"],
        "imperativeVerbs": labels["imperativeVerbs"],
        "constraintCount": labels["constraintCount"],
        "outputFormat": labels["outputFormat"],
        "referenceComplexity": labels["referenceComplexity"],
        "negationComplexity": labels["negationComplexity"],
        "domainSpecificity": labels["domainSpecificity"],
        "agenticTask": labels["agenticTask"],
        "timestamp": str(record.get("timestamp")) if record.get("timestamp") else None,
        "source": source,
    }


def _save_examples(
    latest_by_prompt: dict[str, DistilledFeatureExample],
    output_path: Path,
) -> list[DistilledFeatureExample]:
    examples = sorted(
        latest_by_prompt.values(),
        key=lambda item: ((item["timestamp"] or ""), item["prompt"]),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(examples, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return examples


_CHECKPOINT_INTERVAL = 50


def extract_distilled_feature_examples(
    records: list[dict[str, Any]],
    *,
    annotator: FeatureAnnotator | None = None,
    checkpoint_path: Path | None = None,
    force_annotate: bool = False,
) -> list[DistilledFeatureExample]:
    latest_by_prompt: dict[str, DistilledFeatureExample] = {}

    if checkpoint_path and checkpoint_path.exists():
        try:
            existing = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if isinstance(existing, list):
                for item in existing:
                    prompt = item.get("prompt", "")
                    if prompt:
                        latest_by_prompt[prompt] = item
                print(f"  Resumed {len(latest_by_prompt)} examples from checkpoint")
        except (json.JSONDecodeError, OSError):
            pass

    annotated_since_save = 0
    skipped = 0
    total = len(records)

    for idx, record in enumerate(records, 1):
        prompt = _classification_prompt_from_record(record)
        if not prompt:
            skipped += 1
            print(f"  [{idx}/{total}] skip: empty prompt")
            continue

        labels = (
            None if force_annotate else _extract_semantic_labels_from_record(record)
        )
        source = "log"
        if labels is None and annotator is not None:
            if prompt in latest_by_prompt:
                skipped += 1
                print(f"  [{idx}/{total}] skip: duplicate")
                continue
            print(f"  [{idx}/{total}] annotate: {prompt[:120].replace(chr(10), ' ')}")
            labels = annotator.annotate(prompt)
            source = "annotated"
            annotated_since_save += 1

        if labels is None:
            skipped += 1
            print(f"  [{idx}/{total}] skip: no labels returned")
            continue

        if not _validate_semantic_labels(labels):
            continue

        example = _make_example(prompt, labels, record, source)

        current = latest_by_prompt.get(prompt)
        if current is None or (example["timestamp"] or "") >= (
            current["timestamp"] or ""
        ):
            latest_by_prompt[prompt] = example

        if checkpoint_path and annotated_since_save >= _CHECKPOINT_INTERVAL:
            _save_examples(latest_by_prompt, checkpoint_path)
            print(
                f"  [{idx}/{total}] checkpoint: {len(latest_by_prompt)} examples saved"
            )
            annotated_since_save = 0

    return sorted(
        latest_by_prompt.values(),
        key=lambda item: ((item["timestamp"] or ""), item["prompt"]),
    )


def build_feature_dataset(
    log_paths: list[Path],
    output_path: Path,
    *,
    annotator: FeatureAnnotator | None = None,
    force_annotate: bool = False,
) -> list[DistilledFeatureExample]:
    examples = extract_distilled_feature_examples(
        load_routing_records(log_paths),
        annotator=annotator,
        checkpoint_path=output_path if annotator else None,
        force_annotate=force_annotate,
    )
    _save_examples(
        {e["prompt"]: e for e in examples},
        output_path,
    )
    return examples


def resolve_log_paths(
    paths: list[str], *, log_directory: Path, pattern: str
) -> list[Path]:
    if paths:
        return [Path(path).expanduser() for path in paths]
    return sorted(log_directory.expanduser().glob(pattern))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build distilled semantic feature dataset from routing logs"
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional routing log files. If omitted, scan --log-dir with --glob.",
    )
    parser.add_argument(
        "--log-dir",
        default=str(log_dir()),
        help="Directory containing routing-*.jsonl files",
    )
    parser.add_argument(
        "--glob",
        default="routing-*.jsonl",
        help="Glob used when explicit paths are omitted",
    )
    parser.add_argument(
        "--output",
        default=str(data_dir() / "distilled_feature_dataset.json"),
        help="Output JSON dataset path",
    )
    parser.add_argument(
        "--annotate-missing",
        action="store_true",
        help="Use LLM annotation for records missing semantic labels",
    )
    parser.add_argument(
        "--force-annotate",
        action="store_true",
        help="Ignore pre-existing semanticLabels from routing logs and re-annotate all records with the LLM",
    )
    parser.add_argument("--model", help="LLM model for annotation")
    parser.add_argument("--base-url", help="LLM base URL for annotation")
    parser.add_argument("--api-key", help="LLM API key for annotation")
    args = parser.parse_args(argv)

    log_paths = resolve_log_paths(
        args.paths,
        log_directory=Path(args.log_dir),
        pattern=args.glob,
    )
    if not log_paths:
        parser.error("No routing log files found")

    annotator = None
    if args.annotate_missing or args.force_annotate:
        annotator = LLMFeatureAnnotator(
            model=args.model,
            base_url=args.base_url,
            api_key=args.api_key,
        )

    output_path = Path(args.output).expanduser()
    examples = build_feature_dataset(
        log_paths,
        output_path,
        annotator=annotator,
        force_annotate=args.force_annotate,
    )

    print(f"Loaded {len(log_paths)} log files")
    print(f"Wrote {len(examples)} distilled feature examples to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
