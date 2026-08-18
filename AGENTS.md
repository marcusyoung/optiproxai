# AGENTS.md

This file is for coding agents working in `optiproxai`.

## Project Snapshot

- Language: Python 3.13+
- Package manager and task runner: `uv`
- Build backend: `uv_build`
- CLI entrypoints: `optiproxai = "optiproxai.cli:main"` and `opx = "optiproxai.cli:main"`
- App shape: Click CLI + FastAPI proxy + Pydantic config/models
- Source tree: `src/optiproxai/`
- Tests: `tests/` with `pytest`
- Type checking: `pyright`
- Lint/format: `ruff`

## Repository Layout

- `src/optiproxai/cli.py` - Click commands for `serve`, `route`, `config`, `doctor`, `init`, and `keys`
- `src/optiproxai/proxy.py` - FastAPI OpenAI-compatible proxy
- `src/optiproxai/router.py` - routing decisions, tier/profile/provider/model selection, tier override
- `src/optiproxai/scorer.py` - distilled feature classification and tier scoring
- `src/optiproxai/classification_context.py` - request-to-classification context helpers
- `src/optiproxai/config.py` - YAML config loading, env-var resolution, validation
- `src/optiproxai/api_keys.py` - proxy API key storage and validation
- `src/optiproxai/fallback_backoff.py` - process-local fallback cooldowns
- `src/optiproxai/logger.py` - JSONL routing log writer
- `src/optiproxai/dirs.py` - XDG-compliant directory paths
- `src/optiproxai/dashboard.py` - dashboard ingestion, stats, and HTML rendering
- `src/optiproxai/tokens.py` - token estimation (tiktoken with char/4 fallback)
- `src/optiproxai/training_data.py` - distilled feature dataset data structures/helpers
- `src/optiproxai/feature_training.py` - multi-output feature classifier training
- `src/optiproxai/agentic_training.py` - deprecated wrappers for feature classifier training
- `tests/test_scorer.py` - distilled feature scorer and ambiguous band coverage
- `tests/test_llm_classifier.py` - routing logger coverage with distilled feature payloads
- `tests/test_capability_routing.py` - capability detection, filtering, and escalation
- `tests/test_input_limit_routing.py` - input-limit filtering and tier escalation
- `tests/test_tier_override.py` - per-turn tier override (parser, router, proxy, CLI)
- `tests/test_fallback_backoff.py` - fallback cooldown state
- `tests/test_dashboard.py` - dashboard ingestion, stats, and HTML rendering
- `tests/test_proxy_reload.py` - config hot reload, routing errors, decorative tool schema
- `tests/test_api_keys.py` - proxy API key lifecycle
- `tests/test_api_keys_cli.py` - `keys` CLI commands
- `tests/test_api_keys_proxy.py` - proxy auth and fallback behavior
- `tests/test_router_logging.py` - routing log and session-sticky routing
- `tests/test_config.py` - embedding config validation
- `tests/test_cli.py` - CLI route masking, config errors, doctor, init, aux LLM config
- `tests/test_feature_training.py` - feature classifier training pipeline
- `tests/test_agentic_training_data.py` - dataset extraction and LLM annotation
- `tests/test_agentic_training_script.py` - training script entry point
- `config.example.yaml` - example configuration with all features documented

## Setup Commands

- Install runtime + dev dependencies: `uv sync --dev`
- Install runtime dependencies only: `uv sync`
- Run the CLI locally: `uv run optiproxai --help`
- Start the proxy locally: `uv run optiproxai serve`
- Route a prompt locally: `uv run optiproxai route "hello world"`
- Show resolved config: `uv run optiproxai config`

## Build, Lint, Format, Typecheck, Test

- Build package artifacts: `uv build`
- Lint source: `uv run ruff check src/`
- Check formatting: `uv run ruff format --check src/ tests/`
- Auto-format source and tests: `uv run ruff format src/ tests/`
- Type-check source: `uv run pyright src/`
- Run full test suite: `uv run pytest tests/ -q`

## Single-Test Commands

- Run one test file: `uv run pytest tests/test_scorer.py -q`
- Run one test class: `uv run pytest tests/test_scorer.py::TestAmbiguousBands -q`
- Run one test method: `uv run pytest tests/test_scorer.py::TestAmbiguousBands::test_prefer_upper_fails_toward_higher_tier -q`
- Run tests matching an expression: `uv run pytest tests/ -q -k reasoning`
- Stop after first failure: `uv run pytest tests/ -q -x`

## CI Expectations

The GitHub Actions workflow in `.github/workflows/ci.yml` effectively defines the acceptance bar:

- `uv sync --dev`
- `uv run ruff check src/`
- `uv run ruff format --check src/ tests/`
- `uv run pyright src/`
- `uv run pytest tests/ -q`
- `uv build`

If you change Python code, aim to run the relevant subset first, then the full suite if the change is broad.

## Rules Files

- No `.cursor/rules/` directory was found.
- No `.cursorrules` file was found.
- No `.github/copilot-instructions.md` file was found.

If any of those files are added later, treat them as higher-priority repository instructions and update this file.

## Coding Style

The codebase follows a straightforward typed Python style with light structure and minimal abstraction.

### Imports

- Use `from __future__ import annotations` in Python modules.
- Group imports as: standard library, third-party, local package imports.
- Prefer explicit imports over wildcard imports.
- Keep local imports inside functions only when avoiding import cycles or heavy startup cost.
- Use `TYPE_CHECKING` for type-only imports when helpful, as in `src/optiproxai/logger.py`.

### Formatting

- Follow Ruff formatting; do not hand-format against the formatter.
- Use 4-space indentation.
- Keep line length formatter-friendly; long calls are wrapped vertically.
- Preserve the existing style of section dividers made from comment banners when editing long modules.
- Prefer concise docstrings on modules, classes, and non-obvious functions.

### Types

- Add type hints for public functions, methods, and important locals when clarity helps.
- Use modern Python unions like `str | None`, not `Optional[str]`.
- Prefer built-in generics like `list[str]`, `dict[str, Any]`, and `tuple[str, str]`.
- Use Pydantic `BaseModel` for structured config and API-facing data.
- Use dataclasses or enums only where they fit existing patterns; do not introduce new frameworks casually.
- Keep `Any` contained to boundaries like request payloads, YAML data, and flexible JSON structures.

### Naming

- Use `snake_case` for functions, methods, variables, and module names.
- Use `PascalCase` for classes and Pydantic models.
- Use `UPPER_SNAKE_CASE` for module-level constants such as `_DEFAULT_TIER` and `_TIER_ORDER`.
- Test classes use `Test...` naming; test methods use `test_...` naming.
- Prefer descriptive names over short abbreviations unless the abbreviation is already established in the file.

### Control Flow and Design

- Keep functions focused and direct; most modules prefer readable procedural logic over deep indirection.
- Match the current architecture: CLI -> config/router -> scorer/proxy helpers.
- Prefer small private helpers for repeated logic instead of clever abstractions.
- Preserve current public behavior and CLI/API shapes unless the task explicitly changes them.
- Avoid introducing unnecessary dependencies.

### Error Handling

- Fail loudly for invalid internal configuration with `ValueError` or assertions where the code already does that.
- At HTTP boundaries, return structured OpenAI-style JSON errors rather than raw exceptions.
- Catch narrow exceptions when possible, but match existing patterns at network and file I/O boundaries.
- Log operational failures with the standard `logging` module.
- For optional integrations, degrade gracefully instead of crashing; `router.py` and `logger.py` already follow this pattern.

### Config and Secrets

- Keep secrets in environment variables via `${VAR}` placeholders in YAML; do not hardcode credentials in code.
- Preserve config precedence rules: explicit path, `OPTIPROXAI_CONFIG`, local config, XDG config, then `/etc`.
- When changing config models, update both validation code and docs/examples if needed.

### FastAPI and CLI Conventions

- Keep FastAPI handlers thin; route complex logic into helpers or domain classes.
- Preserve OpenAI-compatible request/response shapes.
- Keep Click commands simple and explicit.
- Prefer JSON-serializable return structures and Pydantic `.model_dump()` where already used.

### Testing Conventions

- Put tests under `tests/`.
- Prefer `pytest` style with plain `assert` statements.
- Group related tests into `Test...` classes.
- Use `unittest.mock.MagicMock` and `patch` for network-bound or external behavior.
- Cover both success paths and graceful fallbacks.
- When adding logic to scoring or routing, add tests for thresholds, edge cases, and fallback behavior.

## Agent Advice

- Read the surrounding module before editing; several files use repeated patterns worth preserving.
- Check whether a change affects CLI behavior, config loading, routing behavior, and tests together.
- If you modify API behavior or config semantics, update `README.md` and possibly `CONTRIBUTING.md`.
- Prefer minimal diffs that fit the current code style.
- Before finishing a meaningful Python change, run lint, format check, typecheck, and the most relevant tests.

## Safe Defaults for Agents

- Assume `uv` is the canonical way to run all project commands.
- Assume `src/optiproxai/` is the authoritative source tree.
- Assume CI compatibility matters more than local convenience.
- Assume user changes elsewhere in the worktree are intentional; do not revert unrelated edits.
